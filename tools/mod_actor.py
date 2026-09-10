#!/usr/bin/env python3
"""Move actor instances in a level and see whether the game agrees.

The MAP actor instance record reads as a position and a pointer to an actor
definition, and the markers land where their names suggest. That is two of the three
methods this project trusts; this is the third, and the only one that has ever caught
an answer the other two agreed on.

The default experiment swaps two instances between real, known-good positions, so
neither can land off the terrain or inside a wall:

  * WalkingCroc goes to another actor's spot, well away from where the level starts.
  * A Gobbo goes to WalkingCroc's spot, so it stands exactly where Croc arrives.

That is informative whichever way it comes out. If Croc appears in the new place, the
record is the spawn point and the position fields are right. If he still arrives on the
beach, the spawn lives elsewhere - but the Gobbo will be standing in his face, which
confirms the position fields anyway.

Source WADs are read from the pristine copy, so repeated runs stay idempotent.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from croc2 import Wad  # noqa: E402
from croc2.chunks import MapChunk  # noqa: E402
from croc2.chunks.stpc import GROUPS_FLAG, StpcChunk  # noqa: E402
from croc2.chunks.trak import HEADER_BASE, HEADER_SIZE  # noqa: E402
from croc2.paths import GAME_WADS as TARGET, PRISTINE_WADS as PRISTINE  # noqa: E402

SPAWN_ACTOR = "WalkingCroc"


def load(level: str):
    """Parse a level far enough to reach its actor instances, from pristine bytes."""
    original = (PRISTINE / f"{level}.wad").read_bytes()
    wad = Wad.from_bytes(original)
    trak_payload = wad.get("TRAK").payload
    count = struct.unpack_from("<I", trak_payload, 0)[0]
    vertex_counts = [
        struct.unpack_from("<H", trak_payload, HEADER_BASE + i * HEADER_SIZE + 0x6C)[0]
        for i in range(count)]
    wfpc = struct.unpack("<I", wad.get("WFPC").payload)[0]
    chunk = wad.get("MAP")
    mp = MapChunk.parse(chunk.payload, vertex_counts, wfpc)
    stpc = StpcChunk.parse(wad.get("STPC").payload, bool(wfpc & GROUPS_FLAG))
    return original, wad, chunk, mp, stpc


def name_of(stpc: StpcChunk, inst) -> str:
    actor = stpc.actor_at(inst.actor_offset)
    return actor.name if actor else f"@{inst.actor_offset}"


def show(stpc: StpcChunk, inst, label: str = "") -> None:
    x, y, z = inst.position
    yaw = inst.degrees[1]
    print(f"  {inst.index:>3}  {x:7.2f} {y:6.2f} {z:8.2f}  yaw {yaw:6.1f}  "
          f"{name_of(stpc, inst)}{label}")


def find(stpc: StpcChunk, mp: MapChunk, want: str):
    """First instance whose actor name contains `want`, case-insensitively."""
    for inst in mp.instances:
        if want.lower() in name_of(stpc, inst).lower():
            return inst
    return None


def write_out(level: str, wad: Wad, chunk, mp: MapChunk, original: bytes,
              dry_run: bool) -> int:
    chunk.payload = mp.to_bytes()
    patched = wad.to_bytes()
    if len(patched) != len(original):
        print(f"refusing to write: size changed {len(original)} -> {len(patched)}")
        return 1
    changed = sum(a != b for a, b in zip(patched, original))
    print(f"\n  {changed} bytes differ; size unchanged at {len(patched):,}")
    if dry_run:
        print("  dry run, nothing written")
        return 0
    (TARGET / f"{level}.wad").write_bytes(patched)
    print(f"  wrote {TARGET / f'{level}.wad'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level", nargs="?", default="t1l1m001")
    ap.add_argument("--list", action="store_true",
                    help="show the level's actor instances and stop")
    ap.add_argument("--move", nargs=4, metavar=("IDX", "X", "Y", "Z"),
                    help="put one instance at a world position")
    ap.add_argument("--swap", nargs=2, type=int, metavar=("I", "J"),
                    help="exchange two instances' positions")
    ap.add_argument("--landmark", default="trampoline",
                    help="actor whose spot Croc is moved to, for the default swap")
    ap.add_argument("--gobbo", default="Gobbo",
                    help="actor moved to Croc's spot, for the default swap")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    name = f"{args.level}.wad"
    if args.restore:
        original = (PRISTINE / name).read_bytes()
        (TARGET / name).write_bytes(original)
        print(f"restored {name} ({len(original):,} bytes)")
        return 0

    original, wad, chunk, mp, stpc = load(args.level)
    print(f"{args.level}: {len(mp.instances)} actor instances, "
          f"{len(stpc.actors)} actor definitions")

    if args.list:
        print(f"  {'#':>3}  {'x':>7} {'y':>6} {'z':>8}  {'yaw':>10}  actor")
        for inst in mp.instances:
            show(stpc, inst)
        return 0

    if args.move:
        idx = int(args.move[0])
        target = tuple(float(v) for v in args.move[1:])
        inst = mp.instances[idx]
        print("before:")
        show(stpc, inst)
        inst.position = target
        print("after:")
        show(stpc, inst)
        return write_out(args.level, wad, chunk, mp, original, args.dry_run)

    if args.swap:
        first, second = (mp.instances[i] for i in args.swap)
        print("\nbefore:")
        show(stpc, first)
        show(stpc, second)
        first.position, second.position = second.position, first.position
    else:
        croc = find(stpc, mp, SPAWN_ACTOR)
        landmark = find(stpc, mp, args.landmark)
        gobbo = find(stpc, mp, args.gobbo)
        if croc is None or landmark is None or gobbo is None:
            print(f"could not find {SPAWN_ACTOR}, {args.landmark} and {args.gobbo} "
                  f"in this level")
            return 1
        # Croc takes the landmark's spot rather than the Gobbo's, so the Gobbo can have
        # Croc's: three known-good positions, two of them changed.
        print("\nbefore:")
        show(stpc, croc, "   <- moving to the landmark")
        show(stpc, landmark, "   <- the landmark, staying put")
        show(stpc, gobbo, "   <- moving to where Croc arrives")
        first, second = croc, gobbo
        croc_was = croc.position
        croc.position = landmark.position
        gobbo.position = croc_was

    print("\nafter:")
    show(stpc, first)
    show(stpc, second)
    return write_out(args.level, wad, chunk, mp, original, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
