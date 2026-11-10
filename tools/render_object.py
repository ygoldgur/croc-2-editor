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
  * With an extra array. These are articulated: the array gives each part's vertex
    count, and the vertices are stored local to their part, so rendered raw the pieces
    heap up at the origin. This tool poses them in the animation frame they were
    exported in - the one that makes every stored face normal agree with its geometry
    again - or, failing that, the first frame of the first animation that fits.

Faces whose texture entry has no page are flat-coloured, and are drawn in that colour.
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
from croc2.chunks.stpc import GROUPS_FLAG, StpcChunk, pose  # noqa: E402
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


def shoot(model, pages, entries, yaw: float, pitch: float, size: int, cull: bool,
          unmirror: bool = True, positions=None, skip=frozenset()):
    """Photograph one model from outside its own bounding box.

    STPC object meshes are stored mirrored in X versus the terrain/world convention.
    Undoing that mirror reverses winding, so face corner order is reversed too.
    ``positions`` replaces the stored vertices, for a posed model; faces touching a
    vertex in ``skip`` are left out.
    """
    vertices = np.asarray(model.vertices if positions is None else positions, float)
    drawable = [f for f in model.faces if not skip.intersection(f.indices)]
    if unmirror:
        # Mirror geometry to world handedness, then reverse winding back to front-facing.
        vertices = vertices.copy()
        vertices[:, 0] *= -1.0
        faces = [(tuple(reversed(f.indices)), f.texture_id) for f in drawable]
        uv_offset = 2
    else:
        faces = [(f.indices, f.texture_id) for f in drawable]
        uv_offset = 0

    used = sorted({i for f in drawable for i in f.indices}) or list(range(len(vertices)))
    lo = vertices[used].min(axis=0)
    hi = vertices[used].max(axis=0)
    centre = (lo + hi) / 2
    span = float(max(hi - lo)) or 1.0
    img, drawn = rasterise(vertices, faces, pages, entries, centre,
                           yaw, pitch, span * 2.0, size, 45.0, cull=cull,
                           uv_offset=uv_offset, flat_colours=True)
    return img, drawn, span


def textured_faces(model, entries) -> int:
    """Faces that select a real texture rectangle rather than the 0xFF no-page marker."""
    return sum(1 for f in model.faces
               if f.texture_id < len(entries) and entries[f.texture_id].has_texture)


def posed(stpc, payload, index: int):
    """``(positions, soft, how)`` for one model: articulated ones in a decoded pose."""
    ref = stpc.models[index]
    parts = stpc.vertex_parts(payload, index)
    soft = frozenset(i for i, p in enumerate(parts) if p < 0)
    if not ref.extra_count:
        return None, soft, ""
    fit = stpc.export_pose(payload, index)
    if fit is not None:
        _, anim, frame = fit
        how = f"anim {anim} frame {frame}, {100 * fit[0]:.0f}% normals"
    else:
        fits = stpc.compatible_animations(payload, index)
        if not fits:
            return None, soft, "no animation fits"
        anim, frame, how = fits[0], 0, f"anim {fits[0]} frame 0"
    matrices = stpc.animation_matrices(payload, anim)[frame]
    model = stpc.model(payload, index)
    return pose(model.vertices, parts, matrices), soft, how


def label(model, ref, entries, how: str = "") -> str:
    span = max(model.bbox_max[k] - model.bbox_min[k] for k in range(3))
    notes = []
    if ref.extra_count:
        notes.append(f"articulated, {how}" if how else "articulated")
    if model.faces and not textured_faces(model, entries):
        notes.append("flat colour only")
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
    ap.add_argument("--raw", action="store_true",
                    help="draw articulated models unposed, with their parts heaped up")
    ap.add_argument("--yaw", type=float, default=35.0)
    ap.add_argument("--pitch", type=float, default=20.0)
    ap.add_argument("--size", type=int, default=320)
    ap.add_argument("--no-cull", action="store_true")
    ap.add_argument("--obj", action="store_true", help="also write the model as OBJ")
    ap.add_argument("--uv-variants", action="store_true",
                    help="compare raw decode vs unmirrored object convention")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    payload, stpc, text = load(args.level)
    pages = _page_images(text)
    entries = text.entries

    decoded = [stpc.model(payload, i) for i in range(len(stpc.models))]
    poses = [(None, frozenset(), "") if args.raw else posed(stpc, payload, i)
             for i in range(len(stpc.models))]
    articulated = sum(1 for r in stpc.models if r.extra_count)
    flat_only = sum(1 for m in decoded if m.faces and not textured_faces(m, entries))
    usable = [i for i, m in enumerate(decoded) if m.faces]
    print(f"{args.level}: {len(stpc.models)} object models, {len(usable)} with faces "
          f"({articulated} articulated, {flat_only} flat colour only)")

    if args.list:
        for i, ref in enumerate(stpc.models):
            print("   " + label(decoded[i], ref, entries, poses[i][2]))
        return 0

    if args.uv_variants:
        if args.model is None:
            print("  --uv-variants needs --model")
            return 2
        model = decoded[args.model]
        fig, axes = plt.subplots(1, 2, figsize=(9, 5), dpi=110)
        for ax, unmirror in zip(axes, (False, True)):
            img, _, _ = shoot(model, pages, entries, args.yaw, args.pitch,
                              args.size * 2, not args.no_cull, unmirror)
            ax.imshow(np.clip(img / 255.0, 0, 1))
            ax.set_title("unmirrored object convention" if unmirror
                         else "raw decoded convention", fontsize=9)
            ax.set_axis_off()
        fig.suptitle(f"{args.level} model {args.model} - corner mappings", fontsize=11)
        fig.tight_layout()
        path = OUT / f"{args.level}_model{args.model:03d}_uv.png"
        fig.savefig(path)
        plt.close(fig)
        print(f"  wrote {path.name}")
        return 0

    if args.model is not None:
        model = decoded[args.model]
        positions, soft, how = poses[args.model]
        img, drawn, span = shoot(model, pages, entries, args.yaw, args.pitch,
                                 args.size * 3, not args.no_cull,
                                 positions=positions, skip=soft)
        path = OUT / f"{args.level}_model{args.model:03d}.png"
        plt.imsave(path, np.clip(img / 255.0, 0, 1))
        print(f"  {label(model, stpc.models[args.model], entries, how)}")
        print(f"  drew {drawn} faces, wrote {path.name}")
        if args.obj:
            write_obj(OUT / f"{args.level}_model{args.model:03d}.obj", model,
                      positions=positions)
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
        positions, soft, how = poses[i]
        img, drawn, span = shoot(model, pages, entries, args.yaw, args.pitch,
                                 args.size, not args.no_cull,
                                 positions=positions, skip=soft)
        ax.imshow(np.clip(img / 255.0, 0, 1))
        ax.set_title(label(model, stpc.models[i], entries, how), fontsize=7)
        print(f"   {label(model, stpc.models[i], entries, how)}  ({drawn} faces drawn)")
    for ax in axes:
        ax.set_axis_off()

    fig.suptitle(f"{args.level} - STPC object models", fontsize=11)
    fig.tight_layout()
    path = OUT / f"{args.level}_objects.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  wrote {path.name}")
    return 0


def write_obj(path: Path, model, unmirror: bool = True, positions=None) -> None:
    vertices = np.asarray(model.vertices if positions is None else positions, float)
    normals = np.asarray(model.normals, float)
    faces = [face.indices for face in model.faces]
    if unmirror:
        vertices = vertices.copy()
        normals = normals.copy()
        vertices[:, 0] *= -1.0
        normals[:, 0] *= -1.0
        faces = [tuple(reversed(idx)) for idx in faces]

    # Stored vertex normals are part-local like the positions, so a posed model gets none.
    with_normals = positions is None
    with open(path, "w") as fh:
        fh.write(f"# Croc 2 STPC object model {model.index}\n")
        for x, y, z in vertices:
            fh.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        if with_normals:
            for nx, ny, nz in normals:
                fh.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")
        for idx in faces:
            refs = (f"{i + 1}//{i + 1}" if with_normals else f"{i + 1}" for i in idx)
            fh.write("f " + " ".join(refs) + "\n")
    print(f"  wrote {path.name}")


if __name__ == "__main__":
    raise SystemExit(main())
