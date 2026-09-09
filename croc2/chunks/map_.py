"""MAP chunk: placement of terrain tiles in the world grid.

Confirmed layout so far::

    uint32 tile_count
    uint32 grid_x, grid_z          # world grid extents, 32 or 64 in shipped levels
    tile_count * 24 bytes:
        float  x, y, z             # world position, on a 1.0 grid at .5 offsets
        uint32 rotation            # 0x000/0x400/0x800/0xC00 -> 0/90/180/270 degrees
        uint32 model_id            # index into the TRAK mesh library
        uint32 flags               # only 0 or 1 observed

After the tile array comes a sequence of further sections, walked in full by
``tools/mapwalk.py``. The second of them is the entity list, decoded here; the rest is
kept as an opaque blob so edits stay byte-exact.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

TILE_STRUCT = struct.Struct("<3f3I")
HEADER_STRUCT = struct.Struct("<3I")

ROT_STEP = 0x400  # a quarter turn


@dataclass
class Tile:
    x: float
    y: float
    z: float
    rotation: int
    model_id: int
    flags: int

    @property
    def degrees(self) -> float:
        return self.rotation / 0x1000 * 360.0

    def to_bytes(self) -> bytes:
        return TILE_STRUCT.pack(self.x, self.y, self.z,
                                self.rotation, self.model_id, self.flags)


ENTITY_SIZE = 90  # 92 bytes once the loader scatters it into its struct

BOX_BITS = 0x0C   # either of these means the record is a grid box, not a point
SPAWN_BIT = 0x08  # exactly one record per level carries it
ANGLE_MASK = 0xF00


@dataclass
class Entity:
    """One object placement from the MAP entity list.

    A record is either a box on the tile grid or a point in the world, and the low
    byte of ``flags`` says which. The two readings share the same three fields, so
    asking for the wrong one gives nonsense; use ``is_box`` first.
    """

    raw: bytearray

    def word(self, off: int) -> int:
        return struct.unpack_from("<I", self.raw, off)[0]

    def set_word(self, off: int, value: int) -> None:
        struct.pack_into("<I", self.raw, off, value)

    def set_cell(self, x: int, y: int, z: int) -> None:
        for off, value in zip((0, 4, 8), (x, y, z)):
            self.set_word(off, value)

    @property
    def flags(self) -> int:
        return self.word(32)

    @property
    def is_box(self) -> bool:
        return bool(self.flags & BOX_BITS)

    @property
    def is_spawn(self) -> bool:
        return bool(self.flags & SPAWN_BIT)

    @property
    def rotation(self) -> int:
        return self.flags & ANGLE_MASK

    @property
    def degrees(self) -> float:
        return self.rotation / 0x1000 * 360.0

    @property
    def stpc_offset(self) -> int:
        """Byte offset into the level's STPC blob, or 0 for none."""
        return self.word(40)

    @property
    def cell(self) -> tuple[int, int, int]:
        """Grid origin. Only meaningful for a box."""
        return self.word(0), self.word(4), self.word(8)

    @property
    def size(self) -> tuple[int, int, int]:
        """Box extent in grid cells. Only meaningful for a box."""
        return self.word(12), self.word(16), self.word(20)

    @property
    def position(self) -> tuple[float, float, float]:
        """World position of a point entity, in the same frame as tiles.

        Stored as 4.12 fixed point with z negated - tile z is negative throughout.
        """
        return (self.word(12) / 4096.0, self.word(16) / 4096.0,
                -self.word(20) / 4096.0)

    def world_position(self, grid_z: int) -> tuple[float, float, float]:
        """World position for either kind, so callers can place them uniformly."""
        if not self.is_box:
            return self.position
        x, y, z = self.cell
        sx, sy, sz = self.size
        return (x + sx / 2.0, float(y), -(z + sz / 2.0))


@dataclass
class MapChunk:
    grid_x: int
    grid_z: int
    tiles: list[Tile]
    entities: list[Entity]
    rest: bytes = b""  # sections after the tile array, kept verbatim
    entities_at: int = -1  # where the entity list starts within rest

    @classmethod
    def parse(cls, data: bytes) -> "MapChunk":
        count, grid_x, grid_z = HEADER_STRUCT.unpack_from(data, 0)
        end = HEADER_STRUCT.size + count * TILE_STRUCT.size
        tiles = [Tile(*TILE_STRUCT.unpack_from(data, off))
                 for off in range(HEADER_STRUCT.size, end, TILE_STRUCT.size)]
        rest = data[end:]
        entities, at = cls._entities(rest)
        return cls(grid_x, grid_z, tiles, entities, rest, at)

    @staticmethod
    def _entities(rest: bytes) -> tuple[list[Entity], int]:
        """The entity list is the second section after the tiles."""
        try:
            leading = struct.unpack_from("<I", rest, 0)[0]
            at = 4 + leading * 4
            count = struct.unpack_from("<I", rest, at)[0]
            at += 4
            if at + count * ENTITY_SIZE > len(rest):
                return [], -1
            return ([Entity(bytearray(rest[at + i * ENTITY_SIZE:
                                           at + (i + 1) * ENTITY_SIZE]))
                     for i in range(count)], at)
        except struct.error:
            return [], -1

    def to_bytes(self) -> bytes:
        out = HEADER_STRUCT.pack(len(self.tiles), self.grid_x, self.grid_z)
        out += b"".join(t.to_bytes() for t in self.tiles)
        return out + self._rest_bytes()

    def _rest_bytes(self) -> bytes:
        """rest, with any entity edits spliced back in."""
        if self.entities_at < 0 or not self.entities:
            return self.rest
        rest = bytearray(self.rest)
        for i, ent in enumerate(self.entities):
            at = self.entities_at + i * ENTITY_SIZE
            rest[at:at + ENTITY_SIZE] = ent.raw
        return bytes(rest)
