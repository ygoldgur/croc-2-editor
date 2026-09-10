#!/usr/bin/env python3
"""Export a level to OBJ and render a top-down map of it.

Modes: `height` colours triangles by elevation, `texture` tints each face with the
average colour of its texture, and `textured` samples the real texture pages.
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
from matplotlib.collections import PolyCollection  # noqa: E402

from croc2.chunks import TextChunk  # noqa: E402
from croc2.level import Level  # noqa: E402
from croc2.paths import GAME_WADS, PRISTINE_WADS  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "shots"
RESOLUTION = 1400


def render_flat(level: Level, out_path: Path, mode: str) -> None:
    positions, triangles, texture_ids = level.world_mesh()
    pts = np.asarray(positions)
    tris = np.asarray(triangles)
    verts = pts[tris]

    # Top-down: X across, Z up the page.
    polys = verts[:, :, [0, 2]]
    heights = verts[:, :, 1].mean(axis=1)
    # Painter's order so higher geometry draws last.
    order = np.argsort(heights)

    fig, ax = plt.subplots(figsize=(11, 11), dpi=110)
    if mode == "texture":
        palette = level.texture_colours()
        faces = [palette[t] if t < len(palette) else (1.0, 0.0, 1.0)
                 for t in texture_ids]
        ax.add_collection(PolyCollection(polys[order],
                                         facecolors=[faces[i] for i in order],
                                         edgecolors="none"))
        ax.set_facecolor("0.15")
    else:
        coll = PolyCollection(polys[order], array=heights[order], cmap="viridis",
                              edgecolors="black", linewidths=0.08)
        ax.add_collection(coll)
        fig.colorbar(coll, ax=ax, label="height (Y)", shrink=0.7)
    ax.autoscale_view()
    ax.set_aspect("equal")
    ax.invert_yaxis()  # match an overhead camera: +Z runs down the page
    ax.set_xlabel("X"); ax.set_ylabel("Z")
    ax.set_title(f"{level.name} - {len(level.map.tiles)} tiles, "
                 f"{len(level.trak.models)} meshes, {len(tris)} triangles")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def _page_images(text: TextChunk):
    """Decode every texture page to an RGB array, or None where one does not decode."""
    pages = []
    for tex in text.textures:
        flat = np.frombuffer(tex.to_rgb(), dtype=np.uint8)
        if flat.size == tex.height * tex.width * 3:
            pages.append(flat.reshape(tex.height, tex.width, 3).astype(np.float32))
        else:
            pages.append(None)
    return pages


def _raster(img, zbuf, screen, uvs, depths, page):
    """Draw one texture-mapped triangle, keeping the highest geometry."""
    p = np.asarray(screen)
    uv = np.asarray(uvs, dtype=np.float64)
    h, w = zbuf.shape
    x0, x1 = max(int(p[:, 0].min()), 0), min(int(p[:, 0].max()) + 1, w - 1)
    y0, y1 = max(int(p[:, 1].min()), 0), min(int(p[:, 1].max()) + 1, h - 1)
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    px, py = xx + 0.5, yy + 0.5
    (ax, ay), (bx, by), (cx, cy) = p
    det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(det) < 1e-9:
        return
    l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / det
    l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / det
    l3 = 1.0 - l1 - l2
    mask = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6)
    if not mask.any():
        return
    depth = l1 * depths[0] + l2 * depths[1] + l3 * depths[2]
    window = zbuf[y0:y1 + 1, x0:x1 + 1]
    mask &= depth > window
    if not mask.any():
        return
    u = l1 * uv[0, 0] + l2 * uv[1, 0] + l3 * uv[2, 0]
    v = l1 * uv[0, 1] + l2 * uv[1, 1] + l3 * uv[2, 1]
    ui = np.clip(u.astype(int), 0, page.shape[1] - 1)
    vi = np.clip(v.astype(int), 0, page.shape[0] - 1)
    img[y0:y1 + 1, x0:x1 + 1][mask] = page[vi[mask], ui[mask]]
    window[mask] = depth[mask]


def render_textured(level: Level, out_path: Path) -> tuple[int, int]:
    positions, faces = level.world_faces()
    pages = _page_images(level.text)
    pts = np.asarray(positions)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    scale = RESOLUTION / max(hi[0] - lo[0], hi[2] - lo[2])
    width = int((hi[0] - lo[0]) * scale) + 1
    height = int((hi[2] - lo[2]) * scale) + 1

    img = np.zeros((height, width, 3), np.float32)
    zbuf = np.full((height, width), -1e9, np.float32)

    drawn = untextured = 0
    for indices, texture_id in faces:
        if texture_id >= len(level.text.entries):
            untextured += 1
            continue
        entry = level.text.entries[texture_id]
        page = pages[entry.page] if entry.has_texture else None
        if page is None:
            untextured += 1
            continue
        corner = [pts[i] for i in indices]
        screen = [((c[0] - lo[0]) * scale, (c[2] - lo[2]) * scale) for c in corner]
        depths = [c[1] for c in corner]
        uvs = level.face_uvs(texture_id)[:len(indices)]
        fan = [(0, 1, 2), (0, 2, 3)] if len(indices) == 4 else [(0, 1, 2)]
        for tri in fan:
            _raster(img, zbuf, [screen[i] for i in tri], [uvs[i] for i in tri],
                    [depths[i] for i in tri], page)
        drawn += 1

    # An overhead camera with screen-right = +X has screen-up = -Z, so rows run with
    # +Z. Flipping here would mirror the whole map.
    plt.imsave(out_path, np.clip(img / 255.0, 0, 1))
    return drawn, untextured


def render_zones(level: Level, out_path: Path) -> int:
    """The textured map with the MAP zone records drawn on top, numbered."""
    positions, _ = level.world_faces()
    pts = np.asarray(positions)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    scale = RESOLUTION / max(hi[0] - lo[0], hi[2] - lo[2])

    img, _ = _textured_image(level, pts, lo, scale)
    fig, ax = plt.subplots(figsize=(13, 13), dpi=110)
    ax.imshow(np.clip(img / 255.0, 0, 1))

    def to_screen(x, z):
        return (x - lo[0]) * scale, (z - lo[2]) * scale

    for i, zone in enumerate(level.map.zones):
        if zone.is_box:
            cx, cy, cz = zone.cell
            sx, _, sz = zone.size
            # Boxes are grid cells; tile z is negative, so the cell runs -z..-(z+sz).
            x0, z0 = to_screen(cx, -(cz + sz))
            x1, z1 = to_screen(cx + sx, -cz)
            colour = "red" if zone.is_default else "yellow"
            ax.add_patch(plt.Rectangle((x0, z0), x1 - x0, z1 - z0, fill=False,
                                       edgecolor=colour, linewidth=2.0))
            tx, tz = (x0 + x1) / 2, (z0 + z1) / 2
        else:
            wx, _, wz = zone.position
            tx, tz = to_screen(wx, wz)
            colour = "red" if zone.is_default else "cyan"
            ax.plot([tx], [tz], marker="o", markersize=7, color=colour,
                    markeredgecolor="black")
        ax.annotate(str(i), (tx, tz), color="white", fontsize=11, weight="bold",
                    ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.15", fc="black", alpha=0.65))

    boxes = sum(1 for e in level.map.zones if e.is_box)
    ax.set_title(f"{level.name} - {len(level.map.zones)} environment zones "
                 f"({boxes} boxes, {len(level.map.zones) - boxes} points); "
                 f"red = default zone")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return len(level.map.zones)


def render_actors(level: Level, out_path: Path) -> tuple[int, int]:
    """The textured map with MAP's actor instances drawn on top, named.

    This is the check on the instance record: if the position fields are read right
    the markers sit on the terrain, near landmarks that suit their names. If they are
    wrong they scatter, or pile up off the map.
    """
    positions, _ = level.world_faces()
    pts = np.asarray(positions)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    scale = RESOLUTION / max(hi[0] - lo[0], hi[2] - lo[2])

    img, _ = _textured_image(level, pts, lo, scale)
    fig, ax = plt.subplots(figsize=(14, 14), dpi=110)
    ax.imshow(np.clip(img / 255.0, 0, 1))

    inside = 0
    for inst in level.map.instances:
        wx, wy, wz = inst.position
        if lo[0] <= wx <= hi[0] and lo[2] <= wz <= hi[2]:
            inside += 1
        sx, sz = (wx - lo[0]) * scale, (wz - lo[2]) * scale
        # A short stalk towards the facing direction, so a wrong yaw shows up too.
        yaw = math.radians(inst.degrees[1])
        ax.plot([sx, sx + math.sin(yaw) * 22], [sz, sz + math.cos(yaw) * 22],
                color="black", linewidth=1.4, zorder=3)
        ax.plot([sx], [sz], marker="o", markersize=7, color="magenta",
                markeredgecolor="black", zorder=4)
        ax.annotate(f"{inst.index} {level.actor_name(inst)}", (sx, sz),
                    textcoords="offset points", xytext=(8, -8), color="white",
                    fontsize=7, zorder=5,
                    bbox=dict(boxstyle="round,pad=0.15", fc="black", alpha=0.6))

    total = len(level.map.instances)
    actors = len(level.stpc.actors) if level.stpc else 0
    ax.set_title(f"{level.name} - {total} actor instances, {actors} actor definitions; "
                 f"{inside} inside the terrain bounds")
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return total, inside


def _textured_image(level: Level, pts, lo, scale):
    """Rasterise the level top-down and hand back the raw image."""
    _, faces = level.world_faces()
    pages = _page_images(level.text)
    hi = pts.max(axis=0)
    width = int((hi[0] - lo[0]) * scale) + 1
    height = int((hi[2] - lo[2]) * scale) + 1
    img = np.zeros((height, width, 3), np.float32)
    zbuf = np.full((height, width), -1e9, np.float32)
    drawn = 0
    for indices, texture_id in faces:
        if texture_id >= len(level.text.entries):
            continue
        entry = level.text.entries[texture_id]
        page = pages[entry.page] if entry.has_texture else None
        if page is None:
            continue
        corner = [pts[i] for i in indices]
        screen = [((c[0] - lo[0]) * scale, (c[2] - lo[2]) * scale) for c in corner]
        depths = [c[1] for c in corner]
        uvs = level.face_uvs(texture_id)[:len(indices)]
        fan = [(0, 1, 2), (0, 2, 3)] if len(indices) == 4 else [(0, 1, 2)]
        for tri in fan:
            _raster(img, zbuf, [screen[i] for i in tri], [uvs[i] for i in tri],
                    [depths[i] for i in tri], page)
        drawn += 1
    return img, drawn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("levels", nargs="+", help="level stem, e.g. t1l1m001")
    ap.add_argument("--mode",
                    choices=("height", "texture", "textured", "zones", "actors"),
                    default="textured", help="how to colour the map")
    ap.add_argument("--from-game", action="store_true",
                    help="read the writable copy the game loads, not the pristine one, "
                         "to see what an edit actually did")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    wads = GAME_WADS if args.from_game else PRISTINE_WADS

    for stem in args.levels:
        level = Level.load(wads / f"{stem}.wad")
        obj = OUT / f"{stem}.obj"
        nv, nf = level.write_obj(obj)
        lo, hi = level.bounds()
        png = OUT / f"{stem}_{args.mode}.png"
        print(f"{stem}: {len(level.map.tiles)} tiles, {len(level.trak.models)} meshes, "
              f"{nv} verts, {nf} textured faces")
        print(f"  bounds  X {lo[0]:7.2f}..{hi[0]:7.2f}   "
              f"Y {lo[1]:6.2f}..{hi[1]:6.2f}   Z {lo[2]:7.2f}..{hi[2]:7.2f}")
        if args.mode == "zones":
            n = render_zones(level, png)
            print(f"  drew {n} zone records over the map")
        elif args.mode == "actors":
            total, inside = render_actors(level, png)
            print(f"  drew {total} actor instances, {inside} inside the terrain bounds")
        elif args.mode == "textured":
            drawn, untextured = render_textured(level, png)
            print(f"  rasterised {drawn} faces, {untextured} carry no texture")
        else:
            render_flat(level, png, args.mode)
        print(f"  wrote {obj.name}, {obj.stem}.mtl, {png.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
