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
- **`MAP` actor instances decoded — objects have positions**: 3,205 placements
  game-wide, with rotation, world position and a pointer into `STPC`'s actors.
  Confirmed by moving two of them and launching the game
- **The player spawn is `WalkingCroc`'s instance**, confirmed the same way: move it and
  Croc arrives somewhere else. This is the question an earlier round looked for in the
  zone records and did not find
- **`STPC` consumed exactly on all 75 parseable levels**, the same bar `MAP` and `TRAK`
  clear: object models (same format as `TRAK`), animation, and a file of 949 actor
  definitions whose extents are now known
- Level logic is data, not engine code: per-level named scripts and triggers ship in
  the WAD, which answers the long-open scripting question
- Zone `+0x28` points at an `STPC` model: all 165 non-null offsets land exactly on a
  model header boundary, which cross-validates the `STPC` model walk from another chunk
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

- **What is inside an actor.** Each of the 949 actor definitions is a size-prefixed blob
  whose extent we know and whose contents we do not. Object types, links and behaviour
  are in there, and the PS1 documentation calls it Argonaut Strategy Language. This is
  now the last large unknown in the level format
- Objects are placed but not yet *drawn*: the instance record points at an entry point
  inside its actor, not at a model index, so which mesh an object uses is still unread
- `t0i0m006` and `t0l1m002` use a `TRAK` variant we reject, and also fail the `MAP`
  and `STPC` walks
- The 44-byte `TRAK` records: layout known, meaning not. Not needed for rendering
- Zone field `+0x50`, non-zero almost only on default zones, passed to `FUN_00454da0`
- How the game decides which zone is active, and what the 335 point-shaped zone
  records mean as opposed to the 183 box-shaped ones

Settled negatives, so they are not chased again:

- Faces carry no stored UVs and no per-face UV rotation. The triangle flags do not
  select one — every flag value wants the default corner assignment on 90-98% of wall
  faces — and the trailing float at +0x18 is continuous, with 24,592 distinct values,
  so it is a plane offset rather than an orientation index.
- The `MAP` zone records are not object placements and hold no spawn point. Moving the
  one record per level that the loader singles out does not change where Croc arrives.
  The real placements are a different `MAP` section entirely, and the spawn is the
  `WalkingCroc` instance in it.
- Object placements are **not** in `STPC`. Two rounds went looking for them there
  because that is where the object names are; `STPC` holds the actor *definitions*, and
  the instances that place them are in `MAP`. The names and the positions live in
  different chunks, which is why looking for them together kept failing.
- Level logic is **not** hard-coded in the engine, which an earlier round of guessing
  concluded. `STPC` carries per-level named scripts and triggers: `setting level flag 6`,
  `Topdialog off`, `level flag 2 was set`, `lll open` / `lll not open`.
- `STPC` as a whole is not one bytecode stream. It is mostly models and animation, with
  the logic in a file of named actors. Whether an individual actor's payload is bytecode
  is still open, and the PS1 documentation suggests it is.

Ghidra runs in a container, so the host needs no JDK and nothing lands in the repo:

```sh
./ghidra/run.sh          # first run imports and analyses, ~2 min; then runs the script
```

It mounts `Croc2.exe` read-only, caches the analysed project in `ghidra/project/`, and
writes `ghidra/out/decompiled.c` — 872 functions, all of them, which is small enough to
grep. Chunk ids appear in the code as 32-bit immediates (`MAP ` is `0x4d415020`), and
searching for one leads straight to its loader. Set `CROC2_EXE` if your install differs.
The image is pinned to amd64: Ghidra ships no arm64 decompiler, so it runs emulated.

## Next steps

Roughly in order of value per effort. Each has a first concrete action and a test that
would say whether it worked, because the things that went wrong here went wrong by being
plausible rather than by being untested.

**1. Render an object.** The `STPC` model headers use the same sub-array sizes at the
same offsets as `TRAK`, so the existing mesh decoder should read them with the header
size changed from `0x80` to `0x88`. The model boundaries are corroborated twice over now:
every non-null zone `+0x28` lands on one, and the walk that produces them consumes the
chunk exactly. First action: run `trak.py`'s parser over the offsets in
`StpcChunk.models`, and export one to OBJ. It works if the result is a closed, sensibly
scaled mesh rather than noise. Watch for two differences: the extra `uint16` array at
`+0x82` that `TRAK` has no room for, and texture indices — the loader rewrites a `uint16`
in each triangle into a pointer to a 20-byte entry in a table at `DAT_004b5a94`, so
object materials may not index `TEXT` the way terrain does.

**2. Find the model an actor uses.** This is what stands between placed objects and drawn
objects. An instance points at an entry point inside its actor's payload, not at a model
index, so the link runs through the actor. First action: take the `b0 00 00 00 d4 00 00 00`
header that 84% of instances point at and find what reads it — those two words are
constant across most of the game, which makes them a shape rather than data. Cross-check
against `STPC`'s model count per level: whatever the field is, it has to stay inside it.
It works when each Sailor Village instance names a model and the mesh suits the label.

**3. Blender add-on.** Terrain is fully decoded and objects now have positions, so a
first pass could show both. Worth starting before the format work is finished, because
round-tripping a real edit through Blender will expose gaps that reading bytes will not.

**4. Finish the zone story.** Two loose ends: which zone the game picks as Croc moves,
and what the 335 point-shaped records mean next to the 183 boxes. First action: find the
readers of the zone array at `level+0x28c`, which should contain the containment test.
Cheap, and it would confirm the box reading from the other direction. `+0x50` and
`FUN_00454da0` fall out of the same search.

**5. The two demo maps.** `t0i0m006` and `t0l1m002` fail `TRAK`, `MAP` and `STPC`. One
variant probably explains all three, and fixing it would let every tool claim 77/77
instead of 75/77.

**6. The 44-byte `TRAK` render faces.** Layout known, meaning not. Not needed for
rendering, so this is only worth doing if something else comes to depend on it.

## What has and has not worked

Four methods, in rough order of how much they can settle:

1. **Read the loader.** Decompiling the chunk loader gives field widths and section
   order outright, with no guessing. This produced the whole `MAP` layout, `WFPC`, the
   `STPC` structure, and the actor instance record.
2. **Make the data falsify a claim.** A normal must match the geometry it belongs to,
   an index must stay inside its table, tiles must seam into a continuous surface, a
   parser must consume its chunk to the last byte. The exact-consumption test in
   particular has validated `TRAK`, `MAP` and `STPC`.
3. **Change it and launch the game.** Slow, and it needs a human at the screen, but it
   is the only method that has caught a wrong answer the other two agreed on — and the
   only one that can promote a reading from "consistent with everything we can measure"
   to settled. It confirmed the zone fields, and then the actor instances and the spawn
   point.
4. **Read the PS1 siblings' documentation.** `refs/PS1-BRender-Reverse` describes the
   same engine's PSX builds, and its section names are the PC ones with a different
   suffix. It handed over the `STPC` actors file and the actor instance record outright.
   It had been sitting in `refs/` cited only for texture code, while two rounds were
   spent guessing at things it documents — worth checking against any open question
   before opening a disassembler.

Guessing at byte patterns without such a test has repeatedly not worked. A column-entropy
scan and an autocorrelation probe both "found" a uniform 180-byte record in the `MAP`
tail that does not exist.

Two cautions earned the hard way, both worth re-reading before trusting a result:

- **A statistic that would pass by chance proves nothing.** All 518 zone records place
  two fields inside their level's grid bounds, which sounded conclusive and was not:
  the values are small and the grids are up to 128 wide.
- **"Distinguished" is not "identified".** Exactly one zone record per level is copied
  into a global of its own. That was read as a player spawn, and was in fact the default
  environment zone. The signature said the record was special, not what it meant. Moving
  it in-game cost two minutes and settled it.
- **Look for a field where the loader points, not where the names are.** The object
  placements were skipped for two rounds because the search followed the object names
  into `STPC`, while the loader was relocating a pointer *out of* `MAP` into it. The
  relocation is the tell: a field the loader adds a base address to is a reference, and
  chasing those found both the zone's model and the actor instances.

## Layout

```
croc2/          format library
  wad.py        container
  chunks/       lght, map_ (tiles, zones, actor instances, section walk),
                stpc (object models, animation, actors), trak, text
  level.py      MAP placements + TRAK meshes -> world mesh, textured OBJ export
  png.py        stdlib-only PNG writer for texture pages
  paths.py      where the game data lives
tools/          roundtrip, survey, render_level, render3d, mod_menu, wadinfo,
                mapwalk and stpcwalk (report whether the library's walks consume
                their chunk exactly), zones (surveys the MAP zone records),
                mod_zone (fog test)
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
python3 tools/render_level.py t1l1m001 --mode zones     # zone boxes drawn over the map
python3 tools/render_level.py t1l1m001 --mode actors    # actor instances, named
python3 tools/render3d.py t1l1m001 --target 10.4 1.0 -8.5 --yaw 180 --pitch 25 --dist 7
python3 tools/wadinfo.py <file.wad>     # chunk layout of one file
```

Chunk walkers. Each reports whether it consumed its chunk exactly, which is the test
that validates a layout:

```sh
python3 tools/mapwalk.py                # MAP, every level; exact on all 75
python3 tools/mapwalk.py t1l1m001       # one level, with section counts
python3 tools/stpcwalk.py                    # STPC, every level; exact on all 75
python3 tools/stpcwalk.py t1l1m001           # one level, with section counts
python3 tools/stpcwalk.py t1l1m001 --names   # that level's actors and their names
python3 tools/zones.py                  # survey every zone record field
python3 tools/zones.py --dump t1l1m001  # one level's zone records as a table
```

Game experiments, all idempotent — they rebuild from `game/pristine/` each run:

```sh
python3 tools/mod_menu.py               # red lights + lifted tiles in the main menu
python3 tools/mod_zone.py t1l1m001      # white close fog; proves the zone fields
python3 tools/mod_zone.py t1l1m001 --music 36    # swap a level's music track
python3 tools/mod_zone.py t1l1m001 --restore
python3 tools/mod_actor.py t1l1m001 --list       # the level's actor instances
python3 tools/mod_actor.py t1l1m001              # move Croc and a Gobbo; needs a human
python3 tools/mod_actor.py t1l1m001 --move 2 12 1.5 -7.5    # one instance, by hand
python3 tools/mod_actor.py t1l1m001 --restore
```

After writing an edit, `render_level.py --from-game` reads the copy the game loads
rather than the pristine one, which shows what the edit did before launching anything.

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
