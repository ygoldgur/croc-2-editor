"""TRAK chunk: the library of reusable terrain-tile meshes that MAP places.

Layout::

    uint32 model_count
    model_count * 128-byte headers, stored contiguously
    per-model data, in the same order, also contiguous

Header (128 bytes)::

    +0x00  float               unknown scalar
    +0x04  float               unknown scalar (bounding radius?)
    +0x08  float               unknown scalar
    +0x0C  8 * 3 floats        axis-aligned bounding box corners, in binary-counting
                               order: x varies fastest, then y, then z
    +0x6C  uint16 vertex_count, uint16 triangle_count
    +0x70  uint32 zero
    +0x74  uint32 zero
    +0x78  uint16 quad_count,  uint16 extra_count
    +0x7C  float               unknown

Data, per model::

    vertex_count   * 24 bytes  position (3 floats) + unit normal (3 floats)
    triangle_count * 28 bytes  the render triangles::

        +0x00  uint16          flags; bit 0x10 marks the second triangle of a quad
        +0x02  3 * uint16      vertex indices
        +0x08  uint32          texture index, into the TEXT chunk's entry table
        +0x0C  3 * float       face normal
        +0x18  float           unknown; not the plane offset

Triangles are emitted in pairs that form a quad: one with bit 0x10 clear followed by
one with it set, sharing an edge and a texture. Quads carry no stored UVs - the four
corners simply take the four corners of the texture rectangle, in winding order.

    (quad_count + extra_count) * 44 bytes  render faces; contents not yet decoded,
                               values look like 4.12 fixed point (0x1000 == 1.0)

The face normal is parallel to the normal computed from the triangle's own vertices
for every triangle in every level, which is what pins that part of the layout down.
Texture indices are dense from zero and always land inside the TEXT entry table;
tinting a level by the colour of the texture they select reproduces its real terrain.

The size formula this implies reproduces the chunk length exactly for 75 of the 77
shipped levels; t0i0m006 and t0l1m002 (demo/attract maps) do not fit and are rejected.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

HEADER_SIZE = 0x80
HEADER_BASE = 4
VERTEX_SIZE = 24
TRIANGLE_SIZE = 28
FACE_SIZE = 44


QUAD_SECOND_HALF = 0x10  # triangle flag: this is the back half of a quad


@dataclass
class Face:
    """A render face: three or four corner indices sharing one texture.

    Quads are the norm; a face is a triangle only where the exporter emitted one
    without a partner. `indices` is in winding order, so corner *i* takes texture
    rectangle corner *i*.
    """
    indices: tuple[int, ...]
    texture_id: int
    flags: int

    @property
    def is_quad(self) -> bool:
        return len(self.indices) == 4

    def triangulate(self):
        if self.is_quad:
            a, b, c, d = self.indices
            return [(a, b, c), (a, c, d)]
        return [self.indices]


@dataclass
class Model:
    index: int
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    vertices: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)
    texture_ids: list[int] = field(default_factory=list)       # parallel to triangles
    face_normals: list[tuple[float, float, float]] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)  # triangles merged back into quads
    faces_raw: bytes = b""      # the 44-byte records, still opaque
    triangles_raw: bytes = b""  # full 28-byte records, for lossless rebuild


class TrakParseError(ValueError):
    pass


def _merge_quads(records, vertex_count):
    """Pair each bit-0x10-clear triangle with the bit-0x10-set one that follows it.

    The pair has to share an edge and a texture to count; anything that does not is
    kept as a standalone triangle. Across the shipped levels this merges cleanly for
    94% of faces, and all but six of the rest are lone triangles rather than misfits.
    """
    faces = []
    i = 0
    while i < len(records):
        flags, idx, texture = records[i]
        partner = records[i + 1] if i + 1 < len(records) else None
        if (not flags & QUAD_SECOND_HALF and partner
                and partner[0] & QUAD_SECOND_HALF
                and partner[2] == texture
                and len(set(idx) & set(partner[1])) == 2):
            extra = [v for v in partner[1] if v not in idx]
            if len(extra) == 1 and max(max(idx), extra[0]) < vertex_count:
                faces.append(Face((*idx, extra[0]), texture, flags))
            i += 2
            continue
        if max(idx) < vertex_count:
            faces.append(Face(idx, texture, flags))
        i += 1
    return faces


@dataclass
class TrakChunk:
    models: list[Model]

    @classmethod
    def parse(cls, data: bytes) -> "TrakChunk":
        count = struct.unpack_from("<I", data, 0)[0]
        data_start = HEADER_BASE + count * HEADER_SIZE
        if count == 0 or data_start > len(data):
            raise TrakParseError(f"implausible model count {count}")

        specs = []
        total = 0
        for i in range(count):
            o = HEADER_BASE + i * HEADER_SIZE
            corners = struct.unpack_from("<24f", data, o + 0x0C)
            xs, ys, zs = corners[0::3], corners[1::3], corners[2::3]
            vc, tc = struct.unpack_from("<2H", data, o + 0x6C)
            qc, ec = struct.unpack_from("<2H", data, o + 0x78)
            specs.append((i, (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)),
                          vc, tc, qc + ec))
            total += VERTEX_SIZE * vc + TRIANGLE_SIZE * tc + FACE_SIZE * (qc + ec)

        if total != len(data) - data_start:
            raise TrakParseError(
                f"model data is {len(data) - data_start} bytes but headers describe {total}")

        models = []
        pos = data_start
        for i, lo, hi, vc, tc, fc in specs:
            m = Model(i, lo, hi)
            for v in range(vc):
                px, py, pz, nx, ny, nz = struct.unpack_from("<6f", data, pos + v * VERTEX_SIZE)
                m.vertices.append((px, py, pz))
                m.normals.append((nx, ny, nz))
            pos += vc * VERTEX_SIZE

            m.triangles_raw = data[pos:pos + tc * TRIANGLE_SIZE]
            records = []
            for t in range(tc):
                base = pos + t * TRIANGLE_SIZE
                flags, = struct.unpack_from("<H", data, base)
                a, b, c = struct.unpack_from("<3H", data, base + 2)
                texture, = struct.unpack_from("<I", data, base + 8)
                records.append((flags, (a, b, c), texture))
                if max(a, b, c) < vc:
                    m.triangles.append((a, b, c))
                    m.texture_ids.append(texture)
                    m.face_normals.append(struct.unpack_from("<3f", data, base + 12))
            m.faces = _merge_quads(records, vc)
            pos += tc * TRIANGLE_SIZE

            m.faces_raw = data[pos:pos + fc * FACE_SIZE]
            pos += fc * FACE_SIZE
            models.append(m)
        return cls(models)
