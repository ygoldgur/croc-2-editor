#!/usr/bin/env python3
"""Probe STPC animation records and likely actor entry animation fields.

This mirrors modelprobe's scoring style, but targets values in 0..n_animations-1.
"""
from __future__ import annotations

import argparse
import math
import struct
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from croc2.level import Level  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402


def show_animation_headers(level: Level, limit: int) -> None:
    stpc = level.stpc
    assert stpc is not None
    print(f"{level.name}: {len(stpc.animations)} animations")
    print("idx   off      body      bytes      n0 frames   gate0c     soft  parts")
    for i, anim in enumerate(stpc.animations[:limit]):
        print(f"{i:>3}  {anim.offset:>7}  {anim.body_at:>7}  "
              f"{anim.body_end - anim.body_at:>7}  {anim.n0:>6}  {anim.frame_count:>5}  "
              f"{anim.gate_0c:>7}  {anim.soft_count:>7}  {anim.part_count:>5}")

    if stpc.animations:
        sizes = [a.body_end - a.body_at for a in stpc.animations]
        print(f"\nbody bytes: min={min(sizes)}  median={sorted(sizes)[len(sizes)//2]}  "
              f"max={max(sizes)}")
        c0 = Counter(a.gate_0c for a in stpc.animations)
        c1 = Counter(a.soft_count for a in stpc.animations)
        print(f"gate_0c distinct={len(c0)} top={c0.most_common(8)}")
        print(f"soft_count distinct={len(c1)} top={c1.most_common(8)}")


def _instance_rows(level: Level, window: int) -> list[tuple[int, bytes, int]]:
    stpc = level.stpc
    assert stpc is not None
    rows: list[tuple[int, bytes, int]] = []
    for inst in level.map.instances:
        actor = stpc.actor_at(inst.actor_offset)
        if actor is None:
            continue
        pos = inst.actor_offset - actor.offset - 4
        if pos < 0 or pos + window > len(actor.data):
            continue
        rows.append((inst.index, actor.data, pos))
    return rows


def score_offsets(level: Level, width: int, dmin: int, dmax: int, window: int,
                  top: int) -> None:
    stpc = level.stpc
    assert stpc is not None
    rows = _instance_rows(level, window)
    if not rows:
        print("no actor entry windows available")
        return
    fmt = "<H" if width == 2 else "<I"
    ranked: list[tuple[float, int, int, int, int]] = []
    for delta in range(dmin, dmax + 1):
        vals = []
        ok = tot = 0
        for _, data, pos in rows:
            at = pos + delta
            if at < 0 or at + width > len(data):
                continue
            tot += 1
            v = struct.unpack_from(fmt, data, at)[0]
            vals.append(v)
            ok += (v < stpc.animation_count)
        if tot == 0:
            continue
        ranked.append((ok / tot, ok, tot, delta, len(set(vals))))
    ranked.sort(reverse=True)
    print(f"\n{level.name}: actor-entry field probe against animation range")
    print(f"rows={len(rows)}  animation_count={stpc.animation_count}")
    print(f"{'delta':>6} {'ok/tot':>11} {'coverage':>10} {'unique':>8}")
    for cov, ok, tot, delta, uniq in ranked[:top]:
        print(f"{delta:>6} {ok:>5}/{tot:<5} {cov:>9.1%} {uniq:>8}")


def dump_offset(level: Level, delta: int, width: int, window: int) -> None:
    stpc = level.stpc
    assert stpc is not None
    fmt = "<H" if width == 2 else "<I"
    print(f"\n{level.name}: values at delta {delta} width {width}")
    by_value = Counter()
    for inst in level.map.instances:
        actor = stpc.actor_at(inst.actor_offset)
        if actor is None:
            continue
        pos = inst.actor_offset - actor.offset - 4
        if pos < 0 or pos + window > len(actor.data):
            continue
        at = pos + delta
        if at < 0 or at + width > len(actor.data):
            continue
        v = struct.unpack_from(fmt, actor.data, at)[0]
        mark = "*" if v < stpc.animation_count else "!"
        if mark == "*":
            by_value[v] += 1
        print(f"{mark} inst {inst.index:>2}  {level.actor_name(inst):24s}  value {v:>8}")
    if by_value:
        print("in-range histogram:")
        for v, n in by_value.most_common():
            print(f"  anim {v:>3}: {n}")


def _parse_animation_body(level: Level, anim_index: int):
    stpc = level.stpc
    assert stpc is not None and level.stpc_payload is not None
    anim = stpc.animations[anim_index]
    data = level.stpc_payload
    cur = anim.body_at

    leading = [struct.unpack_from("<I", data, cur + i * 4)[0] for i in range(anim.n0)]
    cur += anim.n0 * 4

    table_8 = []
    if anim.gate_0c == 0:
        table_8 = [struct.unpack_from("<2I", data, cur + i * 8) for i in range(anim.frame_count)]
        cur += anim.frame_count * 8

    table_a = []
    if anim.soft_count != 0:
        table_a = [struct.unpack_from("<I", data, cur + i * 4)[0] for i in range(anim.frame_count)]
        cur += anim.frame_count * 4

    table_b = [struct.unpack_from("<I", data, cur + i * 4)[0] for i in range(anim.frame_count)]
    cur += anim.frame_count * 4

    frames: list[list[tuple[float, ...]]] = []
    for j in range(anim.frame_count):
        if anim.soft_count != 0:
            if j == 0:
                cur += anim.soft_count * 8
            elif anim.soft_count & 1:
                cur += anim.soft_count * 2 + 2
            else:
                cur += anim.soft_count * 2
        frame = []
        for _ in range(anim.part_count):
            frame.append(struct.unpack_from("<16f", data, cur))
            cur += 64
        frames.append(frame)
    return anim, leading, table_8, table_a, table_b, frames


def dump_animation(level: Level, anim_index: int, frame_limit: int) -> None:
    stpc = level.stpc
    assert stpc is not None
    if not (0 <= anim_index < len(stpc.animations)):
        print(f"animation index {anim_index} out of range 0..{len(stpc.animations)-1}")
        return
    anim, leading, table_8, table_a, table_b, frames = _parse_animation_body(level, anim_index)
    print(f"\n{level.name}: animation {anim_index}")
    print(f"offset={anim.offset} body={anim.body_at}..{anim.body_end} bytes={anim.body_end - anim.body_at}")
    print(f"n0={anim.n0} frames={anim.frame_count} gate_0c={anim.gate_0c} soft={anim.soft_count} parts={anim.part_count}")
    if leading:
        print(f"leading (n0*4): first={leading[:8]} unique={len(set(leading))}")
    if table_8:
        print(f"table_8 (n2*8): first={table_8[:5]} unique0={len(set(a for a,_ in table_8))}")
    if table_a:
        print(f"table_a (n2*4): first={table_a[:12]} unique={len(set(table_a))}")
    print(f"table_b (n2*4): first={table_b[:12]} unique={len(set(table_b))}")

    print("\nframe matrices (diag + translation):")
    for f, mats in enumerate(frames[:frame_limit]):
        head = []
        for m in mats:
            diag = (m[0], m[5], m[10], m[15])
            trans = (m[12], m[13], m[14])
            head.append((diag, trans))
        pretty = ", ".join(
            f"d=({d[0]:.3f},{d[1]:.3f},{d[2]:.3f},{d[3]:.3f}) "
            f"t=({t[0]:.3f},{t[1]:.3f},{t[2]:.3f})" for d, t in head
        )
        print(f"  frame {f:>3}: {pretty}")

    finite = 0
    total = 0
    one_w = 0
    for mats in frames:
        for m in mats:
            total += 1
            finite += all(math.isfinite(x) for x in m)
            one_w += abs(m[15] - 1.0) < 1e-4
    print(f"\nblocks={total}, finite={finite}/{total}, w==1.0={one_w}/{total}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="t1l1m001")
    ap.add_argument("--limit", type=int, default=32, help="headers to print")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--window", type=int, default=192)
    ap.add_argument("--width", type=int, choices=(2, 4), default=4)
    ap.add_argument("--delta-min", type=int, default=-8)
    ap.add_argument("--delta-max", type=int, default=192)
    ap.add_argument("--dump-delta", type=int)
    ap.add_argument("--anim", type=int, help="dump one animation body as matrices")
    ap.add_argument("--frames", type=int, default=3, help="frames to print with --anim")
    args = ap.parse_args()

    level = Level.load(PRISTINE_WADS / f"{args.level}.wad")
    if level.stpc is None:
        print(f"{args.level}: STPC did not parse")
        return 2
    show_animation_headers(level, args.limit)
    score_offsets(level, args.width, args.delta_min, args.delta_max,
                  args.window, args.top)
    if args.dump_delta is not None:
        dump_offset(level, args.dump_delta, args.width, args.window)
    if args.anim is not None:
        dump_animation(level, args.anim, args.frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
