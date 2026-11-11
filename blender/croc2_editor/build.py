"""Turn a decoded level into Blender objects.

Every tile and every actor instance becomes one object, tagged with ``croc2_kind`` and
``croc2_index`` so the exporter can find its record again. Tiles sharing a TRAK model
share one mesh, and instances sharing a model and pose share one too, so the meshes are
for looking at: only object transforms are written back.
"""
from __future__ import annotations

import bpy

from croc2.chunks.trak import rect_corners
from croc2.level import Level

from . import convert

KIND = "croc2_kind"
INDEX = "croc2_index"
LEVEL = "croc2_level"


def build(level: Level, source: str) -> dict[str, int]:
    scene = bpy.context.scene
    scene[LEVEL] = level.name
    scene["croc2_source"] = source

    root = _collection(f"Croc 2 {level.name}", scene.collection)
    terrain = _collection(f"{level.name} terrain", root)
    actors = _collection(f"{level.name} actors", root)
    materials = Materials(level)

    tile_meshes: dict[int, bpy.types.Mesh] = {}
    for i, tile in enumerate(level.map.tiles):
        mesh = tile_meshes.get(tile.model_id)
        if mesh is None:
            mesh = tile_meshes[tile.model_id] = _tile_mesh(level, tile.model_id, materials)
        obj = bpy.data.objects.new(f"tile {i:04d} m{tile.model_id}", mesh)
        obj.location = convert.to_blender(tile.x, tile.y, tile.z)
        obj.rotation_euler = convert.tile_euler(tile.rotation)
        obj.lock_rotation = (True, True, False)
        obj.lock_scale = (True, True, True)
        obj[KIND], obj[INDEX], obj[LEVEL] = "tile", i, level.name
        terrain.objects.link(obj)

    actor_meshes: dict[tuple, bpy.types.Mesh | None] = {}
    drawn = 0
    for inst in level.map.instances:
        name = f"{inst.index:03d} {level.actor_name(inst)}"
        model = level.model_index_for(inst)
        mesh = None
        if model is not None:
            key = (model, level.animation_index_for(inst))
            if key not in actor_meshes:
                actor_meshes[key] = _actor_mesh(level, *key, materials)
            mesh = actor_meshes[key]
        obj = bpy.data.objects.new(name, mesh)
        if mesh is None:
            obj.empty_display_type = "ARROWS"
            obj.empty_display_size = 0.3
        else:
            drawn += 1
        obj.location = convert.to_blender(*inst.position)
        obj.rotation_mode = convert.ACTOR_ROTATION_MODE
        obj.rotation_euler = convert.actor_euler(inst.rotation)
        obj.lock_scale = (True, True, True)
        obj[KIND], obj[INDEX], obj[LEVEL] = "instance", inst.index, level.name
        obj["croc2_actor"] = level.actor_name(inst)
        if model is not None:
            obj["croc2_model"] = model
        actors.objects.link(obj)

    return {"tiles": len(level.map.tiles), "instances": len(level.map.instances),
            "drawn": drawn}


def _collection(name: str, parent) -> bpy.types.Collection:
    coll = bpy.data.collections.new(name)
    parent.children.link(coll)
    return coll


def _tile_mesh(level: Level, model_id: int, materials: "Materials") -> bpy.types.Mesh:
    model = level.trak.models[model_id]
    faces = [(f.indices, f.texture_id, 0) for f in model.faces]
    return _mesh(f"trak {model_id}", model.vertices, faces, materials)


def _actor_mesh(level: Level, model_index: int, anim_index: int | None,
                materials: "Materials") -> bpy.types.Mesh | None:
    try:
        model, posed, soft = level.posed_model(model_index, anim_index)
    except Exception:
        return None
    # STPC models are mirrored in X. Undoing it reverses the winding, and the texture
    # rectangle then starts two corners round.
    vertices = [(-x, y, z) for x, y, z in posed]
    faces = [(tuple(reversed(f.indices)), f.texture_id, 2) for f in model.faces
             if not soft.intersection(f.indices)]
    if not faces:
        return None
    name = f"stpc {model_index}" + (f" anim {anim_index}" if anim_index is not None else "")
    return _mesh(name, vertices, faces, materials)


def _mesh(name, vertices, faces, materials: "Materials") -> bpy.types.Mesh:
    entries = materials.entries
    faces = [f for f in faces if 0 <= f[1] < len(entries)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([convert.to_blender(*v) for v in vertices], [],
                     [tuple(f[0]) for f in faces])
    uv = mesh.uv_layers.new(name="UV")
    slots: dict[str, int] = {}
    indices = []
    for poly, (corners, texture_id, uv_offset) in zip(mesh.polygons, faces):
        entry = entries[texture_id]
        mat = materials.for_entry(entry)
        if mat.name not in slots:
            slots[mat.name] = len(mesh.materials)
            mesh.materials.append(mat)
        indices.append(slots[mat.name])
        if entry.has_texture:
            w, h = materials.page_size(entry.page)
            rect = rect_corners(entry, uv_offset)
            for loop, (u, v) in zip(poly.loop_indices, rect):
                uv.data[loop].uv = (u / w, 1.0 - v / h)
    mesh.polygons.foreach_set("material_index", indices)
    mesh.update()
    return mesh


def _material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:  # before Blender 5, node trees were opt-in
        mat.use_nodes = True
    return mat


class Materials:
    """One material per texture page, and one per flat colour."""

    def __init__(self, level: Level):
        self.level = level
        self.entries = level.text.entries
        self._pages: dict[int, bpy.types.Material] = {}
        self._flat: dict[tuple, bpy.types.Material] = {}

    def page_size(self, page: int) -> tuple[int, int]:
        tex = self.level.text.textures[page]
        return tex.width, tex.height

    def for_entry(self, entry) -> bpy.types.Material:
        if entry.has_texture:
            if entry.page not in self._pages:
                self._pages[entry.page] = self._page_material(entry.page)
            return self._pages[entry.page]
        colour = entry.colour
        if colour not in self._flat:
            self._flat[colour] = self._flat_material(colour)
        return self._flat[colour]

    def _page_material(self, page: int) -> bpy.types.Material:
        name = f"{self.level.name} page {page:02d}"
        mat = _material(name)
        image = self._image(page, name)
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
        tex = nodes.new("ShaderNodeTexImage")
        tex.interpolation = "Closest"
        tex.location = (-350, 300)
        if image is not None:
            tex.image = image
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 1.0
        nodes.active = tex
        return mat

    def _flat_material(self, colour) -> bpy.types.Material:
        r, g, b = (c / 255.0 for c in colour)
        mat = _material(f"{self.level.name} flat {bytes(colour).hex()}")
        mat.diffuse_color = (r, g, b, 1.0)
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
        bsdf.inputs["Roughness"].default_value = 1.0
        return mat

    def _image(self, page: int, name: str) -> bpy.types.Image | None:
        tex = self.level.text.textures[page]
        try:
            rgb = tex.to_rgb()
        except ValueError:
            return None
        if len(rgb) != tex.width * tex.height * 3:
            return None
        image = bpy.data.images.new(name, tex.width, tex.height)
        # Blender's rows run bottom-up; the page's run top-down.
        row = tex.width * 3
        pixels = []
        for y in range(tex.height - 1, -1, -1):
            line = rgb[y * row:(y + 1) * row]
            for x in range(0, row, 3):
                pixels += (line[x] / 255.0, line[x + 1] / 255.0, line[x + 2] / 255.0, 1.0)
        image.pixels.foreach_set(pixels)
        image.pack()
        return image
