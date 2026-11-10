#!/usr/bin/env python3
"""Survey LGHT, MAP, TRAK and STPC across every level: check decoders are complete."""
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from croc2 import Wad  # noqa: E402
from croc2.chunks import LightChunk, MapChunk, TextChunk, TrakChunk, TrakParseError  # noqa: E402
from croc2.chunks.stpc import (GROUPS_FLAG, StpcChunk, StpcParseError,  # noqa: E402
                                face_normal_agreement, read_animation_file)
from croc2.paths import PRISTINE_WADS as WADS  # noqa: E402


def normal_matches(model) -> tuple[int, int]:
    """How many stored face normals point the same way as the triangle's own geometry.

    A field can only pass this test if it really is the face normal, which is what
    pinned the TRAK triangle layout down and now says the same for STPC's models.
    """
    return face_normal_agreement(model.vertices, model.triangles, model.face_normals)


def bbox_matches(model) -> bool:
    """Whether the vertices fill the bounding box the header declares."""
    if not model.vertices:
        return True
    for k in range(3):
        values = [p[k] for p in model.vertices]
        if (abs(min(values) - model.bbox_min[k]) > 1e-3
                or abs(max(values) - model.bbox_max[k]) > 1e-3):
            return False
    return True


def terrain_footprint(tiles) -> tuple[float, float, float, float]:
    """The x/z rectangle the tiles cover. Tiles are 1.0 across, centred on .5s."""
    xs = [t.x for t in tiles]
    zs = [t.z for t in tiles]
    return min(xs) - 0.5, max(xs) + 0.5, min(zs) - 0.5, max(zs) + 0.5


def main() -> int:
    light_types = Counter()
    rotations = Counter()
    tile_flags = Counter()
    grids = Counter()
    lght_trailing = Counter()
    bad = []
    n = 0
    trak_skipped = 0
    tex_span = []  # highest texture id used, as a fraction of the table size
    faces = Counter()
    stpc_exact = stpc_failed = 0
    actors = instances = on_terrain = resolved = 0
    obj_models = Counter()
    obj_bbox = Counter()
    obj_normals = {"static": (0, 0), "articulated": (0, 0)}
    posed = Counter()
    pushed = Counter()

    defanim = read_animation_file((WADS / "defanim.wad").read_bytes())
    defanim_parts = Counter((a.part_count, a.soft_count) for a in defanim)

    for p in sorted(WADS.glob("*.wad")):
        wad = Wad.read(p)
        if not wad.is_level:
            continue
        n += 1

        lc = wad.get("LGHT")
        lights = LightChunk.parse(lc.payload)
        if lights.to_bytes() != lc.payload:
            bad.append(f"{p.name}: LGHT round-trip")
        light_types.update(l.type for l in lights.lights)
        lght_trailing[len(lights.trailing)] += 1

        mc = wad.get("MAP")
        m = MapChunk.parse(mc.payload)
        if m.to_bytes() != mc.payload:
            bad.append(f"{p.name}: MAP round-trip")
        rotations.update(t.rotation for t in m.tiles)
        tile_flags.update(t.flags for t in m.tiles)
        grids[(m.grid_x, m.grid_z)] += 1
        # Every tile must reference a mesh that TRAK actually contains.
        trak_count = int.from_bytes(wad.get("TRAK").payload[:4], "little")
        worst = max(t.model_id for t in m.tiles)
        if worst >= trak_count:
            bad.append(f"{p.name}: model_id {worst} >= TRAK count {trak_count}")

        # Every render triangle must select a texture the level actually ships.
        try:
            trak = TrakChunk.parse(wad.get("TRAK").payload)
        except TrakParseError:
            trak_skipped += 1
            continue
        entries = len(TextChunk.parse(wad.get("TEXT").payload).entries)
        used = {t for m in trak.models for t in m.texture_ids}
        if used and max(used) >= entries:
            bad.append(f"{p.name}: texture id {max(used)} >= TEXT entries {entries}")
        if used:
            tex_span.append((max(used) + 1) / entries)

        # Triangles should merge back into quads, leaving no triangle uncovered.
        text = TextChunk.parse(wad.get("TEXT").payload)
        for m in trak.models:
            covered = 0
            for f in m.faces:
                faces["quad" if f.is_quad else "lone triangle"] += 1
                covered += 2 if f.is_quad else 1
                if f.texture_id < entries and not text.entries[f.texture_id].has_texture:
                    faces["untextured"] += 1
            if covered != len(m.triangles):
                bad.append(f"{p.name}: model {m.index} faces cover {covered} "
                           f"of {len(m.triangles)} triangles")

        # STPC must consume exactly, the same bar MAP and TRAK clear.
        wfpc = struct.unpack("<I", wad.get("WFPC").payload)[0]
        try:
            stpc = StpcChunk.parse(wad.get("STPC").payload, bool(wfpc & GROUPS_FLAG))
        except (StpcParseError, struct.error) as exc:
            stpc_failed += 1
            bad.append(f"{p.name}: STPC {exc}")
            continue
        if stpc.left:
            stpc_failed += 1
            bad.append(f"{p.name}: STPC leaves {stpc.left:+,} bytes")
            continue
        stpc_exact += 1
        actors += len(stpc.actors)

        # Object models. The static ones - no extra uint16 array - must pass the same
        # tests TRAK's meshes pass. The articulated ones fail them raw, because their
        # vertices are part-local; posed in the frame they were exported in, every
        # stored face normal should agree with its geometry again.
        payload = wad.get("STPC").payload
        for i, ref in enumerate(stpc.models):
            model = stpc.model(payload, i)
            kind = "articulated" if ref.extra_count else "static"
            obj_models[kind] += 1
            ok, total = normal_matches(model)
            obj_normals[kind] = (obj_normals[kind][0] + ok,
                                 obj_normals[kind][1] + total)
            obj_bbox[kind] += bbox_matches(model)
            if max(model.texture_ids, default=0) >= entries:
                bad.append(f"{p.name}: STPC model {i} texture id out of range")
            if kind == "static":
                if total and ok != total:
                    bad.append(f"{p.name}: static STPC model {i} has {total - ok} "
                               f"face normals that disagree with its geometry")
                if not bbox_matches(model):
                    bad.append(f"{p.name}: static STPC model {i} does not fill its "
                               f"declared bounding box")
            else:
                counts = stpc.part_counts(payload, i)
                soft = ref.vertex_count - sum(counts)
                if soft < 0:
                    bad.append(f"{p.name}: STPC model {i} parts claim {sum(counts)} "
                               f"of {ref.vertex_count} vertices")
                    continue
                if soft:
                    posed["soft vertices"] += 1
                    posed["soft, fit by defanim"] += \
                        defanim_parts[(len(counts), soft)] > 0
                    continue
                fit = stpc.export_pose(payload, i)
                if fit is None:
                    posed["no animation fits"] += 1
                elif fit[0] == 1.0:
                    posed["every normal"] += 1
                elif fit[0] >= 0.95:
                    posed[">=95%"] += 1
                else:
                    posed["<95%"] += 1

        # Actors name their models and animations by pushing their header offsets.
        # Every animation is named that way, so one that is not is a reading error.
        named = {r.index for a in stpc.actors for r in stpc.references(a)
                 if r.kind == "animation"}
        pushed["animations"] += len(stpc.animations)
        pushed["animations named"] += len(named)
        if len(named) != len(stpc.animations):
            bad.append(f"{p.name}: {len(stpc.animations) - len(named)} animations "
                       f"no actor pushes")
        pushed["models"] += len(stpc.models)
        pushed["models named"] += len({r.index for a in stpc.actors
                                       for r in stpc.references(a) if r.kind == "model"})

        # Actor instances: each must point inside an actor record, and stand on the
        # terrain. Anything that does not is a reading error, bar the odd camera.
        placed = MapChunk.parse(mc.payload, [len(mm.vertices) for mm in trak.models],
                                wfpc)
        # Splicing instances back is a write path now, so it has to be lossless.
        if placed.to_bytes() != mc.payload:
            bad.append(f"{p.name}: MAP round-trip with instances parsed")
        x0, x1, z0, z1 = terrain_footprint(placed.tiles)
        for inst in placed.instances:
            instances += 1
            pushed["instances with a model"] += stpc.instance_model(inst.actor_offset) is not None
            chances = stpc.instance_models(inst.actor_offset)
            pushed["instances evaluated"] += bool(chances)
            if chances and abs(sum(chances.values()) - 1) > 1e-9:
                bad.append(f"{p.name}: instance {inst.index} model chances sum to "
                           f"{sum(chances.values())}")
            # The random switch, whose likeliest branch the game test confirmed.
            actor = stpc.actor_at(inst.actor_offset)
            if (p.stem == "t1l1m001" and actor is not None
                    and actor.name == "Distressed Gobbo"
                    and inst.actor_offset - actor.offset - 4 == 740
                    and chances != {12: 0.25, 13: 0.5, 14: 0.25}):
                bad.append(f"{p.name}: Distressed Gobbo {inst.index} chances {chances}")
            if actor is not None:
                resolved += 1
            else:
                bad.append(f"{p.name}: instance {inst.index} offset "
                           f"{inst.actor_offset} hits no actor")
            wx, _, wz = inst.position
            if x0 <= wx <= x1 and z0 <= wz <= z1:
                on_terrain += 1

    print(f"levels surveyed: {n}")
    print(f"TRAK skipped:    {trak_skipped} (known demo/attract variants)")
    if tex_span:
        print(f"texture ids:     all in range; terrain uses the leading "
              f"{100 * min(tex_span):.0f}-{100 * max(tex_span):.0f}% of each table")
    nf = faces["quad"] + faces["lone triangle"]
    if nf:
        print(f"render faces:    {nf} total, {100 * faces['quad'] / nf:.1f}% quads, "
              f"{faces['untextured']} with no texture")
    print(f"STPC:            {stpc_exact} consumed exactly, {stpc_failed} failed; "
          f"{actors} actor definitions")
    if instances:
        print(f"actor instances: {instances} placed, {resolved} resolve to an actor, "
              f"{100 * on_terrain / instances:.0f}% stand on the terrain")
    for kind in ("static", "articulated"):
        n = obj_models[kind]
        if not n:
            continue
        ok, total = obj_normals[kind]
        print(f"object models:   {n:>5} {kind:<12} "
              f"face normals match {100 * ok / max(total, 1):6.2f}%, "
              f"bbox exact {100 * obj_bbox[kind] / n:6.2f}%")
    print(f"posed:           export pose reproduces every face normal on "
          f"{posed['every normal']}, >=95% on {posed['>=95%']}, less on {posed['<95%']}; "
          f"{posed['no animation fits']} with no animation that fits")
    print(f"                 {posed['soft vertices']} with soft vertices (not decoded), "
          f"{posed['soft, fit by defanim']} of them "
          f"fit by defanim.wad")
    print(f"defanim.wad:     {len(defanim)} animations, consumed exactly")
    print(f"actors name:     {pushed['animations named']}/{pushed['animations']} animations, "
          f"{pushed['models named']}/{pushed['models']} models; "
          f"{pushed['instances with a model']}/{instances} instances find a model, "
          f"{pushed['instances evaluated']} by running their entry code")
    print(f"light types:     {dict(light_types)}")
    print(f"LGHT leftover:   {dict(lght_trailing)}")
    print(f"tile rotations:  {{{', '.join(f'0x{k:X}: {v}' for k, v in sorted(rotations.items()))}}}")
    print(f"tile flags:      {dict(tile_flags)}")
    print(f"grid sizes:      {dict(grids)}")
    print(f"problems:        {len(bad)}")
    for b in bad:
        print("  ", b)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
