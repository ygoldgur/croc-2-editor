#!/usr/bin/env python3
"""Render the object models out of a level's STPC chunk.

The models are in the terrain mesh format with a header eight bytes longer, so the TRAK
decoder reads them once it is told where each array sits and that the triangle's texture
field is a uint16 rather than a uint32. Both index the same TEXT entry table, so objects
can be textured with exactly the terrain machinery.

Models split into two kinds, and the split is visible in the data before you render
anything:

  * No extra uint16 array at +0x82. These decode completely - across the game, every
    face normal matches the geometry it belongs to and every header bounding box matches
    the vertices exactly. Rendering them is honest.
  * With an extra array. These are articulated: their stored vertex positions do not fill
    the header's bounding box, and computed normals disagree with the stored ones, which
    is what happens when parts still need a transform applied. Rendered raw they come out
    with their pieces heaped at the origin, so this tool skips them unless asked.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from croc2 import Wad  # noqa: E402
from croc2.chunks import TextChunk  # noqa: E402
from croc2.chunks.stpc import GROUPS_FLAG, StpcChunk  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402
from tools.render3d import rasterise  # noqa: E402
from tools.render_level import _page_images  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "shots"


def load(stem: str):
    wad = Wad.read(PRISTINE_WADS / f"{stem}.wad")
    payload = wad.get("STPC").payload
    wfpc = struct.unpack("<I", wad.get("WFPC").payload)[0]
    stpc = StpcChunk.parse(payload, bool(wfpc & GROUPS_FLAG))
    text = TextChunk.parse(wad.get("TEXT").payload)
    return payload, stpc, text


def shoot(model, pages, entries, yaw: float, pitch: float, size: int, cull: bool):
    """Photograph one model from outside its own bounding box."""
    faces = [(f.indices, f.texture_id) for f in model.faces]
    lo = np.asarray(model.bbox_min, float)
    hi = np.asarray(model.bbox_max, float)
    centre = (lo + hi) / 2
    span = float(max(hi - lo)) or 1.0
    img, drawn = rasterise(model.vertices, faces, pages, entries, centre,
                           yaw, pitch, span * 2.0, size, 45.0, cull=cull)
    return img, drawn, span


def textured_faces(model, entries) -> int:
    """Faces that select a real texture rectangle rather than the 0xFF no-page marker."""
    return sum(1 for f in model.faces
               if f.texture_id < len(entries) and entries[f.texture_id].has_texture)


def label(model, ref, entries) -> str:
    span = max(model.bbox_max[k] - model.bbox_min[k] for k in range(3))
    notes = []
    if ref.extra_count:
        notes.append("articulated")
    if model.faces and not textured_faces(model, entries):
        notes.append("collision only")
    return (f"{model.index}: {len(model.vertices)}v {len(model.triangles)}t "
            f"{len(model.faces)}f  {span:.2f} tiles"
            + ("  " + ", ".join(notes) if notes else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level", nargs="?", default="t1l1m001")
    ap.add_argument("--list", action="store_true", help="describe the models and stop")
    ap.add_argument("--model", type=int, help="render just this one, large")
    ap.add_argument("--count", type=int, default=12,
                    help="how many models to put in the montage")
    ap.add_argument("--articulated", action="store_true",
                    help="include the models whose parts need transforms we lack")
    ap.add_argument("--yaw", type=float, default=35.0)
    ap.add_argument("--pitch", type=float, default=20.0)
    ap.add_argument("--size", type=int, default=320)
    ap.add_argument("--no-cull", action="store_true")
    ap.add_argument("--obj", action="store_true", help="also write the model as OBJ")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    payload, stpc, text = load(args.level)
    pages = _page_images(text)
    entries = text.entries

    decoded = [stpc.model(payload, i) for i in range(len(stpc.models))]
    articulated = sum(1 for r in stpc.models if r.extra_count)
    collision = sum(1 for m in decoded if m.faces and not textured_faces(m, entries))
    # A model with nothing but untextured faces would come out as a black square.
    usable = [i for i, r in enumerate(stpc.models)
              if (args.articulated or r.extra_count == 0)
              and textured_faces(decoded[i], entries)]
    print(f"{args.level}: {len(stpc.models)} object models, {len(usable)} drawable here "
          f"({articulated} articulated, {collision} collision only)")

    if args.list:
        for i, ref in enumerate(stpc.models):
            print("   " + label(decoded[i], ref, entries))
        return 0

    if args.model is not None:
        model = decoded[args.model]
        img, drawn, span = shoot(model, pages, entries, args.yaw, args.pitch,
                                 args.size * 3, not args.no_cull)
        path = OUT / f"{args.level}_model{args.model:03d}.png"
        plt.imsave(path, np.clip(img / 255.0, 0, 1))
        print(f"  {label(model, stpc.models[args.model], entries)}")
        print(f"  drew {drawn} faces, wrote {path.name}")
        if args.obj:
            write_obj(OUT / f"{args.level}_model{args.model:03d}.obj", model)
        return 0

    # Biggest first, since the interesting props are the large ones.
    ranked = sorted(usable, key=lambda i: -stpc.models[i].triangle_count)
    chosen = ranked[:args.count]
    cols = min(4, len(chosen)) or 1
    rows = (len(chosen) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.2, rows * 3.4), dpi=110)
    axes = np.atleast_1d(axes).ravel()

    for ax, i in zip(axes, chosen):
        model = decoded[i]
        img, drawn, span = shoot(model, pages, entries, args.yaw, args.pitch,
                                 args.size, not args.no_cull)
        ax.imshow(np.clip(img / 255.0, 0, 1))
        ax.set_title(label(model, stpc.models[i], entries), fontsize=7)
        print(f"   {label(model, stpc.models[i], entries)}  ({drawn} faces drawn)")
    for ax in axes:
        ax.set_axis_off()

    fig.suptitle(f"{args.level} - STPC object models", fontsize=11)
    fig.tight_layout()
    path = OUT / f"{args.level}_objects.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {path.name}")
    return 0


def write_obj(path: Path, model) -> None:
    with open(path, "w") as fh:
        fh.write(f"# Croc 2 STPC object model {model.index}\n")
        for x, y, z in model.vertices:
            fh.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        for nx, ny, nz in model.normals:
            fh.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")
        for face in model.faces:
            fh.write("f " + " ".join(f"{i + 1}//{i + 1}" for i in face.indices) + "\n")
    print(f"  wrote {path.name}")


if __name__ == "__main__":
    raise SystemExit(main())
