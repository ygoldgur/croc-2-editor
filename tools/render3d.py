#!/usr/bin/env python3
"""Render a level from a perspective camera, the way the game shows it.

A top-down orthographic map is hard to check against memory of the game, and it shows
the undersides of overhangs. This places a camera above and behind a point, like Croc's
own chase camera, so a render can be compared with a screenshot directly.
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from croc2.chunks.trak import rect_corners  # noqa: E402
from croc2.level import Level  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402
from tools.render_level import _page_images, build_scene  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "shots"
NEAR = 0.05
MARKER = np.array([[[255.0, 0.0, 255.0]]], np.float32)  # faces with texture id -1


def look_at(eye, target, up=(0.0, 1.0, 0.0)):
    """Right-handed view basis; the camera looks down its own -Z."""
    eye = np.asarray(eye, float)
    forward = np.asarray(target, float) - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.asarray(up, float))
    right /= np.linalg.norm(right)
    true_up = np.cross(right, forward)
    return eye, np.stack([right, true_up, -forward])


def raster(img, zbuf, tri, uvs, page, shade, cull=True):
    """Draw one triangle with perspective-correct UVs, nearest-texel sampling."""
    h, w = zbuf.shape
    xs = [p[0] for p in tri]
    ys = [p[1] for p in tri]
    x0, x1 = max(int(min(xs)), 0), min(int(max(xs)) + 1, w - 1)
    y0, y1 = max(int(min(ys)), 0), min(int(max(ys)) + 1, h - 1)
    if x0 >= x1 or y0 >= y1:
        return
    (ax, ay, aw), (bx, by, bw), (cx, cy, cw) = tri
    det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    # The viewport flips Y, so a face pointing at the camera winds negative here.
    if det == 0 or (cull and det > 0):
        return
    yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    px, py = xx + 0.5, yy + 0.5
    l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / det
    l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / det
    l3 = 1.0 - l1 - l2
    mask = (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
    if not mask.any():
        return
    inv_w = l1 / aw + l2 / bw + l3 / cw          # interpolate 1/w
    window = zbuf[y0:y1 + 1, x0:x1 + 1]
    mask &= inv_w > window
    if not mask.any():
        return
    u = (l1 * uvs[0][0] / aw + l2 * uvs[1][0] / bw + l3 * uvs[2][0] / cw) / inv_w
    v = (l1 * uvs[0][1] / aw + l2 * uvs[1][1] / bw + l3 * uvs[2][1] / cw) / inv_w
    ui = np.clip(u.astype(int), 0, page.shape[1] - 1)
    vi = np.clip(v.astype(int), 0, page.shape[0] - 1)
    img[y0:y1 + 1, x0:x1 + 1][mask] = page[vi[mask], ui[mask]] * shade
    window[mask] = inv_w[mask]


def rasterise(positions, faces, pages, entries, target, yaw, pitch, distance,
              size, fov, cull=True, uv_offset=0, flat_colours=False):
    """Draw a set of textured faces from an orbiting camera; return (image, drawn).

    Split out from `render` so anything with vertices, faces and a texture table can be
    photographed the same way - a whole level, or one object out of STPC.

    A face is ``(indices, texture_id[, uv_offset[, flat]])``. Faces whose entry has no
    page are skipped unless ``flat`` (default ``flat_colours``) says to fill them with
    the entry's colour.
    """
    pts = np.asarray(positions)

    ry, rp = math.radians(yaw), math.radians(pitch)
    offset = np.array([math.cos(rp) * math.sin(ry), math.sin(rp),
                       math.cos(rp) * math.cos(ry)]) * distance
    eye, basis = look_at(np.asarray(target, float) + offset, target)

    width = height = size
    focal = (width / 2) / math.tan(math.radians(fov) / 2)
    img = np.zeros((height, width, 3), np.float32)
    zbuf = np.full((height, width), -1e9, np.float32)

    cam = (pts - eye) @ basis.T          # into camera space
    depth = -cam[:, 2]                   # positive in front of the camera
    with np.errstate(divide="ignore", invalid="ignore"):
        sx = width / 2 + cam[:, 0] * focal / depth
        sy = height / 2 - cam[:, 1] * focal / depth

    drawn = 0
    for face in faces:
        indices, texture_id = face[0], face[1]
        face_uv_offset = face[2] if len(face) > 2 else uv_offset
        flat = face[3] if len(face) > 3 else flat_colours
        if texture_id >= len(entries):
            continue
        entry = entries[texture_id] if texture_id >= 0 else None
        if entry is None:
            page = MARKER
        elif entry.has_texture:
            page = pages[entry.page]
        elif flat:
            page = np.asarray(entry.colour, np.float32).reshape(1, 1, 3)
        else:
            continue
        idx = list(indices)
        if np.any(depth[idx] <= NEAR):   # crosses the near plane; skip rather than clip
            continue
        # A little directional shading, so surfaces read as 3D.
        corner = pts[idx]
        normal = np.cross(corner[1] - corner[0], corner[2] - corner[0])
        norm = np.linalg.norm(normal)
        shade = 0.65 + 0.35 * abs(normal[1] / norm) if norm > 1e-9 else 1.0

        uvs = (rect_corners(entry, face_uv_offset)[:len(idx)] if entry is not None
               else [(0, 0)] * len(idx))
        screen = [(sx[i], sy[i], depth[i]) for i in idx]
        fan = [(0, 1, 2), (0, 2, 3)] if len(idx) == 4 else [(0, 1, 2)]
        for a, b, c in fan:
            raster(img, zbuf, [screen[a], screen[b], screen[c]],
                   [uvs[a], uvs[b], uvs[c]], page, shade, cull)
        drawn += 1

    return img, drawn


def render(level, out_path, target, yaw, pitch, distance, size, fov, cull=True,
           scene=False):
    if scene:
        positions, faces, _, _ = build_scene(level)
    else:
        positions, faces = level.world_faces()
    img, drawn = rasterise(positions, faces, _page_images(level.text), level.text.entries,
                           target, yaw, pitch, distance, size, fov, cull)
    plt.imsave(out_path, np.clip(img / 255.0, 0, 1))
    return drawn


def _pick_instances(level: Level, names: list[str], indices: list[int]):
    chosen = []
    want = [n.lower() for n in names]
    index_set = set(indices)
    for inst in level.map.instances:
        name = level.actor_name(inst)
        if inst.index in index_set or any(w in name.lower() for w in want):
            chosen.append(inst)
    return chosen


def _suggest_distance(target: np.ndarray, focus_points: np.ndarray, zoom: str) -> float:
    if len(focus_points) == 0:
        radius = 2.5
    else:
        dxz = focus_points[:, [0, 2]] - target[[0, 2]]
        norms = np.linalg.norm(dxz, axis=1)
        radius = float(np.quantile(norms, 0.7)) if len(norms) else 1.5
        radius = max(radius, 1.5)
    factors = {"close": 1.8, "medium": 2.8, "wide": 4.2}
    caps = {"close": 8.0, "medium": 12.0, "wide": 18.0}
    return min(radius * factors[zoom], caps[zoom])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level")
    ap.add_argument("--target", nargs=3, type=float,
                    help="world point to look at: X Y Z")
    ap.add_argument("--scene", action="store_true",
                    help="render terrain plus placed STPC objects/actors")
    ap.add_argument("--focus-name", action="append", default=[],
                    help="include actor instances whose name contains this text")
    ap.add_argument("--focus-index", type=int, action="append", default=[],
                    help="include this actor instance index in focus target")
    ap.add_argument("--zoom", choices=("close", "medium", "wide"), default="medium",
                    help="auto distance preset when target is inferred")
    ap.add_argument("--yaw", type=float, default=0.0, help="degrees around Y")
    ap.add_argument("--pitch", type=float, default=26.0, help="degrees above horizon")
    ap.add_argument("--dist", type=float, default=None,
                    help="camera distance (default: inferred from focus and zoom)")
    ap.add_argument("--size", type=int, default=900)
    ap.add_argument("--fov", type=float, default=55.0)
    ap.add_argument("--name", default="view3d")
    ap.add_argument("--no-vflip", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-cull", action="store_true")
    args = ap.parse_args()
    level = Level.load(PRISTINE_WADS / f"{args.level}.wad")

    focus = _pick_instances(level, args.focus_name, args.focus_index)
    if args.target is not None:
        target = np.asarray(args.target, dtype=float)
    elif focus:
        p = np.asarray([inst.position for inst in focus], dtype=float)
        target = p.mean(axis=0)
    else:
        lo, hi = level.bounds()
        target = np.array([(lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0,
                           (lo[2] + hi[2]) / 2.0], dtype=float)

    focus_points = np.asarray([inst.position for inst in focus], dtype=float)
    dist = args.dist if args.dist is not None else _suggest_distance(target, focus_points,
                                                                     args.zoom)

    path = OUT / f"{args.name}.png"
    n = render(level, path, target, args.yaw, args.pitch, dist,
               args.size, args.fov, cull=not args.no_cull, scene=args.scene)
    print(f"target {target[0]:.3f} {target[1]:.3f} {target[2]:.3f}")
    print(f"distance {dist:.3f}  zoom {args.zoom}")
    if focus:
        names = ", ".join(f"{inst.index}:{level.actor_name(inst)}" for inst in focus[:8])
        if len(focus) > 8:
            names += ", ..."
        print(f"focus {len(focus)} instance(s): {names}")
    print(f"wrote {path.name} ({n} faces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
