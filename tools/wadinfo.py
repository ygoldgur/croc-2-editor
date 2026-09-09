#!/usr/bin/env python3
"""Dump the top-level chunk layout of a Croc 2 PC .wad file."""
import struct
import sys
from pathlib import Path

KNOWN = {
    "INFO", "VERS", "WFPC", "SMPC", "AMPC", "TEXT", "SPRT",
    "FONT", "RIMG", "TRAK", "STPC", "MAP ", "LGHT", "LGPC", "END ",
}


def chunks(data: bytes):
    """Yield (name, declared_size, payload_offset) walking the RIFF-like chunk list.

    Chunk ids are stored reversed; declared sizes are unreliable, so a chunk's real
    extent is taken as the distance to the next recognisable id.
    """
    pos = 4  # skip the leading file-size uint32
    while pos + 8 <= len(data):
        name = data[pos:pos + 4][::-1].decode("latin-1")
        size = struct.unpack_from("<I", data, pos + 4)[0]
        yield name, size, pos + 8
        if name == "END ":
            break
        pos = next_chunk_start(data, pos + 8, size)
        if pos is None:
            break


def next_chunk_start(data: bytes, payload_start: int, declared_size: int):
    guess = payload_start + declared_size
    if is_chunk_id(data, guess):
        return guess
    # Declared sizes are occasionally a few bytes off; scan for the next valid id.
    for probe in range(max(payload_start, guess - 4096), min(len(data) - 8, guess + 4096)):
        if is_chunk_id(data, probe):
            return probe
    return None


def is_chunk_id(data: bytes, off: int) -> bool:
    if off < 0 or off + 8 > len(data):
        return False
    return data[off:off + 4][::-1].decode("latin-1", "replace") in KNOWN


def main(paths):
    for p in paths:
        data = Path(p).read_bytes()
        declared = struct.unpack_from("<I", data, 0)[0]
        print(f"\n=== {Path(p).name}  file={len(data)}  header_size={declared} "
              f"(delta {len(data) - 4 - declared})")
        prev = None
        for name, size, payload in chunks(data):
            if prev is not None:
                pname, psize, ppayload = prev
                actual = payload - 8 - ppayload
                flag = "" if actual == psize else f"  <-- actual {actual}"
                print(f"  {pname!r:8} off={ppayload - 8:>9} size={psize:>9}{flag}")
            prev = (name, size, payload)
        if prev:
            pname, psize, ppayload = prev
            print(f"  {pname!r:8} off={ppayload - 8:>9} size={psize:>9}  "
                  f"<-- actual {len(data) - ppayload}")


if __name__ == "__main__":
    main(sys.argv[1:])
