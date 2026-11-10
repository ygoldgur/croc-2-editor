#!/usr/bin/env python3
"""Render an STPC model posed by one of its animations, as a frame strip and a GIF.

An articulated model's vertices are stored local to their part, and each animation
frame is one matrix per part that carries them into model space. With ``--actor`` the
model and the animations to choose from come from what that actor's code pushes;
``--model`` and ``--anim`` override either.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from croc2.chunks.stpc import pose  # noqa: E402
from croc2.level import Level  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402
from tools.render3d import rasterise  # noqa: E402
from tools.render_level import _page_images  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "shots"


def _frame_indices(total: int, count: int) -> list[int]:
    if total <= count:
        return list(range(total))
    if count <= 1:
        return [0]
    return sorted(set(int(round(i * (total - 1) / (count - 1))) for i in range(count)))


def _instance(level: Level, index: int):
    for inst in level.map.instances:
        if inst.index == index:
            return inst
    raise ValueError(f"actor instance {index} not found")


def resolve(level: Level, actor_index: int | None, model_index: int | None,
            anim_index: int | None) -> tuple[int, int, object]:
    """The model and animation to draw, from the actor unless overridden."""
    chosen = _instance(level, actor_index) if actor_index is not None else None
    if model_index is None:
        if chosen is None:
            raise ValueError("provide --model or --actor")
        model_index = level.model_index_for(chosen)
        if model_index is None:
            raise ValueError(f"actor {actor_index} ({level.actor_name(chosen)}) "
                             f"pushes no model")
    if anim_index is None:
        fits = (level.animations_for(chosen) if chosen is not None
                and level.model_index_for(chosen) == model_index else [])
        fits = fits or level.stpc.compatible_animations(level.stpc_payload, model_index)
        if not fits:
            raise ValueError(f"no animation fits model {model_index}")
        anim_index = fits[0]
    return model_index, anim_index, chosen


def list_choices(level: Level, actor_index: int | None, model_index: int | None) -> None:
    stpc, data = level.stpc, level.stpc_payload
    if actor_index is not None:
        inst = _instance(level, actor_index)
        actor = level.actor_for(inst)
        print(f"instance {inst.index}: {level.actor_name(inst)}  (actor {actor.index})")
        for r in stpc.references(actor):
            print(f"   pushes {r.kind:9} {r.index:>3}  at +{r.at}")
        chances = stpc.instance_models(inst.actor_offset)
        print("   entry sets " + (", ".join(f"model {m} {100 * p:g}%"
                                            for m, p in sorted(chances.items()))
                                  if chances else "a model the walk cannot reach"))
        model_index = level.model_index_for(inst) if model_index is None else model_index
        print(f"   uses model {model_index}; fitting animations "
              f"{level.animations_for(inst)}")
    if model_index is None:
        return
    ref = stpc.models[model_index]
    if ref.extra_count:
        print(f"model {model_index}: parts {stpc.part_counts(data, model_index)}, "
              f"{ref.vertex_count} vertices")
        print(f"   every fitting animation: "
              f"{stpc.compatible_animations(data, model_index)}")
        fit = stpc.export_pose(data, model_index)
        if fit:
            print(f"   exported in animation {fit[1]} frame {fit[2]}: "
                  f"{100 * fit[0]:.1f}% of face normals agree")
    else:
        print(f"model {model_index}: static, {ref.vertex_count} vertices")


def _render_frame(verts: np.ndarray, faces, pages, entries, centre, span: float,
                  size: int, yaw: float, pitch: float, cull: bool) -> tuple[np.ndarray, int]:
    img, n = rasterise(verts, faces, pages, entries, centre,
                       yaw, pitch, span * 2.2, size, 45.0, cull=cull, flat_colours=True)
    return np.clip(img, 0, 255).astype(np.uint8), n


def render_strip(level: Level, model_index: int, anim_index: int, label: str,
                 frames: int, size: int, yaw: float, pitch: float, cull: bool,
                 out_path: Path, gif_path: Path | None = None, fps: float = 8.0,
                 gif_frames: bool = True) -> int:
    stpc, data = level.stpc, level.stpc_payload
    pages = _page_images(level.text)
    entries = level.text.entries

    model = stpc.model(data, model_index)
    parts = stpc.vertex_parts(data, model_index)
    soft = {i for i, p in enumerate(parts) if p < 0}
    matrices = stpc.animation_matrices(data, anim_index)
    total = len(matrices)
    posed = []
    for frame in matrices:
        verts = np.asarray(pose(model.vertices, parts, frame), dtype=np.float64)
        verts[:, 0] *= -1.0  # STPC models are mirrored in X versus the world
        posed.append(verts)
    # Unmirroring reverses winding; faces on undecoded soft vertices are left out.
    faces = [(tuple(reversed(f.indices)), f.texture_id, 2) for f in model.faces
             if not soft.intersection(f.indices)]
    if soft:
        print(f"  skipping faces on {len(soft)} soft vertices, whose data is not decoded")

    # One camera for the whole animation, so motion is not cancelled by re-framing.
    every = np.concatenate(posed)
    keep = [i for i in range(len(every)) if i % len(posed[0]) not in soft]
    lo, hi = every[keep].min(axis=0), every[keep].max(axis=0)
    centre = (lo + hi) / 2.0
    span = float(max(hi - lo)) or 1.0

    shown = _frame_indices(total, frames)
    fig, axes = plt.subplots(1, len(shown), figsize=(len(shown) * 3.2, 3.6), dpi=120)
    axes = np.atleast_1d(axes).ravel()
    drawn = 0
    for ax, fi in zip(axes, shown):
        img, n = _render_frame(posed[fi], faces, pages, entries, centre, span,
                               size, yaw, pitch, cull)
        drawn += n
        ax.imshow(img)
        ax.set_title(f"frame {fi}/{total}", fontsize=8)
        ax.set_axis_off()
    fig.suptitle(label, fontsize=10)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)

    if gif_path is not None:
        which = range(total) if gif_frames else shown
        imgs = [Image.fromarray(_render_frame(posed[i], faces, pages, entries, centre,
                                              span, size, yaw, pitch, cull)[0])
                for i in which]
        duration = max(int(round(1000.0 / max(fps, 0.1))), 20)
        imgs[0].save(gif_path, save_all=True, append_images=imgs[1:],
                     duration=duration, loop=0, optimize=False)
    return drawn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level", nargs="?", default="t1l1m001")
    ap.add_argument("--actor", type=int, help="actor instance index to resolve model+anim")
    ap.add_argument("--model", type=int, help="override model index")
    ap.add_argument("--anim", type=int, help="override animation index")
    ap.add_argument("--list", action="store_true",
                    help="show what the actor pushes and which animations fit, and stop")
    ap.add_argument("--frames", type=int, default=6, help="how many frames in the strip")
    ap.add_argument("--size", type=int, default=420)
    ap.add_argument("--yaw", type=float, default=35.0)
    ap.add_argument("--pitch", type=float, default=20.0)
    ap.add_argument("--name", default=None)
    ap.add_argument("--gif", action="store_true", help="also write every frame as a GIF")
    ap.add_argument("--fps", type=float, default=15.0, help="GIF playback frames/sec")
    ap.add_argument("--gif-name", default=None, help="GIF filename (default from --name)")
    ap.add_argument("--no-cull", action="store_true")
    args = ap.parse_args()

    level = Level.load(PRISTINE_WADS / f"{args.level}.wad")
    if level.stpc is None:
        print(f"{args.level}: STPC did not parse")
        return 2
    if args.list:
        list_choices(level, args.actor, args.model)
        return 0

    model, anim, inst = resolve(level, args.actor, args.model, args.anim)
    label = f"{level.name} model {model} anim {anim}"
    if inst is not None:
        label += f"  actor {inst.index}: {level.actor_name(inst)}"
    OUT.mkdir(exist_ok=True)
    stem = args.name or f"{args.level}_model{model}_anim{anim}"
    out = OUT / (stem if stem.endswith(".png") else stem + ".png")
    gif_path = OUT / (args.gif_name or out.with_suffix(".gif").name) if args.gif else None
    drawn = render_strip(level, model, anim, label, args.frames, args.size, args.yaw,
                         args.pitch, not args.no_cull, out, gif_path, args.fps)
    print(f"wrote {out.name}  model={model} anim={anim} faces_drawn={drawn}")
    if gif_path is not None:
        print(f"wrote {gif_path.name}  fps={args.fps:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
