"""Headless test of the editor: import, save untouched, save with moves, read back.

    /Applications/Blender.app/Contents/MacOS/Blender -b --python blender/test_editor.py -- [level]

Reads the pristine copy and writes only to a temporary directory. Exits non-zero on any
failure; with ``--render`` also writes a Workbench view to ``shots/editor_<level>.png``.
"""
import math
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import croc2_editor  # noqa: E402
from croc2_editor import build, convert, export  # noqa: E402
from croc2.level import Level  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
LEVEL = next((a for a in args if not a.startswith("--")), "t1l1m001")
SHOTS = Path(__file__).resolve().parent.parent / "shots"


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        raise SystemExit(1)


def objects(kind):
    return {o[build.INDEX]: o for o in bpy.context.scene.objects if o.get(build.KIND) == kind}


def main():
    croc2_editor.register()
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    source = PRISTINE_WADS / f"{LEVEL}.wad"
    counts = croc2_editor.import_level(bpy.context, source)
    level = Level.load(source)
    check(counts["tiles"] == len(level.map.tiles), f"{counts['tiles']} tiles imported")
    check(counts["instances"] == len(level.map.instances),
          f"{counts['instances']} actors imported, {counts['drawn']} with a mesh")

    tmp = Path(tempfile.mkdtemp())
    scene = bpy.context.scene
    result = export.export(scene, tmp / "same.wad")
    check(result.data == source.read_bytes(), "saving untouched is byte-identical")
    check(not result.tiles_moved and not result.instances_moved, "nothing reported moved")

    tiles, insts = objects("tile"), objects("instance")
    tile = tiles[1]
    tile.location.z += 1.0
    tile.location.x += 2.0
    tile.rotation_euler.z -= math.pi / 2 + 0.1   # a quarter turn plus slop, to be snapped
    inst = insts[8]
    inst.location = (10.0, 18.0, 3.0)
    inst.rotation_euler.z += math.radians(45)
    result = export.export(scene, tmp / "moved.wad")
    check(result.tiles_moved == [1] and result.instances_moved == [8],
          f"moved tile {result.tiles_moved} and actor {result.instances_moved}, "
          f"{result.bytes_changed} bytes")

    after = Level.load(tmp / "moved.wad")
    t0, t1 = level.map.tiles[1], after.map.tiles[1]
    check((t1.x, t1.y, t1.z) == (t0.x + 2.0, t0.y + 1.0, t0.z), f"tile 1 at {t1.x, t1.y, t1.z}")
    check(t1.rotation == (t0.rotation + 0x400) % 0x1000, f"tile 1 turned to {t1.rotation:#x}")
    i0, i1 = level.map.instances[8], after.map.instances[8]
    check(all(abs(a - b) < 1e-3 for a, b in zip(i1.position, (10.0, 3.0, -18.0))),
          f"actor 8 at {i1.position}")
    check(i1.rotation[1] == (i0.rotation[1] + 0x200) % 0x1000,
          f"actor 8 turned {i0.rotation[1]:#x} -> {i1.rotation[1]:#x}")
    check(i1.rotation[0] == i0.rotation[0] and i1.rotation[2] == i0.rotation[2],
          "actor 8's other axes untouched")
    others = [i for i, (a, b) in enumerate(zip(level.map.instances, after.map.instances))
              if a.raw != b.raw and i != 8]
    check(not others, "no other actor changed")
    check(after.map.tile_copy_at >= 0, "fixed-point tile copy still matches")

    if "--render" in args:
        render(level)


def render(level):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "TEXTURE"
    scene.render.resolution_x, scene.render.resolution_y = 1280, 800
    focus = next((int(a.split("=")[1]) for a in args if a.startswith("--focus=")), 7)
    inst = level.map.instances[focus]
    x, y, z = convert.to_blender(*inst.position)
    z += 0.3
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (x, y - 1.6, z + 3.2)
    direction = (x - cam.location[0], y - cam.location[1], z - cam.location[2])
    from mathutils import Vector
    cam.rotation_euler = Vector(direction).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    out = SHOTS / f"editor_{level.name}.png"
    scene.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    print(f"wrote {out}")


try:
    main()
except SystemExit:
    raise
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
