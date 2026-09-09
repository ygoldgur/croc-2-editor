#!/usr/bin/env python3
"""Walk the MAP chunk the way the game's loader does, and check it consumes exactly.

Transcribed from FUN_00422020 in Croc2.exe. The loader reads MAP as a sequence of
sections through fread wrappers of width 1, 2 and 4, so every field width is known;
what is not known is the state of three feature flags that gate three of the sections.
This tries every combination and reports which one lands exactly on the end of the
chunk, which is the same total-size test that validated TRAK.
"""
from __future__ import annotations

import argparse
import itertools
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from croc2 import Wad  # noqa: E402
from croc2.chunks import TrakChunk  # noqa: E402
from croc2.chunks.trak import HEADER_BASE, HEADER_SIZE  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402


class Short(Exception):
    """Ran off the end of the chunk."""


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise Short(f"wanted {n} at {self.pos}, only {len(self.data)} bytes")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def skip(self, n: int) -> None:
        self.take(n)


# Bytes consumed per record, counted off the loader's reads.
ENTITY_RECORD = 90     # 92-byte struct; +0x28 holds an offset into STPC
SECTION_C = 30         # 40-byte struct
SECTION_D = 20         # 24-byte struct; last field comes from memory, not the file
SECTION_F = 58         # 64-byte struct


def walk(data: bytes, vertex_counts: list[int], flag_10000: bool,
         flag_200000: bool, flag_10: bool, flag_4000000: bool) -> dict:
    r = Reader(data)
    out: dict = {}

    tile_count = r.u32()
    grid_x = r.u32()
    grid_z = r.u32()
    out.update(tile_count=tile_count, grid_x=grid_x, grid_z=grid_z)
    if not (0 < tile_count < 100000 and 0 < grid_x < 1024 and 0 < grid_z < 1024):
        raise Short("implausible header")

    tiles = []
    for _ in range(tile_count):
        x, y, z, rot, model, flags = struct.unpack("<3f3I", r.take(24))
        tiles.append((x, y, z, rot, model, flags))
    out["tiles"] = tiles

    out["n_a"] = n = r.u32()          # small leading array
    r.skip(n * 4)

    out["n_entities"] = n = r.u32()   # object/actor placements
    entities_at = r.pos
    r.skip(n * ENTITY_RECORD)
    out["entities_at"] = entities_at

    out["n_c"] = n = r.u32()
    r.skip(n * SECTION_C)

    r.skip(256)                       # fixed block of 32 uint32 pairs
    r.skip(grid_x * grid_z * 4)       # one word per grid cell
    r.skip(tile_count * 24)
    per_tile = [r.u32() for _ in range(tile_count)]   # model index per tile

    # Section D pairs, in order, with the tiles whose flag field is non-zero.
    extra_model: dict[int, int] = {}
    if flag_10000:
        out["n_d"] = n = r.u32()
        flagged = [i for i, t in enumerate(tiles) if t[5] != 0]
        for i in range(n):
            fields = struct.unpack("<5I", r.take(SECTION_D))
            if i < len(flagged):
                extra_model[flagged[i]] = fields[4]

    # Per-tile vertex colours. A row is one word per vertex of the tile's model, and
    # the high byte of the first row gives how many rows there are. Tiles that appear
    # in section D then carry a second set of rows for that section's model, with the
    # layer count carried over rather than restarted.
    for tile, model in enumerate(per_tile):
        if model >= len(vertex_counts):
            raise Short(f"model {model} out of range")
        vc = vertex_counts[model]
        layers = 0
        if vc:
            row = [r.u32() for _ in range(vc)]
            layers = max(c >> 24 for c in row)
            if layers > 1:
                r.skip((layers - 1) * vc * 4)

        if tile in extra_model:
            second = extra_model[tile]
            if second >= len(vertex_counts):
                raise Short(f"section D model {second} out of range")
            vc2 = vertex_counts[second]
            if vc2:
                row = [r.u32() for _ in range(vc2)]
                layers = max([layers] + [c >> 24 for c in row])
                if layers > 1:
                    r.skip((layers - 1) * vc2 * 4)

    out["n_e"] = n = r.u32()
    r.skip(n * 48)

    out["n_f"] = n = r.u32()
    r.u32()
    r.skip(n * SECTION_F)

    if flag_200000:
        groups = r.u32()
        for _ in range(groups):
            count = r.u32()
            for _ in range(count):
                items = r.u16()
                r.u16()
                # 28 bytes per item in memory, but only 12 in the file - or 16 when
                # the 0x4000000 flag adds the field the loader otherwise zeroes.
                r.skip(items * (16 if flag_4000000 else 12))

    if flag_10:
        r.u32()

    r.u16()
    out["end"] = r.pos
    out["left"] = len(data) - r.pos
    return out


def vertex_counts_for(wad: Wad) -> list[int]:
    data = wad.get("TRAK").payload
    count = struct.unpack_from("<I", data, 0)[0]
    return [struct.unpack_from("<H", data, HEADER_BASE + i * HEADER_SIZE + 0x6C)[0]
            for i in range(count)]


def flags_from_wfpc(wad: Wad) -> tuple[bool, bool, bool, bool]:
    """The gates on the optional sections are bits of the level's WFPC word."""
    word = struct.unpack("<I", wad.get("WFPC").payload)[0]
    return (bool(word & 0x10000), bool(word & 0x200000),
            bool(word & 0x10), bool(word & 0x4000000))


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
                  else [flags_from_wfpc(wad)])
        hit = None
        for combo in combos:
            try:
                out = walk(data, counts, *combo)
            except (Short, ValueError, struct.error):
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
                for k in ("tile_count", "grid_x", "grid_z", "n_a", "n_entities",
                          "n_c", "n_e", "n_f", "end"):
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
