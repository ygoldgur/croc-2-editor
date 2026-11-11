"""Collect the tagged objects' transforms and write them into the level WAD."""
from __future__ import annotations

from pathlib import Path

import bpy

from croc2.editing import Edits, Result, apply

from . import convert
from .build import INDEX, KIND, LEVEL


def collect(scene: bpy.types.Scene) -> Edits:
    edits = Edits()
    for obj in scene.objects:
        kind = obj.get(KIND)
        if kind is None or obj.get(LEVEL) != scene["croc2_level"]:
            continue
        position = convert.to_game(*obj.location)
        if kind == "tile":
            edits.tiles[obj[INDEX]] = (position, convert.tile_rotation(obj.rotation_euler))
        elif kind == "instance":
            if obj.rotation_mode != convert.ACTOR_ROTATION_MODE:
                raise ValueError(f"{obj.name}: rotation mode changed to {obj.rotation_mode}")
            edits.instances[obj[INDEX]] = (position, convert.actor_rotation(obj.rotation_euler))
    return edits


def export(scene: bpy.types.Scene, target: Path) -> Result:
    source = Path(scene["croc2_source"])
    result = apply(source.read_bytes(), collect(scene))
    target.write_bytes(result.data)
    return result


def snap_tiles(scene: bpy.types.Scene) -> None:
    """Show tiles at the quarter turn they will be saved with."""
    for obj in scene.objects:
        if obj.get(KIND) == "tile" and obj.get(LEVEL) == scene.get(LEVEL):
            obj.rotation_euler = convert.tile_euler(convert.tile_rotation(obj.rotation_euler))
