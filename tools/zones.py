#!/usr/bin/env python3
"""Pull the MAP zone records apart field by field and survey them.

The 90 file bytes scatter into a 92-byte struct, so fields are named by their offset
in memory, not in the file. Widths come from which fread wrapper the loader used.
"""
from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib.util  # noqa: E402

from croc2 import Wad  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402

_spec = importlib.util.spec_from_file_location("mapwalk", Path(__file__).parent / "mapwalk.py")
mapwalk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mapwalk)

# (memory offset, file offset, width). The loader reads these in file order.
LAYOUT = [
    (0x00, 0, 4), (0x04, 4, 4), (0x08, 8, 4), (0x0C, 12, 4),
    (0x10, 16, 4), (0x14, 20, 4), (0x18, 24, 4), (0x1C, 28, 4),
    (0x20, 32, 4), (0x24, 36, 4), (0x28, 40, 4), (0x2C, 44, 4),
    (0x30, 48, 4), (0x34, 52, 4), (0x38, 56, 2), (0x3C, 58, 4),
    (0x40, 62, 4), (0x44, 66, 4), (0x48, 70, 4), (0x4C, 74, 4),
    (0x50, 78, 4), (0x54, 82, 2), (0x56, 84, 2), (0x58, 86, 4),
]

NAMES = {0x00: "cell_x", 0x08: "cell_z", 0x20: "flags", 0x28: "stpc",
         0x3C: "fog_near", 0x40: "fog_far", 0x4C: "fog_rgb", 0x56: "reverb",
         0x58: "music"}

SKIP = {"t0i0m006", "t0l1m002"}


def field(rec: bytes, file_off: int, width: int) -> int:
    fmt = "<H" if width == 2 else "<I"
    return struct.unpack_from(fmt, rec, file_off)[0]


def read_level(path: Path):
    """Yield (level, grid_x, grid_z, [record bytes])."""
    wad = Wad.read(path)
    if not wad.is_level or path.stem in SKIP:
        return None
    counts = mapwalk.vertex_counts_for(wad)
    combo = mapwalk.flags_from_wfpc(wad)
    data = wad.get("MAP").payload
    out = mapwalk.walk(data, counts, *combo)
    at = out["entities_at"]
    recs = [data[at + i * 90: at + (i + 1) * 90] for i in range(out["n_entities"])]
    return path.stem, out["grid_x"], out["grid_z"], recs


def load_all():
    levels = []
    for path in sorted(PRISTINE_WADS.glob("*.wad")):
        try:
            got = read_level(path)
        except Exception:
            continue
        if got:
            levels.append(got)
    return levels


def label(mem: int) -> str:
    return f"+0x{mem:02x} {NAMES.get(mem, ''):8s}"


def survey(levels) -> None:
    recs = [r for _, _, _, rs in levels for r in rs]
    print(f"{len(recs)} zone records across {len(levels)} levels\n")
    print(f"{'field':18s} {'distinct':>8s} {'zero':>6s} {'min':>10s} {'max':>12s}  note")
    for mem, off, width in LAYOUT:
        vals = [field(r, off, width) for r in recs]
        uniq = set(vals)
        zero = sum(1 for v in vals if v == 0)
        note = ""
        nz = [v for v in vals if v]
        if nz and all(v % 4096 == 0 for v in nz):
            note = "all multiples of 4096 (4.12 fixed point)"
        elif nz and sum(1 for v in nz if v % 4096 == 0) > len(nz) * 0.8:
            note = "mostly multiples of 4096"
        if len(uniq) == 1:
            note = "constant"
        print(f"{label(mem):18s} {len(uniq):8d} {zero:6d} {min(vals):10d} "
              f"{max(vals):12d}  {note}")


def defaults(levels) -> None:
    """Fields shared by every level's default zone."""
    rows = []
    for name, gx, gz, recs in levels:
        for r in recs:
            if field(r, 32, 4) & 8:
                rows.append((name, r))
    print(f"\n{len(rows)} default zones (one per level)\n")
    for mem, off, width in LAYOUT:
        vals = Counter(field(r, off, width) for _, r in rows)
        if len(vals) == 1:
            print(f"  {label(mem):18s} constant {next(iter(vals)):#x}")
        elif len(vals) <= 6:
            print(f"  {label(mem):18s} {dict(vals)}")
        else:
            print(f"  {label(mem):18s} {len(vals)} distinct values")


def group_by(levels, mem: int) -> None:
    off, width = next((o, w) for m, o, w in LAYOUT if m == mem)
    counts = Counter()
    for _, _, _, recs in levels:
        for r in recs:
            counts[field(r, off, width)] += 1
    print(f"\nvalues of {label(mem)}:")
    for v, n in counts.most_common(20):
        print(f"   {v:#010x} ({v:6d}): {n:4d} records")


def dump(levels, want: str) -> None:
    for name, gx, gz, recs in levels:
        if name != want:
            continue
        print(f"{name}: {len(recs)} records, grid {gx}x{gz}\n")
        head = "  # " + " ".join(f"{m:>8s}" for m in
                                 (f"+{m:02x}" for m, _, _ in LAYOUT))
        print(head)
        for i, r in enumerate(recs):
            cells = " ".join(f"{field(r, o, w):8d}" for _, o, w in LAYOUT)
            mark = " <- default zone" if field(r, 32, 4) & 8 else ""
            print(f"{i:3d} {cells}{mark}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", metavar="LEVEL")
    ap.add_argument("--group", metavar="OFFSET")
    args = ap.parse_args()

    levels = load_all()
    if args.dump:
        dump(levels, args.dump)
    elif args.group:
        group_by(levels, int(args.group, 16))
    else:
        survey(levels)
        defaults(levels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
