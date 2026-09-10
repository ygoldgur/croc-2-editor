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

import re
import struct
from dataclasses import dataclass, field

MODEL_HEADER = 0x88  # vs 0x80 in TRAK: eight more bytes, one more array
VERTEX_SIZE = 0x18
TRIANGLE_SIZE = 0x1C
FACE_SIZE = 0x2C
ANIM_HEADER = 0x20

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
    """Where one object model sits in the chunk, and how big its arrays are."""
    offset: int          # byte offset of the 0x88-byte header
    vertex_count: int
    triangle_count: int
    face_count: int      # quad_count + extra_count, the 44-byte records
    extra_count: int     # the uint16 array TRAK has no room for


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
class StpcChunk:
    models: list[ModelRef] = field(default_factory=list)
    animation_count: int = 0
    group_count: int = 0
    actors: list[Actor] = field(default_factory=list)

    models_end: int = 0
    animations_end: int = 0
    actors_at: int = 0
    end: int = 0
    left: int = 0

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
            cursor = _walk_anims(data, cursor, 1)
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
        cursor += extra * 2 + verts * VERTEX_SIZE + tris * TRIANGLE_SIZE + faces * FACE_SIZE
        into.append(ModelRef(rec, verts, tris, faces, extra))
        if cursor > len(data):
            raise StpcParseError(f"model {i} ran past the end at {cursor}")
    return cursor


def _walk_anims(data: bytes, cursor: int, count: int) -> int:
    """FUN_00424ef0."""
    base = cursor
    cursor += count * ANIM_HEADER
    for i in range(count):
        rec = base + i * ANIM_HEADER
        n0 = _u32(data, rec + 0x00)
        n2 = _u32(data, rec + 0x08)
        gate_0c = _u32(data, rec + 0x0C)
        gate_10 = _u32(data, rec + 0x10)
        n3 = _u32(data, rec + 0x18)

        cursor += n0 * 4
        if gate_0c == 0:
            cursor += n2 * 8
        if gate_10 != 0:
            cursor += n2 * 4
        cursor += n2 * 4

        for j in range(n2):
            if gate_10 != 0:
                # the first item is sized differently from the rest
                if j == 0:
                    cursor += gate_10 * 8
                elif gate_10 & 1:
                    cursor += gate_10 * 2 + 2
                else:
                    cursor += gate_10 * 2
            cursor += n3 * 0x40
        if cursor > len(data):
            raise StpcParseError(f"anim {i} ran past the end at {cursor}")
    return cursor


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
