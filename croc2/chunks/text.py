"""TEXT chunk: palettes and textures.

Structure, following Croc2ExplorerWV (whose Read*BE helpers are misnamed - the PC
format is little-endian throughout)::

    uint32 palette_count
    per palette: uint32 unknown, uint32 colour_count, colour_count * 3 bytes RGB
    uint32 texture_count
    per texture: uint32 flags, uint32 width, uint32 height
        flags & 0x80  -> uint32 packed_size, then RLE16-compressed 16-bit pixels
        flags == 0    -> width*height bytes, palette indices
        otherwise     -> width*height*2 bytes, raw 16-bit pixels

16-bit pixels are 5:5:5 packed as r | g<<5 | b<<10.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

NO_PAGE = 0xFF  # texture entry page value meaning "this face has no texture"


@dataclass
class Palette:
    unknown: int
    colours: list[tuple[int, int, int]]


@dataclass
class Texture:
    flags: int
    width: int
    height: int
    data: bytes = b""

    @property
    def is_paletted(self) -> bool:
        return self.flags == 0

    def to_rgb(self, palette: Palette | None = None) -> bytes:
        """Expand to a flat RGB byte buffer."""
        out = bytearray()
        if self.is_paletted:
            if palette is None:
                raise ValueError("paletted texture needs a palette")
            n = len(palette.colours)
            for idx in self.data[:self.width * self.height]:
                out += bytes(palette.colours[idx % n])
        else:
            for (v,) in struct.iter_unpack("<H", self.data[:self.width * self.height * 2]):
                out += bytes(((v & 0x1F) << 3, ((v >> 5) & 0x1F) << 3, ((v >> 10) & 0x1F) << 3))
        return bytes(out)


@dataclass
class TextureEntry:
    """One sub-rectangle carved out of a 256x256 texture page.

    Rectangles form a regular atlas grid - typically 32-pixel cells separated by
    2-pixel gutters. TRAK render triangles select one by its position in this table.

    `page` spans exactly the chunk's page count, or is NO_PAGE for the 1133 entries
    game-wide that carry no texture at all. `marker` is 0xFF throughout.
    """
    flags: int
    unknown: int
    page: int
    marker: int

    @property
    def has_texture(self) -> bool:
        return self.page != NO_PAGE
    u0: int
    u1: int
    v0: int
    v1: int

    @property
    def width(self) -> int:
        return self.u1 - self.u0

    @property
    def height(self) -> int:
        return self.v1 - self.v0


@dataclass
class TextChunk:
    palettes: list[Palette] = field(default_factory=list)
    textures: list[Texture] = field(default_factory=list)
    trailing: bytes = b""

    @property
    def entries(self) -> list[TextureEntry]:
        """Parse the texture table that follows the pages: uint16 count, uint16 pad,
        then count 8-byte entries."""
        count = struct.unpack_from("<H", self.trailing, 0)[0]
        out = []
        for i in range(count):
            off = 4 + i * 8
            if off + 8 > len(self.trailing):
                break
            out.append(TextureEntry(*struct.unpack_from("<8B", self.trailing, off)))
        return out

    @classmethod
    def parse(cls, data: bytes) -> "TextChunk":
        pos = 0
        count = struct.unpack_from("<I", data, pos)[0]; pos += 4
        palettes = []
        for _ in range(count):
            unknown, ncol = struct.unpack_from("<2I", data, pos); pos += 8
            cols = [tuple(data[pos + i * 3: pos + i * 3 + 3]) for i in range(ncol)]
            pos += ncol * 3
            palettes.append(Palette(unknown, cols))

        count = struct.unpack_from("<I", data, pos)[0]; pos += 4
        textures = []
        for _ in range(count):
            flags, w, h = struct.unpack_from("<3I", data, pos); pos += 12
            if flags & 0x80:
                packed = struct.unpack_from("<I", data, pos)[0]; pos += 4
                blob = decompress_rle16(data[pos:pos + packed]); pos += packed
            else:
                size = w * h * (2 if flags else 1)
                blob = data[pos:pos + size]; pos += size
            textures.append(Texture(flags, w, h, blob))
        return cls(palettes, textures, data[pos:])


def decompress_rle16(src: bytes) -> bytes:
    """16-bit RLE: a positive count copies literals, a negative count repeats one value."""
    out = bytearray()
    pos = 0
    end = len(src) - 1
    while pos < end:
        (n,) = struct.unpack_from("<h", src, pos); pos += 2
        if n >= 0:
            out += src[pos:pos + n * 2]; pos += n * 2
        else:
            out += src[pos:pos + 2] * (-n); pos += 2
    return bytes(out)
