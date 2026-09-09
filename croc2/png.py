"""Minimal PNG writer, so exporting textures needs nothing outside the stdlib."""
from __future__ import annotations

import struct
import zlib


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def write_png(path, rgb: bytes, width: int, height: int) -> None:
    """Write 8-bit RGB pixels, given row-major with no padding."""
    if len(rgb) != width * height * 3:
        raise ValueError(f"expected {width * height * 3} bytes, got {len(rgb)}")
    stride = width * 3
    # Each scanline is prefixed with its filter type; 0 means "store as-is".
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    header = struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(_chunk(b"IHDR", header))
        fh.write(_chunk(b"IDAT", zlib.compress(raw, 6)))
        fh.write(_chunk(b"IEND", b""))
