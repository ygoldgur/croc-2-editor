"""MAP chunk: placement of terrain tiles in the world grid.

Confirmed layout so far::

    uint32 tile_count
    uint32 grid_x, grid_z          # world grid extents, 32 or 64 in shipped levels
    tile_count * 24 bytes:
        float  x, y, z             # world position, on a 1.0 grid at .5 offsets
        uint32 rotation            # 0x000/0x400/0x800/0xC00 -> 0/90/180/270 degrees
        uint32 model_id            # index into the TRAK mesh library
        uint32 flags               # only 0 or 1 observed

Everything after the tile array is not yet understood (actor instances live in there)
and is kept as an opaque blob so edits stay byte-exact.
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


@dataclass
class MapChunk:
    grid_x: int
    grid_z: int
    tiles: list[Tile]
    rest: bytes = b""  # actor instances and other not-yet-decoded data

    @classmethod
    def parse(cls, data: bytes) -> "MapChunk":
        count, grid_x, grid_z = HEADER_STRUCT.unpack_from(data, 0)
        end = HEADER_STRUCT.size + count * TILE_STRUCT.size
        tiles = [Tile(*TILE_STRUCT.unpack_from(data, off))
                 for off in range(HEADER_STRUCT.size, end, TILE_STRUCT.size)]
        return cls(grid_x, grid_z, tiles, data[end:])

    def to_bytes(self) -> bytes:
        out = HEADER_STRUCT.pack(len(self.tiles), self.grid_x, self.grid_z)
        out += b"".join(t.to_bytes() for t in self.tiles)
        return out + self.rest
