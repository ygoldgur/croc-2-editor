"""Start Blender with the Croc 2 editor loaded, optionally opening a level.

    /Applications/Blender.app/Contents/MacOS/Blender --python blender/start.py -- t1l1m001

The level is read from the game's copy (``game/cd/Wads``), so earlier saves carry over.
"""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import croc2_editor  # noqa: E402

croc2_editor.register()

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if args:
    for obj in list(bpy.data.objects):  # the default cube, camera and light
        bpy.data.objects.remove(obj)
    path = croc2_editor.GAME_WADS / f"{args[0]}.wad"
    counts = croc2_editor.import_level(bpy.context, path)
    print(f"croc2: {path.name}: {counts}")

    def frame_when_ready():
        # The window does not exist yet while --python scripts run.
        if not bpy.context.window_manager.windows:
            return 0.2
        croc2_editor.set_up_views(bpy.context)
        return None

    bpy.app.timers.register(frame_when_ready, first_interval=0.5)
