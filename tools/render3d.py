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

from croc2.level import Level  # noqa: E402
from croc2.paths import PRISTINE_WADS  # noqa: E402
from tools.render_level import _page_images  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "shots"
NEAR = 0.05


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
              size, fov, flip_v=True, cull=True):
    """Draw a set of textured faces from an orbiting camera; return (image, drawn).

    Split out from `render` so anything with vertices, faces and a texture table can be
    photographed the same way - a whole level, or one object out of STPC.
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
    for indices, texture_id in faces:
        if texture_id >= len(entries):
            continue
        entry = entries[texture_id]
        page = pages[entry.page] if entry.has_texture else None
        if page is None:
            continue
        idx = list(indices)
        if np.any(depth[idx] <= NEAR):   # crosses the near plane; skip rather than clip
            continue
        # A little directional shading, so surfaces read as 3D.
        corner = pts[idx]
        normal = np.cross(corner[1] - corner[0], corner[2] - corner[0])
        norm = np.linalg.norm(normal)
        shade = 0.65 + 0.35 * abs(normal[1] / norm) if norm > 1e-9 else 1.0

        rect = [(entry.u0, entry.v0), (entry.u1, entry.v0),
                (entry.u1, entry.v1), (entry.u0, entry.v1)]
        perm = (3, 2, 1, 0) if flip_v else (0, 1, 2, 3)
        uvs = [rect[perm[k]] for k in range(len(idx))]
        screen = [(sx[i], sy[i], depth[i]) for i in idx]
        fan = [(0, 1, 2), (0, 2, 3)] if len(idx) == 4 else [(0, 1, 2)]
        for a, b, c in fan:
            raster(img, zbuf, [screen[a], screen[b], screen[c]],
                   [uvs[a], uvs[b], uvs[c]], page, shade, cull)
        drawn += 1

    return img, drawn


def render(level, out_path, target, yaw, pitch, distance, size, fov, flip_v=True,
           cull=True):
    positions, faces = level.world_faces()
    img, drawn = rasterise(positions, faces, _page_images(level.text), level.text.entries,
                           target, yaw, pitch, distance, size, fov, flip_v, cull)
    plt.imsave(out_path, np.clip(img / 255.0, 0, 1))
    return drawn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("level")
    ap.add_argument("--target", nargs=3, type=float, required=True,
                    help="world point to look at: X Y Z")
    ap.add_argument("--yaw", type=float, default=0.0, help="degrees around Y")
    ap.add_argument("--pitch", type=float, default=30.0, help="degrees above horizon")
    ap.add_argument("--dist", type=float, default=6.0)
    ap.add_argument("--size", type=int, default=900)
    ap.add_argument("--fov", type=float, default=60.0)
    ap.add_argument("--name", default="view3d")
    ap.add_argument("--no-vflip", action="store_true")
    ap.add_argument("--no-cull", action="store_true")
    args = ap.parse_args()
    level = Level.load(PRISTINE_WADS / f"{args.level}.wad")
    path = OUT / f"{args.name}.png"
    n = render(level, path, args.target, args.yaw, args.pitch, args.dist,
               args.size, args.fov, flip_v=not args.no_vflip, cull=not args.no_cull)
    print(f"wrote {path.name} ({n} faces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
