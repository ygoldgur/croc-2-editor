"""Apply placement edits to a level WAD, and check the result before anything is written.

Only edits already shown to reach the game are allowed: moving and turning terrain
tiles and actor instances. Both rewrite fields in place, so the file keeps its size and
every chunk but ``MAP`` stays byte-identical, which ``apply`` checks.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .chunks.map_ import FIXED, ROT_STEP
from .level import Level
from .wad import Wad

Vec3 = tuple[float, float, float]


class EditError(ValueError):
    """An edit the format cannot hold, or a result that does not read back."""


@dataclass
class Edits:
    """New absolute values, keyed by tile index and instance index."""
    tiles: dict[int, tuple[Vec3, int]] = field(default_factory=dict)
    instances: dict[int, tuple[Vec3, tuple[int, int, int]]] = field(default_factory=dict)


@dataclass
class Result:
    data: bytes
    tiles_moved: list[int]
    instances_moved: list[int]
    bytes_changed: int


def apply(original: bytes, edits: Edits) -> Result:
    """Return the WAD with ``edits`` applied, after reading it back to check it."""
    wad = Wad.from_bytes(original)
    level = Level.from_wad(wad)
    mp = level.map
    if mp.instances_at < 0:
        raise EditError("the MAP walk failed on this level, so instances cannot be found")

    tiles_moved = []
    for index, (xyz, rotation) in sorted(edits.tiles.items()):
        tile = mp.tiles[index]
        if rotation % 0x1000 % ROT_STEP:
            raise EditError(f"tile {index}: rotation {rotation:#x} is not a quarter turn")
        x, y, z = _float32(xyz)
        new = (x, y, z, rotation % 0x1000)
        if new != (tile.x, tile.y, tile.z, tile.rotation):
            tile.x, tile.y, tile.z, tile.rotation = new
            tiles_moved.append(index)

    instances_moved = []
    for index, (xyz, rotation) in sorted(edits.instances.items()):
        inst = mp.instances[index]
        before = bytes(inst.raw)
        inst.position = xyz
        inst.rotation = rotation
        if bytes(inst.raw) != before:
            instances_moved.append(index)

    wad.get("MAP").payload = mp.to_bytes()
    data = wad.to_bytes()
    _check(original, data, level, edits)
    changed = sum(a != b for a, b in zip(original, data))
    return Result(data, tiles_moved, instances_moved, changed)


def _float32(xyz: Vec3) -> Vec3:
    return struct.unpack("<3f", struct.pack("<3f", *xyz))


def _check(original: bytes, data: bytes, before: Level, edits: Edits) -> None:
    if len(data) != len(original):
        raise EditError(f"size changed {len(original)} -> {len(data)}")
    old, new = Wad.from_bytes(original), Wad.from_bytes(data)
    for a, b in zip(old.chunks, new.chunks):
        if a.id != b.id:
            raise EditError(f"chunk order changed at {a.name}")
        if a.id != b"MAP " and a.payload != b.payload:
            raise EditError(f"chunk {a.name.strip()} changed")

    after = Level.from_wad(new)
    mp = after.map
    if len(mp.tiles) != len(before.map.tiles) or len(mp.instances) != len(before.map.instances):
        raise EditError("tile or instance count changed on read-back")
    if mp.tile_copy_at < 0 and before.map.tile_copy_at >= 0:
        raise EditError("the fixed-point tile copy no longer matches the tiles")
    for index, (xyz, rotation) in edits.tiles.items():
        tile = mp.tiles[index]
        if (tile.x, tile.y, tile.z) != _float32(xyz) or tile.rotation != rotation % 0x1000:
            raise EditError(f"tile {index} reads back as {tile}")
    for index, (xyz, rotation) in edits.instances.items():
        inst = mp.instances[index]
        if any(abs(a - b) > 0.5 / FIXED for a, b in zip(inst.position, xyz)):
            raise EditError(f"instance {index} reads back at {inst.position}")
        if inst.rotation != tuple(r % 0x1000 for r in rotation):
            raise EditError(f"instance {index} reads back turned {inst.rotation}")
        if inst.actor_offset != before.map.instances[index].actor_offset:
            raise EditError(f"instance {index} lost its actor")
