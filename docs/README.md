# Croc 2 level editor

Reverse engineering the level format of Croc 2 (PC, Argonaut Software, 2000) with the
goal of a Blender add-on for editing and authoring levels.

Format details are in [wad-format.md](wad-format.md).

## State

Working:

- WAD container parse/write, byte-exact round-trip on all 79 WADs
- `LGHT` fully decoded
- `MAP` fully walked — every section consumed exactly on all 75 parseable levels
- `MAP` zone records: fog, camera angle, music and reverb, confirmed in-game
- `WFPC` explained: it is the per-level flag word gating four optional `MAP` sections
- `TRAK` meshes decoded — vertices, normals, triangles, face normals, bounding boxes
- `TEXT` decoded — texture pages and the atlas table
- Which texture each triangle uses, validated on all 75 parseable levels
- Triangles merged back into quads, and their UV mapping — terrain is fully textured
- Tile rotation direction, pinned down by terrain continuity across seams
- Level export to textured OBJ + MTL + texture pages as PNG, openable anywhere
- Perspective renders confirmed by eye against the real game
- Edits confirmed to reach the running game

Blocked or untouched:

- Where objects and actors are actually placed. The `MAP` zone list is not it,
  and no chunk decoded so far holds them; `STPC` is the remaining candidate
- `STPC`: known to be a relocatable blob the `MAP` entities point into by byte offset,
  and known not to be scripts, but its internal layout is undecoded
- `t0i0m006` and `t0l1m002` use a `TRAK` variant we reject
- The 44-byte `TRAK` records: layout known, meaning not. Not needed for rendering

Settled negatives, so they are not chased again: faces carry no stored UVs and no
per-face UV rotation. The triangle flags do not select one — every flag value wants
the default corner assignment on 90-98% of wall faces — and the trailing float at
+0x18 is continuous, with 24,592 distinct values, so it is a plane offset rather than
an orientation index.

Ghidra runs in a container, so the host needs no JDK and nothing lands in the repo:

```sh
./ghidra/run.sh          # first run imports and analyses, ~2 min; then runs the script
```

It mounts `Croc2.exe` read-only, caches the analysed project in `ghidra/project/`, and
writes `ghidra/out/decompiled.c` — 872 functions, all of them, which is small enough to
grep. Chunk ids appear in the code as 32-bit immediates (`MAP ` is `0x4d415020`), and
searching for one leads straight to its loader. Set `CROC2_EXE` if your install differs.
The image is pinned to amd64: Ghidra ships no arm64 decompiler, so it runs emulated.

The technique that has worked is finding a claim about a field that the data can
falsify on its own — a normal must match the geometry it belongs to, an index must
stay inside its table and be dense, tiles must seam together into a continuous
surface. Guessing at byte patterns without such a test has not worked. If that
runs out, disassembling the chunk loaders in `Croc2.exe` (737 KB x86, with string
anchors like `"Couldnt load wad tribe %d, level %d, map %d, type %d"`) is the
fallback; it needs a JDK and Ghidra, neither of which is installed.

## Layout

```
croc2/          format library
  wad.py        container
  chunks/       lght, map_, trak, text
  level.py      MAP placements + TRAK meshes -> world mesh, textured OBJ export
  png.py        stdlib-only PNG writer for texture pages
  paths.py      where the game data lives
tools/          roundtrip, survey, render_level, render3d, mod_menu, wadinfo,
                mapwalk (walks MAP section by section, checks it consumes exactly),
                zones (surveys the MAP zone records), mod_zone (fog test)
game/pristine/  verified untouched disc copy
game/cd/        what the game reads
game/backup/    original save file
refs/           cloned reference repos
docs/           this
shots/          exported OBJ and renders
```

## Usage

```sh
python3 tools/roundtrip.py              # byte-exact round-trip over every WAD
python3 tools/survey.py                 # validate decoders on all 77 levels
python3 tools/render_level.py t1l1m001  # textured OBJ + MTL + pages + PNG into shots/
python3 tools/render_level.py t1l1m001 --mode height    # coloured by elevation instead
python3 tools/render3d.py t1l1m001 --target 10.4 1.0 -8.5 --yaw 180 --pitch 25 --dist 7
python3 tools/mod_menu.py               # patch the main menu; --restore to undo
python3 tools/wadinfo.py <file.wad>     # chunk layout of one file
```

`tools/survey.py` and `roundtrip.py` are the regression tests — run them after any
change to the decoders.

## Game setup

US retail release (`Croc2.exe`, 737,280 bytes), running under a Wineskin wrapper at
`~/Applications/Croc 2.app`. All 97 disc files are checksummed in `game/wads.sha256`
and match hdc0's documented US release.

The game finds its data through the registry key
`Software\Argonaut Software\Croc2\1.00` → `CDPath = "D:\"`, and the prefix's
`dosdevices/d:` symlink points at `game/cd`. That is the whole modding loop: edit a
WAD in `game/cd/Wads/`, relaunch, see the change.

**Do not remount `CROC2.iso`.** Wineskin rewrites drive letters for mounted volumes on
every launch, so a mounted disc silently steals `d:` back and your edits stop reaching
the game with no error. `game/pristine/` exists so nothing needs the mount.

Screen Recording permission is not granted, so `screencapture` only returns the
desktop — the game cannot be verified from the terminal. Use `render3d.py` for
geometry, and a human for anything on screen.

Judge texture orientation from `render3d.py`, never from the top-down views. Overhead
orthographic flattens every wall to a line and shows the undersides of overhangs, which
reads as inverted textures and missing riverbanks even when the data is decoded
correctly. Two rendering bugs were chased for a long time this way before a perspective
camera showed both were artefacts of the view.

Note the backface rule in `render3d.py`: the viewport negates Y, so a triangle facing
the camera has a *negative* screen-space signed area. Getting that sign backwards
renders the world inside-out — ground vanishes, walls show their backs, and props look
scattered at random.
