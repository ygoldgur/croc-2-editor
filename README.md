# Croc 2 level editor

Reverse engineering the level format of Croc 2 (PC, Argonaut Software, 2000), working
towards a Blender add-on for editing and authoring levels.

Terrain currently decodes and renders end to end — tile placement, mesh library,
textures and UVs — verified by rendering levels and comparing them against the game.

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
```

`roundtrip.py` and `survey.py` are the regression tests; run both after touching a
decoder.
