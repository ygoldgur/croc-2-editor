#!/usr/bin/env python3
"""Probe instance->model candidates in actor entry payloads.

MAP actor instances point into actor payloads, not at model ids directly. This script
scans fixed offsets around that per-instance entry point and ranks fields by how often
their values stay inside the level's STPC model table.
"""
from __future__ import annotations

import argparse
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from croc2 import Wad  # noqa: E402
from croc2.chunks import MapChunk, TrakChunk  # noqa: E402
from croc2.chunks.stpc import GROUPS_FLAG, StpcChunk  # noqa: E402
from croc2.level import Level  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402


@dataclass
class Row:
    level: str
    model_count: int
    actor_index: int
    actor_name: str
    instance_index: int
    entry_pos: int  # offset within actor payload where instance entry starts
    payload: bytes
    signature: tuple[int, int]


def rows_for(level_filter: str | None = None) -> list[Row]:
    out: list[Row] = []
    for path in sorted(PRISTINE_WADS.glob("*.wad")):
        stem = path.stem
        if level_filter and stem != level_filter:
            continue
        wad = Wad.read(path)
        if not wad.is_level:
            continue
        try:
            wfpc = struct.unpack("<I", wad.get("WFPC").payload)[0]
            stpc = StpcChunk.parse(wad.get("STPC").payload, bool(wfpc & GROUPS_FLAG))
            trak = TrakChunk.parse(wad.get("TRAK").payload)
            mp = MapChunk.parse(wad.get("MAP").payload, [len(m.vertices) for m in trak.models], wfpc)
        except Exception:
            continue

        for inst in mp.instances:
            actor = stpc.actor_at(inst.actor_offset)
            if actor is None:
                continue
            rel = inst.actor_offset - actor.offset
            pos = rel - 4
            if pos < 0 or pos + 8 > len(actor.data):
                continue
            sig = struct.unpack_from("<II", actor.data, pos)
            out.append(Row(
                level=stem,
                model_count=len(stpc.models),
                actor_index=actor.index,
                actor_name=actor.name,
                instance_index=inst.index,
                entry_pos=pos,
                payload=actor.data,
                signature=sig,
            ))
    return out


def probe(rows: list[Row], width: int, dmin: int, dmax: int, only_signature: bool) -> list[tuple[float, int, int, int, int]]:
    fmt = "<H" if width == 2 else "<I"
    use = [r for r in rows if (not only_signature or r.signature == (0xB0, 0xD4))]
    ranked: list[tuple[float, int, int, int, int]] = []
    for delta in range(dmin, dmax + 1):
        ok = tot = 0
        values: list[int] = []
        for r in use:
            at = r.entry_pos + delta
            if at < 0 or at + width > len(r.payload):
                continue
            tot += 1
            value = struct.unpack_from(fmt, r.payload, at)[0]
            if value < r.model_count:
                ok += 1
                values.append(value)
        if tot == 0:
            continue
        ranked.append((ok / tot, ok, tot, delta, len(set(values))))
    return sorted(ranked, reverse=True)


def dump_level(level: str, delta: int, width: int, only_signature: bool) -> int:
    lvl = Level.load(PRISTINE_WADS / f"{level}.wad")
    if lvl.stpc is None:
        print(f"{level}: STPC did not parse")
        return 2
    stpc = lvl.stpc
    fmt = "<H" if width == 2 else "<I"
    counts = Counter()
    print(f"{level}: {len(lvl.map.instances)} instances, {len(stpc.models)} STPC models")
    print(f"field: width={width} delta={delta} ({'only b0,d4 entries' if only_signature else 'all entries'})")
    for inst in lvl.map.instances:
        actor = stpc.actor_at(inst.actor_offset)
        if actor is None:
            continue
        pos = inst.actor_offset - actor.offset - 4
        if pos < 0 or pos + 8 > len(actor.data):
            continue
        sig = struct.unpack_from("<II", actor.data, pos)
        if only_signature and sig != (0xB0, 0xD4):
            continue
        at = pos + delta
        if at < 0 or at + width > len(actor.data):
            continue
        value = struct.unpack_from(fmt, actor.data, at)[0]
        marker = "*" if value < len(stpc.models) else "!"
        print(f"{marker} inst {inst.index:>2}  actor {lvl.actor_name(inst):24s}  value {value:>6}  sig {sig[0]:#x},{sig[1]:#x}")
        if value < len(stpc.models):
            counts[value] += 1
    if counts:
        print("\nin-range model-id histogram:")
        for model, n in counts.most_common():
            print(f"  model {model:>3}: {n}")
    return 0


def guess_level(level: str, only_signature: bool) -> int:
    """Guess model ids by learning a per-signature offset rule inside one level."""
    lvl = Level.load(PRISTINE_WADS / f"{level}.wad")
    if lvl.stpc is None:
        print(f"{level}: STPC did not parse")
        return 2
    stpc = lvl.stpc
    candidates = [24, 28, 44, 60, 64, 124, 140, 144, 152]

    # Build rows per signature.
    grouped: dict[bytes, list[tuple[int, bytes, int]]] = {}
    for inst in lvl.map.instances:
        actor = stpc.actor_at(inst.actor_offset)
        if actor is None:
            continue
        pos = inst.actor_offset - actor.offset - 4
        if pos < 0 or pos + 32 > len(actor.data):
            continue
        sig = struct.unpack_from("<II", actor.data, pos)
        if only_signature and sig != (0xB0, 0xD4):
            continue
        key = actor.data[pos:pos + 32]
        grouped.setdefault(key, []).append((inst.index, actor.data, pos))

    # Pick best delta for each signature by in-range coverage, then low cardinality.
    rule: dict[bytes, int] = {}
    for key, rows in grouped.items():
        best = None
        for delta in candidates:
            values = []
            ok = 0
            for _, data, pos in rows:
                at = pos + delta
                if at < 0 or at + 4 > len(data):
                    continue
                v = struct.unpack_from("<I", data, at)[0]
                values.append(v)
                ok += v < len(stpc.models)
            if not values:
                continue
            score = (ok / len(values), -len(set(values)), -delta)
            if best is None or score > best[0]:
                best = (score, delta)
        if best is not None:
            rule[key] = best[1]

    print(f"{level}: {len(lvl.map.instances)} instances, {len(stpc.models)} STPC models")
    print(f"signatures learned: {len(rule)}")
    print("legend: '*' in-range model guess, '!' unresolved")

    resolved = 0
    by_model = Counter()
    for inst in lvl.map.instances:
        actor = stpc.actor_at(inst.actor_offset)
        if actor is None:
            print(f"! inst {inst.index:>2}  actor {'<none>':24s}  model ?")
            continue
        pos = inst.actor_offset - actor.offset - 4
        if pos < 0 or pos + 32 > len(actor.data):
            print(f"! inst {inst.index:>2}  actor {lvl.actor_name(inst):24s}  model ?")
            continue
        sig = struct.unpack_from("<II", actor.data, pos)
        if only_signature and sig != (0xB0, 0xD4):
            print(f"! inst {inst.index:>2}  actor {lvl.actor_name(inst):24s}  model ?  (sig {sig[0]:#x},{sig[1]:#x})")
            continue
        key = actor.data[pos:pos + 32]
        delta = rule.get(key)
        if delta is None:
            print(f"! inst {inst.index:>2}  actor {lvl.actor_name(inst):24s}  model ?")
            continue
        at = pos + delta
        if at < 0 or at + 4 > len(actor.data):
            print(f"! inst {inst.index:>2}  actor {lvl.actor_name(inst):24s}  model ?")
            continue
        model = struct.unpack_from("<I", actor.data, at)[0]
        if model < len(stpc.models):
            resolved += 1
            by_model[model] += 1
            print(f"* inst {inst.index:>2}  actor {lvl.actor_name(inst):24s}  model {model:>3}  (delta {delta:>3})")
        else:
            print(f"! inst {inst.index:>2}  actor {lvl.actor_name(inst):24s}  model ?    (raw {model}, delta {delta})")

    print(f"\nresolved {resolved}/{len(lvl.map.instances)}")
    if by_model:
        print("model histogram:")
        for model, n in by_model.most_common():
            print(f"  model {model:>3}: {n}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", help="restrict probing to one level stem")
    ap.add_argument("--width", type=int, choices=(2, 4), default=4)
    ap.add_argument("--delta-min", type=int, default=-8)
    ap.add_argument("--delta-max", type=int, default=160)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--signature", action="store_true",
                    help="only actor entries starting with 0xb0,0xd4")
    ap.add_argument("--dump-level", action="store_true",
                    help="dump per-instance values for --level using --delta")
    ap.add_argument("--guess-level", action="store_true",
                    help="guess per-instance model ids for --level")
    ap.add_argument("--delta", type=int, default=24,
                    help="offset from entry start for --dump-level")
    args = ap.parse_args()

    if args.dump_level:
        if not args.level:
            print("--dump-level needs --level")
            return 2
        return dump_level(args.level, args.delta, args.width, args.signature)
    if args.guess_level:
        if not args.level:
            print("--guess-level needs --level")
            return 2
        return guess_level(args.level, args.signature)

    rows = rows_for(args.level)
    ranked = probe(rows, args.width, args.delta_min, args.delta_max, args.signature)
    print(f"rows: {len(rows)}")
    if args.signature:
        sig_rows = sum(r.signature == (0xB0, 0xD4) for r in rows)
        print(f"signature rows: {sig_rows}")
    print(f"top {args.top} candidates (coverage of in-range STPC model ids)")
    print(f"{'delta':>6} {'ok/tot':>11} {'coverage':>10} {'unique':>8}")
    for cov, ok, tot, delta, uniq in ranked[:args.top]:
        print(f"{delta:>6} {ok:>5}/{tot:<5} {cov:>9.1%} {uniq:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
