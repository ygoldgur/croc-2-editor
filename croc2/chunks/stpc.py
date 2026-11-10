"""STPC chunk: object models, animation, and the actor definitions.

Unlike MAP, this chunk is not read field by field. The loader (``FUN_00421f40``)
allocates the whole thing, reads it in one ``fread``, and then walks it patching
internal pointers, so every apparent "allocation" is a bump of a cursor through the
blob itself. Replaying that cursor arithmetic has to land exactly on the end.

::

    uint32 n_models
    n_models * { 0x88-byte header, then that model's arrays }
    uint32 n_animations
    n_animations * { 0x20-byte header, then that record's arrays }
    [0x100] uint32 n_groups, then batched model groups
    uint32 n_actors
    n_actors * { uint32 size_in_words, then that many words }

The record shapes come from the two fixup helpers. ``FUN_00424db0`` walks the 0x88-byte
model headers, whose sub-arrays are 24, 28 and 44 bytes at the same header offsets as
the TRAK terrain meshes - so object meshes are in the terrain mesh format, with eight
more header bytes for one extra array. ``FUN_00424ef0`` walks the 0x20-byte animation
records.

A model with that extra array is articulated: the array gives each part's vertex count,
in vertex order, and the vertices are stored local to their part. An animation frame is
one 4x4 matrix per part, which carries those vertices into model space. ``pose`` applies
one, and ``export_pose`` finds the frame the model was authored in by its face normals.

The actors file is not touched by the loader at all: ``FUN_00421f40`` returns after the
third section. Nothing needs relocating there because MAP's actor instances point into
it by absolute offset instead. Its layout is the one the PS1 build uses for the same
data, where this chunk is called ``DPSX`` - see
``refs/PS1-BRender-Reverse/Documentation/Data formats/Actors.md``. Reading it that way
consumes the chunk exactly on all 75 parseable levels, which is the test that also
validated MAP and TRAK.

An actor's payload holds its behaviour, and is not decoded. Names appear inside it as
plain nul-terminated strings at no fixed offset, padded with 0xcd - MSVC's
uninitialised-memory fill - so ``Actor.names`` scans for them rather than parsing.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

from ..vm import PUSH_STPC, entry_models
from .trak import Model, bbox_from_header, read_model

MODEL_HEADER = 0x88  # vs 0x80 in TRAK: eight more bytes, one more array
VERTEX_SIZE = 0x18
TRIANGLE_SIZE = 0x1C
FACE_SIZE = 0x2C
ANIM_HEADER = 0x20
MATRIX_SIZE = 0x40
Matrix = tuple[float, ...]  # 16 floats, row-major, translation in the last row

GROUPS_FLAG = 0x100  # WFPC bit gating the batched model groups

# Actor payloads are behaviour data with strings embedded in it, so a name is only
# recognisable by shape: printable, at least four characters, nul-terminated.
NAME_RE = re.compile(rb"[A-Za-z][A-Za-z0-9 _\-\.]{3,40}\x00")


class StpcParseError(ValueError):
    """Ran off the end of the chunk, or a record did not fit."""


def _u16(data: bytes, off: int) -> int:
    if off + 2 > len(data):
        raise StpcParseError(f"uint16 past end at {off}")
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    if off + 4 > len(data):
        raise StpcParseError(f"uint32 past end at {off}")
    return struct.unpack_from("<I", data, off)[0]


@dataclass
class ModelRef:
    """Where one object model sits in the chunk, and how big its arrays are.

    The array order is `FUN_00424db0`'s, which stores a pointer to each one back into
    the header as it goes: the extra `uint16` array (+0x84), then vertices (+0x70),
    triangles (+0x74), then the 44-byte faces (+0x7c).
    """
    offset: int          # byte offset of the 0x88-byte header
    vertex_count: int
    triangle_count: int
    face_count: int      # quad_count + extra_count, the 44-byte records
    extra_count: int     # parts: the uint16 array of per-part vertex counts
    extra_at: int = 0
    vertices_at: int = 0
    triangles_at: int = 0
    faces_at: int = 0


@dataclass
class AnimationRef:
    """One animation record header plus where its variable body sits.

    Each frame holds one 4x4 float matrix per part. ``soft_count`` vertices sit outside
    the rigid parts and carry their own per-frame data ahead of each frame's matrices;
    that data is skipped, not decoded. ``n0`` and ``gate_0c`` gate leading tables of
    unknown meaning.
    """
    offset: int
    n0: int
    frame_count: int     # +0x08
    gate_0c: int
    soft_count: int      # +0x10
    part_count: int      # +0x18
    body_at: int
    body_end: int


@dataclass
class Actor:
    """One actor definition: a size-prefixed blob of behaviour data.

    ``offset`` is the offset of the size word, which is what MAP's actor instances
    are relative to; the payload starts four bytes later.
    """
    index: int
    offset: int
    data: bytes

    @property
    def end(self) -> int:
        return self.offset + 4 + len(self.data)

    def contains(self, offset: int) -> bool:
        return self.offset <= offset < self.end

    @property
    def names(self) -> list[str]:
        return [m.group()[:-1].decode("latin1") for m in NAME_RE.finditer(self.data)]

    @property
    def name(self) -> str:
        """The first name in the payload, which is the best label we have."""
        names = self.names
        return names[0] if names else f"actor {self.index}"


@dataclass
class Reference:
    """An actor pushing a model or animation by its header's absolute STPC offset."""
    at: int      # payload offset of the operand word
    kind: str    # "model" or "animation"
    index: int


@dataclass
class StpcChunk:
    models: list[ModelRef] = field(default_factory=list)
    animations: list[AnimationRef] = field(default_factory=list)
    animation_count: int = 0
    group_count: int = 0
    actors: list[Actor] = field(default_factory=list)

    models_end: int = 0
    animations_end: int = 0
    actors_at: int = 0
    end: int = 0
    left: int = 0
    _references: dict[int, list[Reference]] = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, data: bytes, flag_100: bool) -> "StpcChunk":
        out = cls()
        # The main loop calls the fixup helper once per model, so each header is
        # followed by its own arrays rather than the headers being contiguous.
        cursor = 4
        for _ in range(_u32(data, 0)):
            cursor = _walk_models(data, cursor, 1, out.models)
        out.models_end = cursor

        out.animation_count = _u32(data, cursor)
        cursor += 4
        for _ in range(out.animation_count):  # likewise one call per record
            cursor = _walk_anims(data, cursor, 1, out.animations)
        out.animations_end = cursor

        if flag_100:
            out.group_count = _u32(data, cursor)
            cursor += 4
            for _ in range(out.group_count):
                head = cursor
                n = _u32(data, head + 4)
                cursor = head + 16
                if n & 0x1FFFFFFF:
                    cursor += n * 8
                # Only this section passes a batch, so its headers are contiguous.
                cursor = _walk_models(data, cursor, n, out.models)

        out.actors_at = cursor
        cursor = _walk_actors(data, cursor, out.actors)
        out.end = cursor
        out.left = len(data) - cursor
        return out

    def actor_at(self, offset: int) -> Actor | None:
        """The actor whose record contains an offset, as MAP instances give it."""
        for actor in self.actors:
            if actor.contains(offset):
                return actor
        return None

    def references(self, actor: Actor) -> list[Reference]:
        """The models and animations an actor pushes, in code order.

        Actor payloads are bytecode, and ``124 <word>`` pushes an immediate. When that
        word is the offset of a model or animation header, the actor is naming that
        record: every animation in the game and 94% of the models are named this way,
        each actor claiming a consecutive run of them.
        """
        if actor.index not in self._references:
            models = {m.offset: i for i, m in enumerate(self.models)}
            anims = {a.offset: i for i, a in enumerate(self.animations)}
            out = []
            for off in range(0, len(actor.data) - 7, 4):
                op, value = struct.unpack_from("<2I", actor.data, off)
                if op != PUSH_STPC:
                    continue
                if value in models:
                    out.append(Reference(off + 4, "model", models[value]))
                elif value in anims:
                    out.append(Reference(off + 4, "animation", anims[value]))
            self._references[actor.index] = out
        return self._references[actor.index]

    def instance_models(self, offset: int) -> dict[int, float]:
        """The models an instance entering its actor at ``offset`` may set, by chance.

        Found by running the entry code (``vm.entry_models``). Most entries set one
        model outright; the Distressed Gobbo draws a random number and sets models 12,
        13 or 14 a quarter, half and a quarter of the time. Empty when the code reaches
        an opcode that is not decoded, which is two entries in five.
        """
        actor = self.actor_at(offset)
        if actor is None:
            return {}
        models = {m.offset: i for i, m in enumerate(self.models)}
        return entry_models(actor.data, offset - actor.offset - 4, models)

    def instance_model(self, offset: int) -> int | None:
        """The model an instance most likely uses.

        The likeliest of ``instance_models``. When the entry code cannot be followed,
        the first model pushed at or after the entry point, or failing that anywhere in
        the actor - a guess that matches the evaluated model on 2033 of the 2041
        instances whose entry code can be followed.
        """
        chances = self.instance_models(offset)
        if chances:
            return max(chances, key=chances.get)
        actor = self.actor_at(offset)
        if actor is None:
            return None
        entry = offset - actor.offset - 4
        models = [r for r in self.references(actor) if r.kind == "model"]
        after = [r for r in models if r.at >= entry]
        chosen = after or models
        return chosen[0].index if chosen else None

    def instance_animations(self, data: bytes, offset: int) -> list[int]:
        """The animations an instance's actor pushes that fit its model, in code order.

        Which one plays first is decided by the bytecode, which is not decoded, so this
        is the set to choose from rather than the answer.
        """
        actor = self.actor_at(offset)
        model = self.instance_model(offset)
        if actor is None or model is None:
            return []
        fits = set(self.compatible_animations(data, model))
        out = []
        for r in self.references(actor):
            if r.kind == "animation" and r.index in fits and r.index not in out:
                out.append(r.index)
        return out

    def model(self, data: bytes, index: int) -> Model:
        """Decode one object model into the same mesh type TRAK produces.

        The header's first 0x80 bytes are TRAK's, so the bounding box and the array
        counts are read from the same offsets; only the array positions and the
        triangle's texture width differ.
        """
        ref = self.models[index]
        lo, hi = bbox_from_header(data, ref.offset)
        return read_model(data, index, lo, hi,
                          ref.vertices_at, ref.vertex_count,
                          ref.triangles_at, ref.triangle_count,
                          ref.faces_at, ref.face_count,
                          texture_width=2)

    def animation_matrices(self, data: bytes, index: int) -> list[list[Matrix]]:
        return animation_frames(data, self.animations[index])

    def part_counts(self, data: bytes, index: int) -> tuple[int, ...]:
        """The +0x82 array: how many consecutive vertices each part owns, in order."""
        ref = self.models[index]
        return struct.unpack_from(f"<{ref.extra_count}H", data, ref.extra_at)

    def vertex_parts(self, data: bytes, index: int) -> list[int]:
        """The part each vertex belongs to; -1 for the soft vertices after the last part.

        A static model has no part table and comes back as one part holding everything.
        """
        ref = self.models[index]
        if not ref.extra_count:
            return [0] * ref.vertex_count
        parts = [p for p, n in enumerate(self.part_counts(data, index)) for _ in range(n)]
        return parts + [-1] * (ref.vertex_count - len(parts))

    def compatible_animations(self, data: bytes, index: int) -> list[int]:
        """Animations whose frames place exactly this model's parts and soft vertices."""
        ref = self.models[index]
        if not ref.extra_count:
            return []
        counts = self.part_counts(data, index)
        soft = ref.vertex_count - sum(counts)
        return [i for i, a in enumerate(self.animations)
                if a.part_count == len(counts) and a.soft_count == soft]

    def export_pose(self, data: bytes, index: int) -> tuple[float, int, int] | None:
        """The (agreement, animation, frame) whose pose reproduces the stored face normals.

        Vertices are stored local to their part, but face normals are stored in model
        space for the pose the model was exported in. Posing with that frame makes every
        triangle's geometry agree with its stored normal again, which no wrong pose does
        by accident. Soft-vertex models are skipped, since their soft data is not decoded.
        """
        ref = self.models[index]
        if not ref.extra_count or ref.vertex_count != sum(self.part_counts(data, index)):
            return None
        model = self.model(data, index)
        parts = self.vertex_parts(data, index)
        best = None
        for a in self.compatible_animations(data, index):
            for f, frame in enumerate(self.animation_matrices(data, a)):
                ok, total = face_normal_agreement(pose(model.vertices, parts, frame),
                                                  model.triangles, model.face_normals)
                score = ok / total if total else 0.0
                if best is None or score > best[0]:
                    best = (score, a, f)
                if total and ok == total:
                    return best
        return best


def _walk_models(data: bytes, cursor: int, count: int, into: list[ModelRef]) -> int:
    """FUN_00424db0: headers first, then each model's arrays in header order."""
    base = cursor
    cursor += count * MODEL_HEADER
    for i in range(count):
        rec = base + i * MODEL_HEADER
        extra = _u16(data, rec + 0x82)
        verts = _u16(data, rec + 0x6C)
        tris = _u16(data, rec + 0x6E)
        faces = _u16(data, rec + 0x78) + _u16(data, rec + 0x7A)
        ref = ModelRef(rec, verts, tris, faces, extra, extra_at=cursor)
        ref.vertices_at = ref.extra_at + extra * 2
        ref.triangles_at = ref.vertices_at + verts * VERTEX_SIZE
        ref.faces_at = ref.triangles_at + tris * TRIANGLE_SIZE
        cursor = ref.faces_at + faces * FACE_SIZE
        into.append(ref)
        if cursor > len(data):
            raise StpcParseError(f"model {i} ran past the end at {cursor}")
    return cursor


def _walk_anims(data: bytes, cursor: int, count: int,
                into: list[AnimationRef] | None = None) -> int:
    """FUN_00424ef0."""
    base = cursor
    cursor += count * ANIM_HEADER
    for i in range(count):
        rec = base + i * ANIM_HEADER
        anim = AnimationRef(
            offset=rec,
            n0=_u32(data, rec + 0x00),
            frame_count=_u32(data, rec + 0x08),
            gate_0c=_u32(data, rec + 0x0C),
            soft_count=_u32(data, rec + 0x10),
            part_count=_u32(data, rec + 0x18),
            body_at=cursor,
            body_end=0,
        )
        cursor = _frames_at(anim)
        for j in range(anim.frame_count):
            cursor += _soft_size(anim, j) + anim.part_count * MATRIX_SIZE
        anim.body_end = cursor
        if into is not None:
            into.append(anim)
        if cursor > len(data):
            raise StpcParseError(f"anim {i} ran past the end at {cursor}")
    return cursor


def _frames_at(anim: AnimationRef) -> int:
    """Where the first frame starts, past the per-animation tables."""
    cursor = anim.body_at + anim.n0 * 4
    if anim.gate_0c == 0:
        cursor += anim.frame_count * 8
    if anim.soft_count:
        cursor += anim.frame_count * 4
    return cursor + anim.frame_count * 4


def _soft_size(anim: AnimationRef, frame: int) -> int:
    """Soft-vertex bytes ahead of one frame's matrices.

    The first frame stores three int16 coordinates and a pad per vertex; later frames
    store two bytes per vertex, padded to a word.
    """
    n = anim.soft_count
    if not n:
        return 0
    if frame == 0:
        return n * 8
    return n * 2 + 2 if n & 1 else n * 2


def animation_frames(data: bytes, anim: AnimationRef) -> list[list[Matrix]]:
    """Every frame of one animation, as one row-major 4x4 matrix per part."""
    cursor = _frames_at(anim)
    frames = []
    for j in range(anim.frame_count):
        cursor += _soft_size(anim, j)
        frame = []
        for _ in range(anim.part_count):
            frame.append(struct.unpack_from("<16f", data, cursor))
            cursor += MATRIX_SIZE
        frames.append(frame)
    return frames


def pose(vertices, parts: list[int], frame: list[Matrix]) -> list[tuple[float, float, float]]:
    """Carry each part-local vertex into model space by its part's matrix.

    The matrices take row vectors, ``[x y z 1] @ M``, with the translation in the last
    row. Soft vertices (part -1) are returned unmoved.
    """
    out = []
    for (x, y, z), p in zip(vertices, parts):
        if p < 0:
            out.append((x, y, z))
            continue
        m = frame[p]
        out.append((x * m[0] + y * m[4] + z * m[8] + m[12],
                    x * m[1] + y * m[5] + z * m[9] + m[13],
                    x * m[2] + y * m[6] + z * m[10] + m[14]))
    return out


def face_normal_agreement(positions, triangles, face_normals) -> tuple[int, int]:
    """How many stored face normals point the way their triangle's geometry does."""
    ok = total = 0
    for (a, b, c), fn in zip(triangles, face_normals):
        pa, pb, pc = positions[a], positions[b], positions[c]
        u = [pb[k] - pa[k] for k in range(3)]
        v = [pc[k] - pa[k] for k in range(3)]
        g = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0]]
        gl = math.sqrt(sum(t * t for t in g))
        fl = math.sqrt(sum(t * t for t in fn))
        if gl < 1e-9 or fl < 1e-9:
            continue
        total += 1
        ok += sum(g[k] * fn[k] for k in range(3)) / (gl * fl) > 0.99
    return ok, total


def read_animation_file(data: bytes) -> list[AnimationRef]:
    """``defanim.wad``: Croc's own animations, in STPC's animation record format.

    ::

        uint32 bytes after this word
        uint32 n_animations
        uint32 bytes after this word
        n_animations * { 0x20-byte header, then that record's arrays }
    """
    out: list[AnimationRef] = []
    cursor = 12
    for _ in range(_u32(data, 4)):
        cursor = _walk_anims(data, cursor, 1, out)
    if cursor != len(data):
        raise StpcParseError(f"animation file leaves {len(data) - cursor:+,} bytes")
    return out


def _walk_actors(data: bytes, cursor: int, into: list[Actor]) -> int:
    """The actors file: a count, then size-prefixed records. Sizes are in words."""
    count = _u32(data, cursor)
    cursor += 4
    for i in range(count):
        size = _u32(data, cursor) * 4
        start = cursor
        cursor += 4 + size
        if cursor > len(data):
            raise StpcParseError(f"actor {i}/{count} ran past the end at {cursor}")
        into.append(Actor(i, start, data[start + 4:cursor]))
    return cursor
