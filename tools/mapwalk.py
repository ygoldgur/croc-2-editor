#!/usr/bin/env python3
"""Walk the MAP chunk the way the game's loader does, and check it consumes exactly.

The layout itself lives in croc2/chunks/map_.py, transcribed from FUN_00422020. This
is the report over it: landing on the last byte is the same total-size test that
validated TRAK, and it holds on all 75 parseable levels.

Four sections are gated on bits of the level's WFPC word. `--search` ignores WFPC and
tries every combination instead, which is how those bits were identified.
"""
from __future__ import annotations

import argparse
import itertools
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from croc2 import Wad  # noqa: E402
from croc2.chunks.map_ import MapWalkError, flags_from_wfpc, walk_sections  # noqa: E402
from croc2.chunks.trak import HEADER_BASE, HEADER_SIZE  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402

REPORTED = ("tile_count", "grid_x", "grid_z", "n_a", "n_zones", "n_c", "n_e",
            "n_instances", "end")


def vertex_counts_for(wad: Wad) -> list[int]:
    data = wad.get("TRAK").payload
    count = struct.unpack_from("<I", data, 0)[0]
    return [struct.unpack_from("<H", data, HEADER_BASE + i * HEADER_SIZE + 0x6C)[0]
            for i in range(count)]


def wfpc_flags(wad: Wad) -> tuple[bool, bool, bool, bool]:
    return flags_from_wfpc(struct.unpack("<I", wad.get("WFPC").payload)[0])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level", nargs="?")
    ap.add_argument("--search", action="store_true",
                    help="try every flag combination instead of reading WFPC")
    args = ap.parse_args()

    paths = ([PRISTINE_WADS / f"{args.level}.wad"] if args.level
             else sorted(PRISTINE_WADS.glob("*.wad")))
    exact = failed = 0
    winners: dict[tuple, int] = {}

    for path in paths:
        wad = Wad.read(path)
        if not wad.is_level:
            continue
        try:
            counts = vertex_counts_for(wad)
        except Exception:
            continue
        data = wad.get("MAP").payload

        combos = (itertools.product([False, True], repeat=4) if args.search
                  else [wfpc_flags(wad)])
        hit = None
        for combo in combos:
            try:
                out = walk_sections(data, counts, *combo)
            except (MapWalkError, ValueError, struct.error):
                continue
            if out["left"] == 0:
                hit = (combo, out)
                break
        if hit:
            exact += 1
            winners[hit[0]] = winners.get(hit[0], 0) + 1
            if args.level:
                combo, out = hit
                print(f"{path.stem}: exact with flags "
                      f"0x10000={combo[0]} 0x200000={combo[1]} 0x10={combo[2]} "
                      f"0x4000000={combo[3]}")
                for k in REPORTED:
                    print(f"   {k:12s} {out[k]}")
        else:
            failed += 1
            if args.level:
                print(f"{path.stem}: no flag combination consumes the chunk exactly")

    if not args.level:
        print(f"{exact} levels walked exactly, {failed} failed")
        for combo, n in sorted(winners.items(), key=lambda kv: -kv[1]):
            print(f"   flags 0x10000={combo[0]} 0x200000={combo[1]} "
                  f"0x10={combo[2]} 0x4000000={combo[3]}: {n}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
