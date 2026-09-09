"""LGHT chunk: the level's light list.

Layout is a uint32 count followed by variable-length entries. Each entry starts with
a type byte and three colour bytes; type 1 (directional) then carries a direction
vector, type 2 (positional) a longer payload ending in a trailing byte. Any other
type carries nothing further.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

DIRECTIONAL = 1
POSITIONAL = 2


@dataclass
class Light:
    type: int
    colour: tuple[int, int, int]
    values: tuple[float, ...] = ()
    tail: int | None = None

    def to_bytes(self) -> bytes:
        out = bytes((self.type, *self.colour))
        out += struct.pack(f"<{len(self.values)}f", *self.values)
        if self.tail is not None:
            out += bytes((self.tail,))
        return out


@dataclass
class LightChunk:
    lights: list[Light]
    trailing: bytes = b""

    @classmethod
    def parse(cls, data: bytes) -> "LightChunk":
        count = struct.unpack_from("<I", data, 0)[0]
        pos = 4
        lights = []
        for _ in range(count):
            ltype = data[pos]
            colour = (data[pos + 1], data[pos + 2], data[pos + 3])
            pos += 4
            nfloats = {DIRECTIONAL: 3, POSITIONAL: 5}.get(ltype, 0)
            values = struct.unpack_from(f"<{nfloats}f", data, pos) if nfloats else ()
            pos += 4 * nfloats
            tail = None
            if ltype == POSITIONAL:
                tail = data[pos]
                pos += 1
            lights.append(Light(ltype, colour, values, tail))
        return cls(lights, data[pos:])

    def to_bytes(self) -> bytes:
        out = struct.pack("<I", len(self.lights))
        out += b"".join(light.to_bytes() for light in self.lights)
        return out + self.trailing
