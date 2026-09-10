# Croc 2 level editor

Reverse engineering the level format of Croc 2 (PC, Argonaut Software, 2000), working
towards a Blender add-on for editing and authoring levels.

Terrain decodes and renders end to end — tile placement, mesh library, textures and UVs
— verified by rendering levels and comparing them against the game. The `MAP` chunk is
fully walked, its environment zones (fog, camera, music, reverb) are confirmed by
editing them and launching the game, and both `MAP` and `STPC` now parse to their last
byte on every level that parses at all.

Objects have positions: 3,205 actor placements game-wide, each with a rotation and a
pointer to the actor definition it runs. Moving two of them and launching the game puts
Croc and a Gobbo King exactly where the edit says, which also identifies the player
spawn. Drawing objects is the current frontier — an instance points into its actor's
behaviour data rather than at a mesh, so which model an object uses is still unread.

- [docs/README.md](docs/README.md) — project state, layout and usage
- [docs/wad-format.md](docs/wad-format.md) — the format itself, field by field

## No game data here

This repository contains no Croc 2 assets, and none should ever be committed to it.
The game is copyright Argonaut Software and Fox Interactive.

To use the tools you need your own copy of the disc, arranged as:

```
game/pristine/       an untouched copy of the disc; the reference for original bytes
game/pristine/Wads/  the WAD files the tools read
game/cd/             a writable copy, the one the game actually loads
```

`croc2/paths.py` is the only place those locations are defined.

## Running

Nothing outside the Python standard library is needed to read or write WADs.
`matplotlib` and `numpy` are used only by the renderers.

```sh
python3 tools/roundtrip.py              # byte-exact round-trip over every WAD
python3 tools/survey.py                 # validate the decoders across all levels
python3 tools/render3d.py t1l1m001 --target 10.4 1.0 -8.5 --pitch 22 --dist 6
python3 tools/render_level.py t1l1m001 --mode actors   # where the objects stand
```

`roundtrip.py` and `survey.py` are the regression tests; run both after touching a
decoder.
