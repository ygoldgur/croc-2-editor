"""Reader/writer for Croc 2 PC .wad containers.

A WAD is a 4-byte size field followed by a sequence of RIFF-like chunks. Chunk ids
are stored reversed ("PMAM" -> "MAP "), and the declared payload size is unreliable:
the engine ignores it for most chunk types, so several shipped files disagree with it
by a few bytes. Parsing therefore partitions the file exhaustively and keeps the
declared size alongside the real payload, which makes re-serialization byte-exact.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

LEVEL_CHUNK_IDS = (
    b"INFO", b"VERS", b"WFPC", b"SMPC", b"AMPC", b"TEXT", b"SPRT",
    b"FONT", b"RIMG", b"TRAK", b"STPC", b"MAP ", b"LGHT", b"LGPC", b"END ",
)

# How far from the declared end we are willing to hunt for the next chunk id.
_RESYNC_WINDOW = 0x10000


@dataclass
class Chunk:
    id: bytes           # forward-facing id, e.g. b"MAP "
    declared_size: int  # size as written in the file, preserved verbatim
    payload: bytes
    offset: int = 0     # offset of the chunk header within the file

    @property
    def name(self) -> str:
        return self.id.decode("latin-1")

    @property
    def size_is_wrong(self) -> bool:
        return self.declared_size != len(self.payload)

    def to_bytes(self) -> bytes:
        return self.id[::-1] + struct.pack("<I", self.declared_size) + self.payload


@dataclass
class Wad:
    path: Path | None
    header: bytes                      # the leading size field, kept verbatim
    chunks: list[Chunk] = field(default_factory=list)
    trailing: bytes = b""              # anything after the last chunk
    raw: bytes | None = None           # set for files that aren't chunk containers

    @classmethod
    def from_bytes(cls, data: bytes, path: Path | None = None) -> "Wad":
        if not _is_chunk_id(data, 4):
            return cls(path=path, header=b"", raw=data)

        wad = cls(path=path, header=data[:4])
        pos = 4
        while pos + 8 <= len(data):
            cid = data[pos:pos + 4][::-1]
            declared = struct.unpack_from("<I", data, pos + 4)[0]
            payload_start = pos + 8
            if cid == b"END ":
                end = len(data)
            else:
                end = _find_next_chunk(data, payload_start, declared)
                if end is None:
                    end = len(data)
            wad.chunks.append(Chunk(cid, declared, data[payload_start:end], pos))
            pos = end
            if cid == b"END ":
                break
        wad.trailing = data[pos:]
        return wad

    @classmethod
    def read(cls, path: str | Path) -> "Wad":
        path = Path(path)
        return cls.from_bytes(path.read_bytes(), path)

    def to_bytes(self) -> bytes:
        if self.raw is not None:
            return self.raw
        return self.header + b"".join(c.to_bytes() for c in self.chunks) + self.trailing

    def write(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())

    def get(self, name: str) -> Chunk | None:
        want = name.encode("latin-1").ljust(4)
        return next((c for c in self.chunks if c.id == want), None)

    @property
    def is_level(self) -> bool:
        return self.get("MAP") is not None and self.get("TRAK") is not None


def _is_chunk_id(data: bytes, off: int) -> bool:
    return 0 <= off and off + 8 <= len(data) and data[off:off + 4][::-1] in LEVEL_CHUNK_IDS


def _find_next_chunk(data: bytes, payload_start: int, declared: int) -> int | None:
    """Locate the next chunk header, tolerating a slightly wrong declared size."""
    guess = payload_start + declared
    if _is_chunk_id(data, guess):
        return guess
    # Search outwards from the declared end so the closest candidate wins; a stray
    # matching id deep inside a payload should never beat the real boundary.
    for delta in range(1, _RESYNC_WINDOW):
        for cand in (guess + delta, guess - delta):
            if cand >= payload_start and _is_chunk_id(data, cand):
                return cand
    return None
