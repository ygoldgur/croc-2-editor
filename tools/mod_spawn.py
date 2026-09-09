#!/usr/bin/env python3
"""Move a level's spawn record and see where Croc actually appears.

The entity record singled out by flag bit 8 is a spawn only by inference: it is the
one record per level the loader copies into a global of its own. Moving it somewhere
unmistakable and launching the game is the direct test.

Source WADs are read from the pristine copy, so repeated runs stay idempotent.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from croc2 import Wad  # noqa: E402
from croc2.chunks import MapChunk  # noqa: E402
from croc2.paths import GAME_WADS as TARGET, PRISTINE_WADS as PRISTINE  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level", nargs="?", default="t1l1m001")
    ap.add_argument("--cell", default="10,0,17",
                    help="destination grid cell as x,y,z")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    name = f"{args.level}.wad"
    original = (PRISTINE / name).read_bytes()
    if args.restore:
        (TARGET / name).write_bytes(original)
        print(f"restored {name} ({len(original)} bytes)")
        return 0

    wad = Wad.from_bytes(original)
    chunk = wad.get("MAP")
    mp = MapChunk.parse(chunk.payload)

    spawns = [e for e in mp.entities if e.is_spawn]
    if len(spawns) != 1:
        print(f"expected exactly one spawn record, found {len(spawns)}")
        return 1
    spawn = spawns[0]

    occupied = {(int(round(t.x - 0.5)), int(round(-t.z - 0.5))) for t in mp.tiles}
    x, y, z = (int(v) for v in args.cell.split(","))
    print(f"{args.level}: grid {mp.grid_x}x{mp.grid_z}, {len(mp.entities)} entities")
    print(f"  spawn cell {spawn.cell} -> ({x}, {y}, {z})")
    print(f"  old cell on terrain: {spawn.cell[0], spawn.cell[2] in occupied}")
    print(f"  new cell on terrain: {(x, z) in occupied}")

    spawn.set_cell(x, y, z)
    chunk.payload = mp.to_bytes()

    patched = wad.to_bytes()
    assert len(patched) == len(original), "patch must not change file size"
    changed = sum(a != b for a, b in zip(patched, original))
    print(f"  {changed} bytes differ; size unchanged at {len(patched)}")

    if args.dry_run:
        return 0
    (TARGET / name).write_bytes(patched)
    print(f"  wrote {TARGET / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
