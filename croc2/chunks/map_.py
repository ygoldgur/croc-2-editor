"""MAP chunk: placement of terrain tiles and actors in the world grid.

Confirmed layout so far::

    uint32 tile_count
    uint32 grid_x, grid_z          # world grid extents, 32 or 64 in shipped levels
    tile_count * 24 bytes:
        float  x, y, z             # world position, on a 1.0 grid at .5 offsets
        uint32 rotation            # 0x000/0x400/0x800/0xC00 -> 0/90/180/270 degrees
        uint32 model_id            # index into the TRAK mesh library
        uint32 flags               # only 0 or 1 observed

After the tile array comes a sequence of further sections, transcribed from the loader
``FUN_00422020`` by :func:`walk_sections`. Two of them are decoded: the environment
zones and the actor instances. The rest is kept as an opaque blob so edits stay
byte-exact.

Locating the later sections needs the vertex count of every TRAK model, because one
section's size depends on them, and the level's WFPC word, because four sections are
optional. :meth:`MapChunk.parse` therefore only decodes the zones unless it is given
both.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

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


ZONE_SIZE = 90  # 92 bytes once the loader scatters it into its struct

BOX_BITS = 0x0C   # either of these means the record is a grid box, not a point
SPAWN_BIT = 0x08  # exactly one record per level carries it
ANGLE_MASK = 0xF00


@dataclass
class Zone:
    """One environment zone from the MAP zone list.

    A record is either a box on the tile grid or a point in the world, and the low
    byte of ``flags`` says which. The two readings share the same three fields, so
    asking for the wrong one gives nonsense; use ``is_box`` first.
    """

    raw: bytearray

    # Memory offset -> (file offset, width). The 90 file bytes scatter into a 92-byte
    # struct because of the uint16 at +0x38, so the two stop agreeing after it. Fields
    # are addressed by memory offset here, to match the game's own code.
    FIELDS = {
        0x00: (0, 4), 0x04: (4, 4), 0x08: (8, 4), 0x0C: (12, 4),
        0x10: (16, 4), 0x14: (20, 4), 0x18: (24, 4), 0x1C: (28, 4),
        0x20: (32, 4), 0x24: (36, 4), 0x28: (40, 4), 0x2C: (44, 4),
        0x30: (48, 4), 0x34: (52, 4), 0x38: (56, 2), 0x3C: (58, 4),
        0x40: (62, 4), 0x44: (66, 4), 0x48: (70, 4), 0x4C: (74, 4),
        0x50: (78, 4), 0x54: (82, 2), 0x56: (84, 2), 0x58: (86, 4),
    }

    def word(self, mem_off: int) -> int:
        off, width = self.FIELDS[mem_off]
        return struct.unpack_from("<H" if width == 2 else "<I", self.raw, off)[0]

    def set_word(self, mem_off: int, value: int) -> None:
        off, width = self.FIELDS[mem_off]
        struct.pack_into("<H" if width == 2 else "<I", self.raw, off, value)

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
    def is_default(self) -> bool:
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
        return self.word(0x00), self.word(0x04), self.word(0x08)

    @property
    def size(self) -> tuple[int, int, int]:
        """Box extent in grid cells. Only meaningful for a box."""
        return self.word(0x0C), self.word(0x10), self.word(0x14)

    # Environment settings, applied by FUN_00423ce0 whenever this zone becomes
    # the active one. The names come from what that function does with them.

    @property
    def fog_near(self) -> float:
        return self.word(0x3C) / 4096.0

    @property
    def fog_far(self) -> float:
        return self.word(0x40) / 4096.0

    @property
    def fog_colour(self) -> tuple[int, int, int]:
        v = self.word(0x4C)
        return (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF

    @fog_colour.setter
    def fog_colour(self, rgb: tuple[int, int, int]) -> None:
        r, g, b = rgb
        self.set_word(0x4C, (r << 16) | (g << 8) | b)

    @property
    def music_track(self) -> int:
        """Track id, or 0 for none."""
        return self.word(0x58)

    @property
    def reverb(self) -> tuple[int, int]:
        """(type 0-15, level 0-127), as split by ADS_SetReverb."""
        v = self.word(0x56)
        return v & 0xF, (v >> 8) & 0x7F

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


INSTANCE_SIZE = 58  # 64 bytes once the loader scatters it into its struct
NULL_LINK = 0xFFFFFFFF


@dataclass
class ActorInstance:
    """One placed actor: where it stands, and which actor definition it runs.

    The loader reads these into the array at ``level+0x2a0`` one field at a time, so
    every width below comes from the code rather than from guessing. Two fields are
    relocated as it goes, which is what identifies them: ``+0x18`` is added to the
    STPC blob's base, and ``+0x34`` indexes the 40-byte section that follows the
    zones, checked against its count with an "Erruuughh" bailout if it overruns.

    The PS1 build stores the same record - 0x40 bytes with the actor offset at +0x18 -
    in its own level file, which is the second source for the reading.
    """

    index: int
    raw: bytearray

    # Memory offset -> (file offset, width). The three leading uint16s are widened to
    # uint32 in memory, so the two stop agreeing immediately; fields are addressed by
    # memory offset here, to match the game's own code.
    FIELDS = {
        0x00: (0, 2), 0x04: (2, 2), 0x08: (4, 2),
        0x0C: (6, 4), 0x10: (10, 4), 0x14: (14, 4), 0x18: (18, 4), 0x1C: (22, 4),
        0x20: (26, 4), 0x24: (30, 4), 0x28: (34, 4), 0x2C: (38, 4), 0x30: (42, 4),
        0x34: (46, 4), 0x38: (50, 4), 0x3C: (54, 4),
    }

    def word(self, mem_off: int) -> int:
        off, width = self.FIELDS[mem_off]
        return struct.unpack_from("<H" if width == 2 else "<I", self.raw, off)[0]

    def set_word(self, mem_off: int, value: int) -> None:
        off, width = self.FIELDS[mem_off]
        struct.pack_into("<H" if width == 2 else "<I", self.raw, off,
                         value & (0xFFFF if width == 2 else 0xFFFFFFFF))

    @property
    def rotation(self) -> tuple[int, int, int]:
        """Orientation in the same 12-bit binary angle the tiles use.

        Unlike tiles, actors are not confined to quarter turns, so the whole 0..0x1000
        range appears here.
        """
        return self.word(0x00), self.word(0x04), self.word(0x08)

    @property
    def degrees(self) -> tuple[float, float, float]:
        return tuple(r / 0x1000 * 360.0 for r in self.rotation)

    @property
    def position(self) -> tuple[float, float, float]:
        """World position, in the same frame as the tiles.

        Stored as 4.12 fixed point with z negated, exactly like a point zone - tile z
        is negative throughout.
        """
        return (self.word(0x0C) / 4096.0, self.word(0x10) / 4096.0,
                -self.word(0x14) / 4096.0)

    @position.setter
    def position(self, xyz: tuple[float, float, float]) -> None:
        x, y, z = xyz
        for mem_off, value in ((0x0C, x), (0x10, y), (0x14, -z)):
            self.set_word(mem_off, int(round(value * 4096)))

    @property
    def actor_offset(self) -> int:
        """Byte offset into the level's STPC blob, landing inside an actor record."""
        return self.word(0x18)

    @property
    def data_index(self) -> int:
        """Index into the 40-byte section after the zones, or -1 for none."""
        value = self.word(0x34)
        return -1 if value == NULL_LINK else value

    @property
    def link_index(self) -> int:
        """Index into MAP's small leading word array, or -1 for none."""
        value = self.word(0x20)
        return -1 if value == NULL_LINK else value


class MapWalkError(Exception):
    """Ran off the end of the chunk while walking its sections."""


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise MapWalkError(f"wanted {n} at {self.pos}, only {len(self.data)} bytes")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def skip(self, n: int) -> None:
        self.take(n)


# Bytes consumed per record, counted off the loader's reads.
SECTION_C = 30         # 40-byte struct; actor instances index into it
SECTION_D = 20         # 24-byte struct; last field comes from memory, not the file


def walk_sections(data: bytes, vertex_counts: list[int], flag_10000: bool,
                  flag_200000: bool, flag_10: bool, flag_4000000: bool) -> dict:
    """Walk the whole chunk the way FUN_00422020 reads it.

    Returns where each section starts and how many records it holds, plus ``left``,
    the number of bytes still unaccounted for. ``left == 0`` is the test that makes
    this layout trustworthy rather than plausible; it holds on all 75 parseable levels.

    The four flags are bits of the level's WFPC word - see :func:`flags_from_wfpc`.
    """
    r = _Reader(data)
    out: dict = {}

    tile_count = r.u32()
    grid_x = r.u32()
    grid_z = r.u32()
    out.update(tile_count=tile_count, grid_x=grid_x, grid_z=grid_z)
    if not (0 < tile_count < 100000 and 0 < grid_x < 1024 and 0 < grid_z < 1024):
        raise MapWalkError("implausible header")

    tiles = []
    for _ in range(tile_count):
        x, y, z, rot, model, flags = struct.unpack("<3f3I", r.take(24))
        tiles.append((x, y, z, rot, model, flags))
    out["tiles"] = tiles

    out["n_a"] = n = r.u32()          # small leading array
    r.skip(n * 4)

    out["n_zones"] = n = r.u32()      # environment zones
    out["zones_at"] = r.pos
    r.skip(n * ZONE_SIZE)

    out["n_c"] = n = r.u32()          # per-instance data, indexed by instance +0x34
    out["c_at"] = r.pos
    r.skip(n * SECTION_C)

    r.skip(256)                       # fixed block of 32 uint32 pairs
    r.skip(grid_x * grid_z * 4)       # one word per grid cell
    r.skip(tile_count * 24)
    per_tile = [r.u32() for _ in range(tile_count)]   # model index per tile

    # Section D pairs, in order, with the tiles whose flag field is non-zero.
    extra_model: dict[int, int] = {}
    if flag_10000:
        out["n_d"] = n = r.u32()
        flagged = [i for i, t in enumerate(tiles) if t[5] != 0]
        for i in range(n):
            fields = struct.unpack("<5I", r.take(SECTION_D))
            if i < len(flagged):
                extra_model[flagged[i]] = fields[4]

    # Per-tile vertex colours. A row is one word per vertex of the tile's model, and
    # the high byte of the first row gives how many rows there are. Tiles that appear
    # in section D then carry a second set of rows for that section's model, with the
    # layer count carried over rather than restarted.
    for tile, model in enumerate(per_tile):
        if model >= len(vertex_counts):
            raise MapWalkError(f"model {model} out of range")
        vc = vertex_counts[model]
        layers = 0
        if vc:
            row = [r.u32() for _ in range(vc)]
            layers = max(c >> 24 for c in row)
            if layers > 1:
                r.skip((layers - 1) * vc * 4)

        if tile in extra_model:
            second = extra_model[tile]
            if second >= len(vertex_counts):
                raise MapWalkError(f"section D model {second} out of range")
            vc2 = vertex_counts[second]
            if vc2:
                row = [r.u32() for _ in range(vc2)]
                layers = max([layers] + [c >> 24 for c in row])
                if layers > 1:
                    r.skip((layers - 1) * vc2 * 4)

    out["n_e"] = n = r.u32()
    r.skip(n * 48)

    out["n_instances"] = n = r.u32()  # the actor placements
    r.u32()
    out["instances_at"] = r.pos
    r.skip(n * INSTANCE_SIZE)

    if flag_200000:
        groups = r.u32()
        for _ in range(groups):
            count = r.u32()
            for _ in range(count):
                items = r.u16()
                r.u16()
                # 28 bytes per item in memory, but only 12 in the file - or 16 when
                # the 0x4000000 flag adds the field the loader otherwise zeroes.
                r.skip(items * (16 if flag_4000000 else 12))

    if flag_10:
        r.u32()

    r.u16()
    out["end"] = r.pos
    out["left"] = len(data) - r.pos
    return out


def flags_from_wfpc(word: int) -> tuple[bool, bool, bool, bool]:
    """The gates on the optional sections are bits of the level's WFPC word."""
    return (bool(word & 0x10000), bool(word & 0x200000),
            bool(word & 0x10), bool(word & 0x4000000))


@dataclass
class MapChunk:
    grid_x: int
    grid_z: int
    tiles: list[Tile]
    zones: list[Zone]
    rest: bytes = b""  # sections after the tile array, kept verbatim
    zones_at: int = -1  # where the zone list starts within rest
    instances: list[ActorInstance] = field(default_factory=list)

    instances_at: int = -1  # where the actor instances start within rest

    @classmethod
    def parse(cls, data: bytes, vertex_counts: list[int] | None = None,
              wfpc: int = 0) -> "MapChunk":
        """Parse the tile array and the zones, and the actor instances if it can.

        The instances sit deep in the tail, past a section whose size depends on the
        TRAK models' vertex counts, so reaching them needs ``vertex_counts`` and the
        level's ``wfpc`` word. Without them the tail is left opaque.
        """
        count, grid_x, grid_z = HEADER_STRUCT.unpack_from(data, 0)
        end = HEADER_STRUCT.size + count * TILE_STRUCT.size
        tiles = [Tile(*TILE_STRUCT.unpack_from(data, off))
                 for off in range(HEADER_STRUCT.size, end, TILE_STRUCT.size)]
        rest = data[end:]
        zones, at = cls._zones(rest)
        chunk = cls(grid_x, grid_z, tiles, zones, rest, at)
        if vertex_counts is not None:
            chunk._parse_instances(data, end, vertex_counts, wfpc)
        return chunk

    def _parse_instances(self, data: bytes, rest_at: int,
                         vertex_counts: list[int], wfpc: int) -> None:
        try:
            out = walk_sections(data, vertex_counts, *flags_from_wfpc(wfpc))
        except (MapWalkError, struct.error):
            return
        at = out["instances_at"]
        self.instances_at = at - rest_at
        self.instances = [
            ActorInstance(i, bytearray(
                data[at + i * INSTANCE_SIZE:at + (i + 1) * INSTANCE_SIZE]))
            for i in range(out["n_instances"])
        ]

    @staticmethod
    def _zones(rest: bytes) -> tuple[list[Zone], int]:
        """The entity list is the second section after the tiles."""
        try:
            leading = struct.unpack_from("<I", rest, 0)[0]
            at = 4 + leading * 4
            count = struct.unpack_from("<I", rest, at)[0]
            at += 4
            if at + count * ZONE_SIZE > len(rest):
                return [], -1
            return ([Zone(bytearray(rest[at + i * ZONE_SIZE:
                                           at + (i + 1) * ZONE_SIZE]))
                     for i in range(count)], at)
        except struct.error:
            return [], -1

    def to_bytes(self) -> bytes:
        out = HEADER_STRUCT.pack(len(self.tiles), self.grid_x, self.grid_z)
        out += b"".join(t.to_bytes() for t in self.tiles)
        return out + self._rest_bytes()

    def _rest_bytes(self) -> bytes:
        """rest, with any zone and actor instance edits spliced back in."""
        rest = bytearray(self.rest)
        if self.zones_at >= 0:
            for i, zone in enumerate(self.zones):
                at = self.zones_at + i * ZONE_SIZE
                rest[at:at + ZONE_SIZE] = zone.raw
        if self.instances_at >= 0:
            for i, inst in enumerate(self.instances):
                at = self.instances_at + i * INSTANCE_SIZE
                rest[at:at + INSTANCE_SIZE] = inst.raw
        return bytes(rest)
