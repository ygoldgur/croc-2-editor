# Animation Work Summary

Where object and character rendering stands. Format details are in
[wad-format.md](wad-format.md), under `STPC`.

## Goal Context

- render Sailor Village (`t1l1m001`) terrain + placed objects/actors
- render actors correctly posed and animated

Both work for every Sailor Village character except Croc's soft vertices. The
Distressed Gobbo, the one the first round never got right, has three models. Its entry
code draws a random number and picks model 12, 13 or 14 (a quarter, a half and a
quarter of the time), and it has seven animations of its own (18–24). The game drew
model 13 for every one checked.

## What Was Wrong Before

The first round rendered the purple vase correctly and no character. Five
separate problems, all fixed:

1. **Vertex-to-part assignment.** Vertices were given to the part with the nearest
   translation. The `+0x82` array is the part table: consecutive runs of vertices,
   one count per part.
2. **Matrix use.** Vertices were transformed by `M · inverse(frame 0)`, which cancels the
   transform at frame 0 and draws the raw heap of parts. Each matrix maps its part-local
   vertices straight into model space, as row vectors: `[x y z 1] · M`.
3. **Model and animation choice.** A heuristic read a field at a fixed distance from the
   instance's entry point. It usually returned the same number for both, and was never
   compatible with the model. The actors name their records outright: bytecode `124`
   pushes the absolute `STPC` offset of each model and animation they use. Which pushed
   model an instance gets is decided by running its entry code (`croc2/vm.py`). Taking
   the first push gave the Distressed Gobbo the wrong one.
4. **Untextured faces skipped.** Most of a Gobbo has no texture page and is flat-coloured
   from its `TEXT` entry (`u0, u1, v0` = RGB). Skipping those faces drew a floating head.
5. **`--frames 1` crashed** with a division by zero.

## The Tests That Say It Is Right

- **Face normals, posed.** Stored face normals are in model space for the pose the model
  was exported in. Posed by the right frame, 199 models game-wide reproduce every one;
  raw, articulated models match 17%. `StpcChunk.export_pose` finds that frame, and
  `tools/survey.py` reports the counts.
- **Part counts match animation counts.** Models have 4, 5 or 9 parts in Sailor Village;
  its animations place 4, 5 or 9 matrices per frame, nothing else.
- **Every animation is named by an actor.** All 1,380 animations game-wide, checked by
  `tools/survey.py`, and each actor's animations fit its model.
- **By eye.** Gobbo King with crown and staff, Clipboard Gobbo holding its clipboard,
  Distressed Gobbo walking (animation 20), Croc green and recognisable.

## Open

- **Soft vertices** (83 models, Croc among them): per-frame data in the animation record,
  not decoded. Their faces are left out, so Croc has holes.
- **Which animation plays first** is up to the actor's bytecode. The tools use the first
  fitting one in code order.
- **Entry walks that stop.** 1,164 instances reach an undecoded opcode before setting
  a model, and fall back to the first model pushed. Distressed Gobbo instance 9 sets no
  model at all in its entry, and its real model is unknown.

## Confirmed In Game

`tools/mod_model.py --map 12=9 13=6` swapped the Distressed Gobbo's pushes of models 12
and 13 for Gobbo Says' and Clipboard Gobbo's models. Every Distressed Gobbo seen turned
into the Clipboard Gobbo, and none took the purple hat. So the pushes are the models,
and the switch chose 13. `tools/survey.py` checks that the walk gives these Gobbos 13.

`--word 756=1` turned the Gobbo's `rand(2.0)` into `rand(1)`, which is always 0. Every
Distressed Gobbo then wore model 12's red hat, so the switch is what picks the model.
The game never seeds `rand`, so the draw is the same on every boot; that is why the
Gobbos are stable across restarts.

## Useful Commands

```bash
python3 tools/render_anim.py t1l1m001 --actor 8 --list          # what the actor names
python3 tools/render_anim.py t1l1m001 --actor 8 --gif           # model 13, animation 18
python3 tools/render_anim.py t1l1m001 --actor 8 --anim 20 --gif # the walk cycle
python3 tools/render_object.py t1l1m001 --count 16              # posed montage
python3 tools/render3d.py t1l1m001 --scene --focus-index 8 --dist 0.9 --yaw 150
python3 tools/animprobe.py --level t1l1m001 --anim 18 --frames 1  # raw record dump
```
