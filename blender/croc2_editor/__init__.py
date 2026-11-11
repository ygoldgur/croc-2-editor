"""Croc 2 level editor: move terrain tiles and actors, and save into the game's WAD.

Import a level WAD, move or turn tiles and actors, and save. Saving rewrites only
transforms, in place, and reads the result back before writing it (``croc2.editing``).
Tiles turn in quarter turns only, which is all the shipped levels use.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import bpy  # noqa: E402
from bpy.props import StringProperty  # noqa: E402
from bpy_extras.io_utils import ImportHelper  # noqa: E402

from croc2.level import Level  # noqa: E402
from croc2.paths import GAME_WADS, PRISTINE_WADS  # noqa: E402

from . import build, export  # noqa: E402

bl_info = {
    "name": "Croc 2 level editor",
    "author": "Argonaut project",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "File > Import > Croc 2 level; View3D > Sidebar > Croc 2",
    "description": "Move Croc 2 terrain tiles and actors and save them into the game",
    "category": "Import-Export",
}


def import_level(context, path: Path) -> dict[str, int]:
    level = Level.load(path)
    if level.map.instances_at < 0:
        raise ValueError(f"{path.name}: the MAP walk fails here, so it cannot be edited")
    counts = build.build(level, str(path))
    set_up_views(context)
    return counts


def set_up_views(context) -> None:
    """Textured shading, and the view framed on the level.

    Blender zooms toward the view's pivot and stalls on reaching it, so leaving the
    pivot at the origin - a corner of the level - stops zooming in anywhere useful.
    """
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            space.shading.type = "SOLID"
            space.shading.color_type = "TEXTURE"
            space.clip_start = 0.01
            space.clip_end = 1000.0
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is not None:
                with context.temp_override(window=window, area=area, region=region):
                    bpy.ops.view3d.view_all()


class CROC2_OT_import(bpy.types.Operator, ImportHelper):
    """Import a Croc 2 level WAD: its terrain tiles and its actors"""
    bl_idname = "croc2.import_level"
    bl_label = "Import Croc 2 Level"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".wad"
    filter_glob: StringProperty(default="*.wad", options={"HIDDEN"})

    def invoke(self, context, event):
        self.filepath = str(GAME_WADS) + "/"
        return super().invoke(context, event)

    def execute(self, context):
        try:
            counts = import_level(context, Path(self.filepath))
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{counts['tiles']} tiles, {counts['instances']} actors "
                              f"({counts['drawn']} with a mesh)")
        return {"FINISHED"}


class CROC2_OT_save(bpy.types.Operator):
    """Write the moved tiles and actors into the WAD the game loads"""
    bl_idname = "croc2.save_level"
    bl_label = "Save to Game"

    @classmethod
    def poll(cls, context):
        return "croc2_source" in context.scene

    def execute(self, context):
        scene = context.scene
        target = GAME_WADS / f"{scene['croc2_level']}.wad"
        try:
            result = export.export(scene, target)
        except Exception as exc:
            self.report({"ERROR"}, f"not saved: {exc}")
            return {"CANCELLED"}
        export.snap_tiles(scene)
        self.report({"INFO"}, f"saved {target.name}: {len(result.tiles_moved)} tiles and "
                              f"{len(result.instances_moved)} actors differ from the "
                              f"imported file, {result.bytes_changed} bytes")
        return {"FINISHED"}


class CROC2_OT_revert(bpy.types.Operator):
    """Put the untouched copy of this level back where the game loads it"""
    bl_idname = "croc2.revert_level"
    bl_label = "Revert Game Copy to Original"

    @classmethod
    def poll(cls, context):
        return "croc2_level" in context.scene

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        name = f"{context.scene['croc2_level']}.wad"
        (GAME_WADS / name).write_bytes((PRISTINE_WADS / name).read_bytes())
        self.report({"INFO"}, f"restored {name}; re-import to see it")
        return {"FINISHED"}


class CROC2_PT_panel(bpy.types.Panel):
    bl_label = "Croc 2"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Croc 2"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.operator(CROC2_OT_import.bl_idname, icon="IMPORT")
        if "croc2_level" not in scene:
            return
        layout.label(text=f"Level {scene['croc2_level']}")
        layout.label(text=f"From {Path(scene['croc2_source']).parent.name}/"
                          f"{Path(scene['croc2_source']).name}")
        layout.operator(CROC2_OT_save.bl_idname, icon="FILE_TICK")
        layout.operator(CROC2_OT_revert.bl_idname, icon="LOOP_BACK")

        obj = context.active_object
        if obj is None or build.KIND not in obj:
            return
        box = layout.box()
        if obj[build.KIND] == "tile":
            box.label(text=f"Tile {obj[build.INDEX]}")
            box.label(text="Saved in quarter turns")
        else:
            box.label(text=f"Actor instance {obj[build.INDEX]}")
            box.label(text=obj.get("croc2_actor", ""))
            if "croc2_model" in obj:
                box.label(text=f"Model {obj['croc2_model']}")
        box.label(text="Only moves and turns are saved")


def _menu_import(self, context):
    self.layout.operator(CROC2_OT_import.bl_idname, text="Croc 2 level (.wad)")


CLASSES = (CROC2_OT_import, CROC2_OT_save, CROC2_OT_revert, CROC2_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
