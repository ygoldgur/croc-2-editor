#!/usr/bin/env python3
"""Swap the model an actor names, and see whether the game draws the other one.

Actor bytecode names its models by pushing the absolute STPC offset of their headers
(``124 <offset>``). Which push an instance ends up with is decided by its entry code
(``croc2.vm``); this is the game test of both.

The default experiment rewrites the Distressed Gobbo's push of its first model (12, a red
sailor hat) to Gobbo Says' model (9, a purple box hat). Both have nine parts, so the
Distressed Gobbo's own animations still fit. One Distressed Gobbo is also moved to where
Croc arrives, so it is the first thing on screen.

Done in game: no Distressed Gobbo took the purple hat, but ``--map 12=9 13=6`` turned
every one seen into the Clipboard Gobbo. The pushes are the models. ``--word 756=1``
pinned the entry code's ``rand`` to 0 and turned every one into model 12, so the switch
over 12, 13 and 14 is what picks; ``rand`` is never seeded, so it picks 13 every boot.

Source WADs are read from the pristine copy, so repeated runs stay idempotent.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from croc2.paths import GAME_WADS as TARGET, PRISTINE_WADS as PRISTINE  # noqa: E402
from tools.mod_actor import SPAWN_ACTOR, find, load, parse, show  # noqa: E402


def actor_named(stpc, want: str):
    for actor in stpc.actors:
        if actor.name.lower() == want.lower():
            return actor
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level", nargs="?", default="t1l1m001")
    ap.add_argument("--actor", default="Distressed Gobbo", help="actor whose push to rewrite")
    ap.add_argument("--donor", default="Gobbo Says",
                    help="actor whose first model is put in instead")
    ap.add_argument("--to", type=int, help="model index to put in, instead of the donor's")
    ap.add_argument("--all", action="store_true",
                    help="rewrite every model the actor pushes, not just the first")
    ap.add_argument("--map", nargs="+", metavar="FROM=TO",
                    help="rewrite each push of model FROM to model TO, e.g. 12=9 13=6")
    ap.add_argument("--word", nargs="+", metavar="AT=VALUE",
                    help="set the actor's payload word at byte offset AT; alone, it "
                         "leaves the pushes alone, e.g. 756=1 makes the Gobbo's rand(1)")
    ap.add_argument("--no-bring", action="store_true",
                    help="leave the actor's instances where they are")
    ap.add_argument("--force", action="store_true", help="allow a different part count")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    name = f"{args.level}.wad"
    if args.restore:
        original = (PRISTINE / name).read_bytes()
        (TARGET / name).write_bytes(original)
        print(f"restored {name} ({len(original):,} bytes)")
        return 0

    original, wad, map_chunk, mp, stpc = load(args.level)
    stpc_chunk = wad.get("STPC")
    data = bytearray(stpc_chunk.payload)

    actor = actor_named(stpc, args.actor)
    if actor is None:
        print(f"no actor named {args.actor!r} in {args.level}")
        return 1
    pushes = [r for r in stpc.references(actor) if r.kind == "model"]
    if not pushes:
        print(f"{args.actor} pushes no model")
        return 1
    if args.map:
        mapping = dict(tuple(int(v) for v in pair.split("=")) for pair in args.map)
    elif args.word and args.to is None and not args.all:
        mapping = {}
    else:
        if args.to is not None:
            target = args.to
        else:
            donor = actor_named(stpc, args.donor)
            donor_models = ([r for r in stpc.references(donor) if r.kind == "model"]
                            if donor else [])
            if not donor_models:
                print(f"no donor model: {args.donor!r} not found or pushes none")
                return 1
            target = donor_models[0].index
        mapping = {r.index: target for r in (pushes if args.all else pushes[:1])}

    print(f"{args.level}: actor {actor.index} {actor.name!r} pushes models "
          f"{[r.index for r in pushes]}")
    chosen = [r for r in pushes if r.index in mapping]
    if len({r.index for r in chosen}) != len(mapping):
        print(f"refusing: {sorted(set(mapping) - {r.index for r in chosen})} "
              f"not pushed by this actor")
        return 1
    for r in chosen:
        target = mapping[r.index]
        if stpc.models[r.index].extra_count != stpc.models[target].extra_count and not args.force:
            print(f"refusing: model {r.index} has {stpc.models[r.index].extra_count} parts, "
                  f"model {target} has {stpc.models[target].extra_count}; its animations "
                  f"would not fit (--force)")
            return 1
        at = actor.offset + 4 + r.at
        old = struct.unpack_from("<I", data, at)[0]
        assert old == stpc.models[r.index].offset and data[at - 4:at] == b"\x7c\0\0\0"
        struct.pack_into("<I", data, at, stpc.models[target].offset)
        print(f"  STPC +{at}: push model {r.index} (offset {old}) -> "
              f"model {target} (offset {stpc.models[target].offset})")
    for pair in args.word or []:
        rel, value = (int(v) for v in pair.split("="))
        at = actor.offset + 4 + rel
        old = struct.unpack_from("<i", data, at)[0]
        struct.pack_into("<i", data, at, value)
        print(f"  STPC +{at}: actor word +{rel} {old} -> {value}")
    stpc_chunk.payload = bytes(data)

    instances = [i for i in mp.instances if stpc.actor_at(i.actor_offset) is actor]
    if not args.no_bring:
        croc = find(stpc, mp, SPAWN_ACTOR)
        if croc is not None and instances:
            print("\nmoving one to where Croc arrives:")
            show(stpc, instances[0], "   <- before")
            instances[0].position = croc.position
            show(stpc, instances[0], "   <- after")
            map_chunk.payload = mp.to_bytes()

    patched = wad.to_bytes()
    if len(patched) != len(original):
        print(f"refusing to write: size changed {len(original)} -> {len(patched)}")
        return 1
    changed = sum(a != b for a, b in zip(patched, original))
    print(f"\n  {changed} bytes differ; size unchanged at {len(patched):,}")

    # Read the result back the way the tools do, so the prediction is the library's.
    _, _, _, _, stpc2 = parse(patched)
    now = [r.index for r in stpc2.references(stpc2.actors[actor.index]) if r.kind == "model"]
    print(f"  read back: {actor.name} now pushes models {now}")
    for inst in instances:
        chances = stpc2.instance_models(inst.actor_offset)
        print(f"    instance {inst.index:3} at ({inst.position[0]:6.2f} {inst.position[2]:6.2f})"
              f": predicted {chances or 'not reached by the walk'}")

    if args.dry_run:
        print("  dry run, nothing written")
        return 0
    (TARGET / name).write_bytes(patched)
    print(f"  wrote {TARGET / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
