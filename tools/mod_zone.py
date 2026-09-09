#!/usr/bin/env python3
"""Prove the MAP records are environment zones by making the fog impossible to miss.

FUN_00423ce0 feeds these fields to the fog, the music player and ADS_SetReverb. If
that reading is right, washing the fog out to white and pulling it in close should be
obvious the moment the level loads.

The colour is set to white rather than a hue on purpose: the loader has a branch that
forces the fog colour to greyscale off its green byte, so a red would come out black
and tell us less. White survives either path.

Source WADs are read from the pristine copy, so repeated runs stay idempotent.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from croc2 import Wad  # noqa: E402
from croc2.chunks import MapChunk  # noqa: E402
from croc2.paths import GAME_WADS as TARGET, PRISTINE_WADS as PRISTINE  # noqa: E402

FOG_NEAR = 1.0
FOG_FAR = 5.0
FOG_COLOUR = (255, 255, 255)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level", nargs="?", default="t1l1m001")
    ap.add_argument("--music", type=int, help="also force every zone to this track")
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

    print(f"{args.level}: {len(mp.zones)} zone records")
    for i, zone in enumerate(mp.zones):
        before = (zone.fog_near, zone.fog_far, zone.fog_colour, zone.music_track)
        zone.set_word(0x3C, int(FOG_NEAR * 4096))
        zone.set_word(0x40, int(FOG_FAR * 4096))
        zone.fog_colour = FOG_COLOUR
        if args.music is not None:
            zone.set_word(0x58, args.music)
        if i < 3:
            print(f"  zone {i}: fog {before[0]:.1f}..{before[1]:.1f} {before[2]} "
                  f"music={before[3]}  ->  fog {FOG_NEAR:.1f}..{FOG_FAR:.1f} "
                  f"{FOG_COLOUR}" + (f" music={args.music}" if args.music is not None
                                     else ""))
    if len(mp.zones) > 3:
        print(f"  ... and {len(mp.zones) - 3} more, all set the same")

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
