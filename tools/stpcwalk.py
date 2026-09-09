#!/usr/bin/env python3
"""Walk the STPC chunk the way the game's loader does, and check it consumes exactly.

STPC is not read field by field like MAP. The loader allocates the whole chunk, reads
it in one go, and then walks it patching internal pointers (FUN_00421f40). Every
"allocation" is really just a bump of a cursor through that blob, so replaying the same
cursor arithmetic here has to land exactly on the end of the chunk.

The record shapes come from the two fixup helpers:

  FUN_00424db0  0x88-byte model headers, with vertex/triangle/face arrays of
                24, 28 and 44 bytes - the same sizes, at the same header offsets,
                as the TRAK terrain meshes.
  FUN_00424ef0  0x20-byte records with nested per-item arrays, ending in 64-byte
                blocks: the animation side.
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from croc2 import Wad  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402

MODEL_HEADER = 0x88   # vs 0x80 in TRAK: eight more bytes, one more array
VERTEX_SIZE = 0x18
TRIANGLE_SIZE = 0x1C
FACE_SIZE = 0x2C
ANIM_HEADER = 0x20


class Short(Exception):
    pass


def u16(data: bytes, off: int) -> int:
    if off + 2 > len(data):
        raise Short(f"u16 past end at {off}")
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes, off: int) -> int:
    if off + 4 > len(data):
        raise Short(f"u32 past end at {off}")
    return struct.unpack_from("<I", data, off)[0]


def walk_models(data: bytes, cursor: int, count: int) -> tuple[int, list]:
    """FUN_00424db0: headers first, then each model's arrays in header order."""
    base = cursor
    cursor += count * MODEL_HEADER
    models = []
    for i in range(count):
        rec = base + i * MODEL_HEADER
        extra = u16(data, rec + 0x82)
        verts = u16(data, rec + 0x6C)
        tris = u16(data, rec + 0x6E)
        faces = u16(data, rec + 0x78) + u16(data, rec + 0x7A)
        cursor += extra * 2
        cursor += verts * VERTEX_SIZE
        cursor += tris * TRIANGLE_SIZE
        cursor += faces * FACE_SIZE
        models.append((verts, tris, faces, extra))
        if cursor > len(data):
            raise Short(f"model {i} ran past the end at {cursor}")
    return cursor, models


def walk_anims(data: bytes, cursor: int, count: int) -> int:
    """FUN_00424ef0."""
    base = cursor
    cursor += count * ANIM_HEADER
    for i in range(count):
        rec = base + i * ANIM_HEADER
        n0 = u32(data, rec + 0x00)
        n2 = u32(data, rec + 0x08)
        gate_0c = u32(data, rec + 0x0C)
        gate_10 = u32(data, rec + 0x10)
        n3 = u32(data, rec + 0x18)

        cursor += n0 * 4
        if gate_0c == 0:
            cursor += n2 * 8
        if gate_10 != 0:
            cursor += n2 * 4
        cursor += n2 * 4

        for j in range(n2):
            if gate_10 != 0:
                # the first item is sized differently from the rest
                if j == 0:
                    cursor += gate_10 * 8
                elif gate_10 & 1:
                    cursor += gate_10 * 2 + 2
                else:
                    cursor += gate_10 * 2
            cursor += n3 * 0x40
        if cursor > len(data):
            raise Short(f"anim {i} ran past the end at {cursor}")
    return cursor


def walk(data: bytes, flag_100: bool) -> dict:
    n_models = u32(data, 0)
    cursor = 4
    # The main loop calls the helper once per model, so each header is followed by its
    # own arrays. Only the third section passes a batch and gets contiguous headers.
    models = []
    for _ in range(n_models):
        cursor, got = walk_models(data, cursor, 1)
        models += got
    after_models = cursor

    n_anims = u32(data, cursor)
    cursor += 4
    for _ in range(n_anims):  # likewise one call per record
        cursor = walk_anims(data, cursor, 1)
    after_anims = cursor

    groups = 0
    if flag_100:
        groups = u32(data, cursor)
        cursor += 4
        for _ in range(groups):
            head = cursor
            n = u32(data, head + 4)
            cursor = head + 16
            if n & 0x1FFFFFFF:
                cursor += n * 8
            cursor, _ = walk_models(data, cursor, n)

    return dict(n_models=n_models, n_anims=n_anims, groups=groups,
                after_models=after_models, after_anims=after_anims,
                end=cursor, left=len(data) - cursor, models=models)


NAME_RE = re.compile(rb"[A-Za-z][A-Za-z0-9 _\-\.]{3,40}\x00")


def object_names(wad: Wad) -> list[str]:
    """The object and script names in the STPC tail.

    The tail is not decoded, but the names inside it are plain nul-terminated strings
    padded with 0xcd, so they can be pulled out without understanding the records.
    """
    data = wad.get("STPC").payload
    try:
        flags = struct.unpack("<I", wad.get("WFPC").payload)[0]
        start = walk(data, bool(flags & 0x100))["end"]
    except (Short, struct.error, KeyError):
        start = 0
    return [m.group()[:-1].decode("latin1") for m in NAME_RE.finditer(data[start:])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level", nargs="?")
    ap.add_argument("--names", action="store_true",
                    help="list the object and script names in the STPC tail")
    args = ap.parse_args()

    if args.names:
        paths = ([PRISTINE_WADS / f"{args.level}.wad"] if args.level
                 else sorted(PRISTINE_WADS.glob("*.wad")))
        for path in paths:
            wad = Wad.read(path)
            if not wad.is_level:
                continue
            names = object_names(wad)
            print(f"{path.stem}: {len(names)} names")
            for name in names:
                print(f"   {name}")
        return 0

    paths = ([PRISTINE_WADS / f"{args.level}.wad"] if args.level
             else sorted(PRISTINE_WADS.glob("*.wad")))
    exact = failed = 0
    problems = []

    for path in paths:
        wad = Wad.read(path)
        if not wad.is_level:
            continue
        try:
            data = wad.get("STPC").payload
            flags = struct.unpack("<I", wad.get("WFPC").payload)[0]
        except Exception:
            continue
        try:
            out = walk(data, bool(flags & 0x100))
        except (Short, struct.error) as exc:
            failed += 1
            problems.append((path.stem, str(exc)))
            continue
        if out["left"] == 0:
            exact += 1
            if args.level:
                verts = sum(m[0] for m in out["models"])
                tris = sum(m[1] for m in out["models"])
                print(f"{path.stem}: STPC {len(data):,} bytes consumed exactly")
                print(f"   models      {out['n_models']}")
                print(f"   animations  {out['n_anims']}")
                print(f"   groups      {out['groups']}")
                print(f"   vertices    {verts:,}")
                print(f"   triangles   {tris:,}")
                print(f"   models end at {out['after_models']:,}, "
                      f"anims end at {out['after_anims']:,}")
        else:
            failed += 1
            problems.append((path.stem, f"{out['left']:+,} bytes left"))

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
