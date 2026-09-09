#!/usr/bin/env python3
"""First symbolic change: patch the main menu level and prove the edit reaches the game.

Two independently recognisable edits are applied so a single launch tells us which
decoder works:

  * LGHT - recolour the key directional light to pure red, tinting the whole scene.
  * MAP  - lift a few terrain tiles well above the ground so they float visibly.

Source WADs are always read from the pristine copy so repeated runs stay idempotent.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from croc2 import Wad  # noqa: E402
from croc2.chunks import LightChunk, MapChunk  # noqa: E402
from croc2.paths import GAME_WADS as TARGET, PRISTINE_WADS as PRISTINE  # noqa: E402

LEVEL = "t0i0m000.wad"

LIFT = 3.0
LIFT_COUNT = 6


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", action="store_true", help="copy the pristine wad back")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    original = (PRISTINE / LEVEL).read_bytes()
    if args.restore:
        (TARGET / LEVEL).write_bytes(original)
        print(f"restored {LEVEL} ({len(original)} bytes)")
        return 0

    wad = Wad.from_bytes(original)

    lchunk = wad.get("LGHT")
    lights = LightChunk.parse(lchunk.payload)
    before = lights.lights[0].colour
    lights.lights[0].colour = (255, 0, 0)
    print(f"LGHT: light[0] colour {before} -> {lights.lights[0].colour}")
    lchunk.payload = lights.to_bytes()

    mchunk = wad.get("MAP")
    tiles = MapChunk.parse(mchunk.payload)
    # Lift a contiguous run from the middle of the list; they sit next to each other
    # in the world, so the result reads as one raised block rather than scattered noise.
    start = len(tiles.tiles) // 2
    for tile in tiles.tiles[start:start + LIFT_COUNT]:
        print(f"MAP : tile ({tile.x:6.2f},{tile.y:5.2f},{tile.z:6.2f}) "
              f"model={tile.model_id} y -> {tile.y + LIFT:.2f}")
        tile.y += LIFT
    mchunk.payload = tiles.to_bytes()

    patched = wad.to_bytes()
    assert len(patched) == len(original), "patch must not change file size"
    changed = sum(a != b for a, b in zip(patched, original))
    print(f"\n{changed} bytes differ; size unchanged at {len(patched)}")

    if args.dry_run:
        return 0
    (TARGET / LEVEL).write_bytes(patched)
    print(f"wrote {TARGET / LEVEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
