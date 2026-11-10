"""Assemble a whole level: place TRAK tile meshes into the world using MAP records."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path

from .chunks import LightChunk, MapChunk, TextChunk, TrakChunk
from .chunks.stpc import GROUPS_FLAG, Actor, StpcChunk, StpcParseError, pose
from .chunks.trak import rect_corners
from .png import write_png
from .wad import Wad

FULL_TURN = 0x1000  # rotation field is 12-bit; 0x400 is a quarter turn


@dataclass
class Level:
    name: str
    map: MapChunk
    trak: TrakChunk
    lights: LightChunk
    text: TextChunk
    stpc: StpcChunk | None = None
    stpc_payload: bytes | None = None

    @classmethod
    def load(cls, path) -> "Level":
        wad = Wad.read(path)
        if not wad.is_level:
            raise ValueError(f"{path} is not a level wad")
        trak = TrakChunk.parse(wad.get("TRAK").payload)
        wfpc_chunk = wad.get("WFPC")
        wfpc = struct.unpack("<I", wfpc_chunk.payload)[0] if wfpc_chunk else 0
        return cls(
            name=wad.path.stem if wad.path else "level",
            # The actor instances sit deep in the MAP tail, past a section sized by
            # the TRAK vertex counts and behind WFPC's section gates.
            map=MapChunk.parse(wad.get("MAP").payload,
                               [len(m.vertices) for m in trak.models], wfpc),
            trak=trak,
            lights=LightChunk.parse(wad.get("LGHT").payload),
            text=TextChunk.parse(wad.get("TEXT").payload),
            stpc=cls._stpc(wad, wfpc),
            stpc_payload=wad.get("STPC").payload if wad.get("STPC") else None,
        )

    @staticmethod
    def _stpc(wad: Wad, wfpc: int) -> StpcChunk | None:
        chunk = wad.get("STPC")
        if chunk is None:
            return None
        try:
            return StpcChunk.parse(chunk.payload, bool(wfpc & GROUPS_FLAG))
        except (StpcParseError, struct.error):
            return None  # the two demo maps, which fail every chunk walk

    def actor_for(self, instance) -> Actor | None:
        """The actor definition an instance points at, if STPC could be walked."""
        if self.stpc is None:
            return None
        return self.stpc.actor_at(instance.actor_offset)

    def actor_name(self, instance) -> str:
        actor = self.actor_for(instance)
        return actor.name if actor else f"@{instance.actor_offset}"

    def model_index_for(self, instance) -> int | None:
        """The STPC model an instance most likely uses; see ``StpcChunk.instance_model``."""
        if self.stpc is None:
            return None
        return self.stpc.instance_model(instance.actor_offset)

    def animations_for(self, instance) -> list[int]:
        """The animations an instance's actor pushes that fit its model."""
        if self.stpc is None:
            return []
        return self.stpc.instance_animations(self.stpc_payload, instance.actor_offset)

    def animation_index_for(self, instance) -> int | None:
        """The first fitting animation the actor pushes; not known to be the first played."""
        anims = self.animations_for(instance)
        return anims[0] if anims else None

    def posed_model(self, model_index: int, anim_index: int | None = None, frame: int = 0):
        """A model's mesh and its vertices, posed by one animation frame if articulated.

        Returns ``(model, positions, soft)``, where ``soft`` is the set of vertices left
        unplaced because their per-frame data is not decoded; faces using them are wrong.
        """
        model = self.stpc.model(self.stpc_payload, model_index)
        parts = self.stpc.vertex_parts(self.stpc_payload, model_index)
        soft = {i for i, p in enumerate(parts) if p < 0}
        if anim_index is None or not self.stpc.models[model_index].extra_count:
            return model, list(model.vertices), soft
        frames = self.stpc.animation_matrices(self.stpc_payload, anim_index)
        return model, pose(model.vertices, parts, frames[frame % len(frames)]), soft

    def place(self, tile):
        """The tile's model vertices, rotated about Y and moved into world space.

        The turn is anticlockwise seen from above. Taking it the other way leaves
        0 and 180 degree tiles alone but swaps 90 with 270, which tears the terrain:
        summed over every level, the wrong sign drops the number of vertices shared
        between neighbouring tiles from 220042 to 160916.
        """
        model = self.trak.models[tile.model_id]
        angle = tile.rotation / FULL_TURN * math.tau
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        return [(tile.x + vx * cos_a - vz * sin_a,
                 tile.y + vy,
                 tile.z + vx * sin_a + vz * cos_a)
                for vx, vy, vz in model.vertices]

    def world_faces(self):
        """Return (positions, faces) with faces placed and rotated into the world.

        Each face is (corner_indices, texture_id). Corner *i* maps to corner *i* of
        the texture rectangle, which is how the game derives UVs - none are stored.
        """
        positions: list[tuple[float, float, float]] = []
        faces: list[tuple[tuple[int, ...], int]] = []
        for tile in self.map.tiles:
            model = self.trak.models[tile.model_id]
            base = len(positions)
            positions.extend(self.place(tile))
            for face in model.faces:
                faces.append((tuple(base + i for i in face.indices), face.texture_id))
        return positions, faces

    def face_uvs(self, texture_id: int):
        """The texture rectangle's corners, in the winding order terrain faces use."""
        return rect_corners(self.text.entries[texture_id])

    def write_textures(self, directory, stem: str) -> list[str]:
        """Dump the texture pages as PNGs and return their filenames."""
        directory = Path(directory)
        names = []
        for i, tex in enumerate(self.text.textures):
            name = f"{stem}_page{i:02d}.png"
            rgb = bytes(tex.to_rgb())
            if len(rgb) == tex.width * tex.height * 3:
                write_png(directory / name, rgb, tex.width, tex.height)
                names.append(name)
            else:
                names.append("")
        return names

    def texture_colours(self) -> list[tuple[float, float, float]]:
        """Average colour of each TEXT entry, for previewing without real UVs."""
        pages = []
        for tex in self.text.textures:
            flat = bytes(tex.to_rgb())
            expected = tex.width * tex.height * 3
            pages.append(flat if len(flat) == expected else None)

        colours = []
        for entry in self.text.entries:
            page = pages[entry.page] if entry.has_texture else None
            if page is None:
                colours.append((0.0, 0.0, 0.0))  # untextured face
                continue
            width = self.text.textures[entry.page].width
            total = [0, 0, 0]
            count = 0
            for v in range(entry.v0, max(entry.v1, entry.v0 + 1)):
                row = (v * width + entry.u0) * 3
                for u in range(max(entry.u1 - entry.u0, 1)):
                    total[0] += page[row + u * 3]
                    total[1] += page[row + u * 3 + 1]
                    total[2] += page[row + u * 3 + 2]
                    count += 1
            colours.append(tuple(c / count / 255.0 for c in total) if count
                           else (1.0, 0.0, 1.0))
        return colours

    def world_mesh(self):
        """Return (positions, triangles, texture_ids) for the level in world space.

        Texture ids run parallel to triangles and index the TEXT entry table.
        """
        positions: list[tuple[float, float, float]] = []
        triangles: list[tuple[int, int, int]] = []
        texture_ids: list[int] = []
        for tile in self.map.tiles:
            model = self.trak.models[tile.model_id]
            base = len(positions)
            positions.extend(self.place(tile))
            triangles.extend((base + a, base + b, base + c)
                             for a, b, c in model.triangles)
            texture_ids.extend(model.texture_ids)
        return positions, triangles, texture_ids

    def bounds(self):
        positions, _, _ = self.world_mesh()
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        zs = [p[2] for p in positions]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def write_obj(self, path) -> tuple[int, int]:
        """Write a textured OBJ, plus its MTL and the texture pages beside it."""
        path = Path(path)
        positions, faces = self.world_faces()
        pages = self.write_textures(path.parent, path.stem)
        mtl_path = path.with_suffix(".mtl")

        with open(mtl_path, "w") as fh:
            for i, name in enumerate(pages):
                fh.write(f"newmtl page{i:02d}\nKd 1 1 1\n")
                if name:
                    fh.write(f"map_Kd {name}\n")
                fh.write("\n")

        written = 0
        with open(path, "w") as fh:
            fh.write(f"# Croc 2 level {self.name}\n")
            fh.write(f"mtllib {mtl_path.name}\n")
            for x, y, z in positions:
                fh.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

            # UVs are per-face, so emit a vt for every corner as we go.
            uv_lines: list[str] = []
            face_lines: list[str] = []
            current_page = None
            for indices, texture_id in faces:
                if texture_id >= len(self.text.entries):
                    continue
                entry = self.text.entries[texture_id]
                if not entry.has_texture:
                    continue
                tex = self.text.textures[entry.page]
                if entry.page != current_page:
                    face_lines.append(f"usemtl page{entry.page:02d}")
                    current_page = entry.page
                corners = self.face_uvs(texture_id)[:len(indices)]
                first = len(uv_lines) + 1
                for u, v in corners:
                    # OBJ puts the texture origin at bottom-left; pages are top-down.
                    uv_lines.append(f"vt {u / tex.width:.6f} {1.0 - v / tex.height:.6f}")
                face_lines.append("f " + " ".join(
                    f"{p + 1}/{first + k}" for k, p in enumerate(indices)))
                written += 1

            fh.write("\n".join(uv_lines) + "\n")
            fh.write("\n".join(face_lines) + "\n")
        return len(positions), written
