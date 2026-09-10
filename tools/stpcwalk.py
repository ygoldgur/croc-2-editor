#!/usr/bin/env python3
"""Walk the STPC chunk the way the game's loader does, and check it consumes exactly.

The layout itself lives in croc2/chunks/stpc.py; this is the report over it. Landing
on the last byte is what makes the reading trustworthy rather than plausible, and it
is the same test that validated MAP and TRAK.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from croc2 import Wad  # noqa: E402
from croc2.chunks.stpc import GROUPS_FLAG, StpcChunk, StpcParseError  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402


def level_paths(level: str | None) -> list[Path]:
    if level:
        return [PRISTINE_WADS / f"{level}.wad"]
    return sorted(PRISTINE_WADS.glob("*.wad"))


def parse(wad: Wad) -> StpcChunk:
    flags = struct.unpack("<I", wad.get("WFPC").payload)[0]
    return StpcChunk.parse(wad.get("STPC").payload, bool(flags & GROUPS_FLAG))


def report_names(paths: list[Path]) -> None:
    """List each level's actors, and the names found inside them."""
    for path in paths:
        wad = Wad.read(path)
        if not wad.is_level:
            continue
        try:
            stpc = parse(wad)
        except (StpcParseError, struct.error, KeyError) as exc:
            print(f"{path.stem}: {exc}")
            continue
        print(f"{path.stem}: {len(stpc.actors)} actors")
        for actor in stpc.actors:
            names = actor.names
            head = names[0] if names else "(unnamed)"
            print(f"   {actor.index:>3}  {len(actor.data):>7} bytes  {head}")
            for name in names[1:]:
                print(f"                          {name}")


def report_one(stem: str, stpc: StpcChunk, size: int) -> None:
    verts = sum(m.vertex_count for m in stpc.models)
    tris = sum(m.triangle_count for m in stpc.models)
    print(f"{stem}: STPC {size:,} bytes consumed exactly")
    print(f"   models      {len(stpc.models)}")
    print(f"   animations  {stpc.animation_count}")
    print(f"   groups      {stpc.group_count}")
    print(f"   actors      {len(stpc.actors)}")
    print(f"   vertices    {verts:,}")
    print(f"   triangles   {tris:,}")
    print(f"   models end at {stpc.models_end:,}, anims end at "
          f"{stpc.animations_end:,}, actors at {stpc.actors_at:,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level", nargs="?")
    ap.add_argument("--names", action="store_true",
                    help="list each level's actors and the names inside them")
    args = ap.parse_args()
    paths = level_paths(args.level)

    if args.names:
        report_names(paths)
        return 0

    exact = failed = 0
    problems = []
    for path in paths:
        wad = Wad.read(path)
        if not wad.is_level:
            continue
        try:
            stpc = parse(wad)
        except (StpcParseError, struct.error, KeyError) as exc:
            failed += 1
            problems.append((path.stem, str(exc)))
            continue
        if stpc.left:
            failed += 1
            problems.append((path.stem, f"{stpc.left:+,} bytes left"))
            continue
        exact += 1
        if args.level:
            report_one(path.stem, stpc, len(wad.get("STPC").payload))

    if not args.level:
        print(f"{exact} levels walked exactly, {failed} failed")
        for stem, why in problems[:12]:
            print(f"   {stem}: {why}")
    elif problems:
        for stem, why in problems:
            print(f"{stem}: {why}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
