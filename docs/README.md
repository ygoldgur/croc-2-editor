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
- **Object models decode and render**, textured, from the same `TEXT` table terrain uses.
  The 1,596 static models pass the `TRAK` tests exactly: every face normal matches its
  geometry, every declared bounding box matches its vertices
- **Articulated models pose and animate.** The `+0x82` array is a part table (vertices
  per part), and each animation frame is one model-space matrix per part. Posed in the
  right frame, 199 models reproduce every stored face normal, which no wrong pose does by
  chance. Sailor Village's Gobbos, the King and Croc render whole and move
- **Actors name their models and animations**: actor bytecode pushes the `STPC` offset of
  each record it uses, confirmed in game. Every animation in the game is named this
  way, so the scene draws its characters
- **Actor bytecode runs on a VM whose handler table is found** (`0x4af340`). About twenty
  opcodes are decoded, enough to run 2,041 of 3,205 instances' entry code to the
  model it sets. The Distressed Gobbo picks models 12, 13 or 14 with `rand`, 13 half the
  time. The game shows 13 on every boot, because `rand` is never seeded. Forcing the
  draw to 0 turned every one into model 12 in game, which confirms the switch
- Faces with no texture page are flat-coloured, and the colour is in the `TEXT` entry.
  Most of a Gobbo is flat-coloured
- `defanim.wad` is Croc's 72 shared animations, in `STPC`'s animation record format
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

- **What is inside an actor.** Each of the 949 actor definitions is a bytecode blob
  (Argonaut Strategy Language, per the PS1 documentation). The opcodes on the way to
  setting a model are decoded (`croc2/vm.py`); the remaining ~270 are where object
  types, links and behaviour live, and they are the last large unknown in the level
  format. 1,164 instances fall back to guessing their model as the first one pushed
- **Which animation plays first.** An instance's actor names several that fit its model;
  the tools use the first in code order, which is not known to be the one the game
  starts with
- **Soft vertices.** 83 models, Croc's included, have vertices outside every part, with
  per-frame data in the animation that is not decoded. Their faces are left undrawn, so
  Croc renders with holes
- 140 articulated models have no frame that reproduces all their face normals, probably
  because they were exported in a pose that does not ship. They render whole regardless;
  95 have no animation in their level that fits
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
  the logic in a file of named actors, whose payloads are bytecode.
- Untextured object faces are **not** collision geometry. They are flat-coloured, and
  skipping them is what made the first Gobbo renders look like floating heads and turned a
  jigsaw piece and a pair of binoculars into "collision only" models.
- Animation matrices are **not** relative to a rest pose or a parent part. The first
  renderer multiplied by the inverse of frame 0, which cancels the transform and draws
  the raw heap of parts; each matrix maps its part straight into model space.
- Vertices are **not** assigned to parts by nearness. The part table gives contiguous
  runs; nearest-translation clustering put limbs on the wrong parts.

Ghidra runs in a container, so the host needs no JDK and nothing lands in the repo:

```sh
./ghidra/run.sh          # first run imports and analyses, ~2 min; then runs the script
```

It mounts `Croc2.exe` read-only, caches the analysed project in `ghidra/project/`, and
writes `ghidra/out/decompiled.c` — the 872 functions Ghidra found, which is small enough
to grep. Chunk ids appear in the code as 32-bit immediates (`MAP ` is `0x4d415020`), and
searching for one leads straight to its loader. Set `CROC2_EXE` if your install differs.
Code reached only through a pointer table is missing: none of the actor VM's opcode
handlers are there. The host's `objdump -d --x86-asm-syntax=intel
--start-address=0x... Croc2.exe` disassembles them directly.
The image is pinned to amd64: Ghidra ships no arm64 decompiler, so it runs emulated.

## Next steps

Roughly in order of value per effort. Each has a first concrete action and a test that
would say whether it worked, because the things that went wrong here went wrong by being
plausible rather than by being untested.

**1. Decode more actor opcodes.** The model link is confirmed in game, and the entry
walk resolves 2,041 instances. The 1,164 it cannot finish stop mostly at opcodes 121,
16, 57, 254, 126, 73, 19, 35 and 79 (`tools/survey.py` counts the walks that finish). First
action: disassemble those handlers from the table at file offset `0xaf340` and add the
ones with a plain stack effect to `croc2/vm.py`. It works when the evaluated count goes
up with no new survey problems. The same walk, carried past `SET_MODEL`, should show
which animation an entry starts. Two things still need a game test: Distressed Gobbo
instance 9 (entry `+5024`, which sets no model), and the first animation.

**2. Decode the soft vertices.** This is what Croc still lacks. Frame 0 carries 8 bytes
per soft vertex, three `int16`s and a pad; later frames 2 bytes each. First action: find
the code that reads `+0x10` of an animation record, near `FUN_00424ef0`'s callers. It
works when Croc's posed mesh closes up, with no stretched or missing faces, and fills
his declared bounding box.

**3. Blender add-on.** Terrain, object placements, static meshes and posed, animated
characters all decode now, so a first pass could show a level much as the game does.
Worth starting before the format work is finished, because round-tripping a real edit
through Blender will expose gaps that reading bytes will not.

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
  chasing those found both the zone's model and the actor instances. The same idea found
  the actor-to-model link in a minute, after a round of fitting field offsets had not:
  search the payload for the exact offsets of the records it might mean.

## Layout

```
croc2/          format library
  wad.py        container
  chunks/       lght, map_ (tiles, zones, actor instances, section walk),
                stpc (object models, part tables, animation records and posing,
                actors and the records they name, defanim.wad), trak, text
  level.py      MAP placements + TRAK meshes -> world mesh, textured OBJ export;
                which model and animations each placed instance uses
  vm.py         the actor bytecode opcodes decoded so far; runs an entry to its model
  png.py        stdlib-only PNG writer for texture pages
  paths.py      where the game data lives
tools/          roundtrip, survey, render_level, render3d, mod_menu, wadinfo,
                render_object (draws the STPC object meshes, articulated ones posed),
                render_anim (one model through one animation, strip and GIF),
                animprobe and modelprobe (exploratory dumps of those records),
                mapwalk and stpcwalk (report whether the library's walks consume
                their chunk exactly), zones (surveys the MAP zone records),
                mod_zone (fog test), mod_actor (moves objects, spawn included)
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
python3 tools/render_level.py t1l1m001 --mode scene     # terrain plus posed objects
python3 tools/render3d.py t1l1m001 --target 10.4 1.0 -8.5 --yaw 180 --pitch 25 --dist 7
python3 tools/wadinfo.py <file.wad>     # chunk layout of one file
```

The objects out of `STPC`, textured, from an orbiting camera:

```sh
python3 tools/render_object.py t1l1m001            # a montage of the models
python3 tools/render_object.py t1l1m001 --list     # sizes, articulated ones' export pose
python3 tools/render_object.py t1l1m001 --model 8 --obj   # one model, large, plus OBJ
python3 tools/render_object.py t1l1m001 --raw      # articulated models unposed
```

Characters, through their animations:

```sh
python3 tools/render_anim.py t1l1m001 --actor 8 --list    # what instance 8's actor names
python3 tools/render_anim.py t1l1m001 --actor 8 --gif     # its model, first animation
python3 tools/render_anim.py t1l1m001 --actor 8 --anim 20 --gif   # another of its own
python3 tools/render_anim.py t1l1m001 --model 9 --anim 14 # any model, any animation
python3 tools/render3d.py t1l1m001 --scene --focus-index 8 --dist 0.9 --yaw 150
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
python3 tools/mod_model.py t1l1m001              # its model 12 push made model 9
python3 tools/mod_model.py t1l1m001 --map 12=9 13=6   # what proved the link
python3 tools/mod_model.py t1l1m001 --word 756=1      # rand(1): what proved the switch
python3 tools/mod_model.py t1l1m001 --all        # every model it names replaced
python3 tools/mod_model.py t1l1m001 --restore
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
