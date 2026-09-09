#!/usr/bin/env python3
"""Assert that every WAD survives a parse/serialize cycle byte-for-byte."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from croc2 import Wad  # noqa: E402
from croc2.paths import PRISTINE_WADS as WADS  # noqa: E402


def main() -> int:
    failures = []
    levels = raws = 0
    for p in sorted(WADS.glob("*.wad")):
        original = p.read_bytes()
        wad = Wad.from_bytes(original, p)
        if wad.raw is not None:
            raws += 1
        elif wad.is_level:
            levels += 1
        if wad.to_bytes() != original:
            failures.append(p.name)
            continue
        bad = [c.name for c in wad.chunks if c.size_is_wrong]
        if bad:
            print(f"  {p.name}: declared size wrong for {', '.join(bad)}")

    total = levels + raws + len(failures)
    print(f"\n{total} wads: {levels} levels, {raws} non-chunk, {len(failures)} failed")
    if failures:
        print("FAILED:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
