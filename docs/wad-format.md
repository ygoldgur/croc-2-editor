# Croc 2 PC WAD format

What we have verified against the US retail disc. Anything marked UNKNOWN is not
guessed at in code — it is preserved as opaque bytes so edits stay lossless.

Prior art this builds on: [hdc0/Croc-2-mods](https://github.com/hdc0/Croc-2-mods)
(chunk list), [zeroKilo/Croc2ExplorerWV](https://github.com/zeroKilo/Croc2ExplorerWV)
(texture/RLE code), [OverSurge/PS1-BRender-Reverse](https://github.com/OverSurge/PS1-BRender-Reverse)
(PS1 equivalents). `TRAK` and `MAP` are documented as "unknown purpose" in those
sources; the layouts below are new work.

The PS1 repo turned out to be worth more than a texture reference. Its `DPSX` section
is `STPC`'s counterpart and runs in the same order — models, animation, optional extra
models behind the same flag bit, then a file of actors — and its Croc 2 level file
holds the same actor instance record this chunk keeps in `MAP`. Two of the sections
below were read by taking its layout and testing it against the PC data, which is
faster than either decompiling or guessing. Where the two disagree the PC loader wins,
since it is the code that actually reads these bytes.

## Container

```
uint32 file_size            unreliable; two levels disagree with the real size
repeat:
  char[4] id                stored reversed: "PAM " on disc means "MAP "
  uint32  payload_size      also unreliable
  bytes   payload
```

The engine ignores the declared size for most chunk types, and shipped files exploit
that: `t1l1m001` and `t5l2m001` both declare a wrong size for `MAP`. The parser
resyncs by scanning for the next valid chunk id and keeps the wrong value verbatim,
so round-tripping is byte-exact on all 79 WADs.

Level chunks, in order: `INFO VERS WFPC SMPC AMPC TEXT SPRT FONT TRAK STPC MAP LGHT
LGPC END`. `SMPC`/`AMPC` (audio) and `RIMG` are not present in every level.

`defanim.wad` and `fesound.wad` are not chunk containers and are handled as raw blobs.
`defanim.wad` turns out to be Croc's own animations, in exactly `STPC`'s animation record
format — see [Animation records](#animation-records).

## LGHT — lights

Fully decoded: parses with zero leftover bytes in all 77 levels.

```
uint32 count
per light:
  uint8  type
  uint8  r, g, b
  type 1 (directional): 3 floats  unit direction vector
  type 2 (positional):  5 floats  x, y, z, fade_from, fade_to
                        uint8     flag
```

Totals across the game: 231 directional, 851 positional. The positional layout
matches the `point_lights` structure CrocUtils documents for Croc 1.

## MAP — terrain tile placement

Not a tile array followed by a blob. The loader (`FUN_00422020`, reached from the chunk
dispatcher at `0x0048e5xx`) reads `MAP` as a **sequence of sections**, one field at a
time, through three `fread` wrappers that give every field's width away:

```
FUN_00418750   fread(dst, 1, 1, f)    1 byte
FUN_00418770   fread(dst, 2, 1, f)    2 bytes
FUN_00418790   fread(dst, 4, 1, f)    4 bytes
```

The whole chunk is now accounted for. `tools/mapwalk.py` transcribes the loader's read
order and **consumes all 75 parseable levels' `MAP` chunks to the last byte**; the only
failures are `t0i0m006` and `t0l1m002`, the same two demo maps that already fail `TRAK`.
That exact-consumption test is what makes the layout below trustworthy rather than a
guess, and it is the same test that validated `TRAK`.

Sections after the tile array, in read order. Sizes are **file** bytes, which are often
smaller than the in-memory struct the loader scatters them into:

```
uint32 n, n * uint32                    small leading array; 20 in Sailor Village
uint32 n, n * 90 bytes                  environment zones - see below (92 in memory)
uint32 n, n * 30 bytes                  40-byte struct in memory; actor instances
                                        index into it
256 bytes                               fixed: 32 pairs of uint32
grid_x * grid_z * uint32                one word per grid cell; values 0-6, not tile indices
tile_count * 24 bytes                   the tiles again, in fixed point (below)
tile_count * uint32                     a TRAK model index per tile
[0x10000]  uint32 n, n * 20 bytes       section D; pairs with the flagged tiles
per tile: vertex colours               see below
uint32 n, n * 48 bytes                  read byte by byte
uint32 n, uint32, n * 58 bytes          the actor instances - see below (64 in memory)
[0x200000] nested group section          innermost items are 12 bytes, or 16 with 0x4000000
[0x10]     uint32
uint16                                  final field
```

The second tile array repeats each tile's transform as `int32 rot_x, rot_y, rot_z, x,
y, -z`, with positions in 20.12 fixed point. `rot_y` is the tile's rotation, and the
other two are zero. It agrees with the float array exactly on all 34,650 tiles in the
75 levels. Lifting the float copy alone moved what the game drew (the main-menu test),
so the floats are what is rendered; the fixed copy is presumably what collision reads.
That has not been tested. `MapChunk.to_bytes` regenerates the copy from the floats, so
an edited tile stays consistent. The word per grid cell holds only the values 0 to 6,
so it is a per-cell class of some kind, not an index of the tile that fills the cell.

The per-tile vertex colours are the one data-dependent part. A row is one `uint32` per
vertex of that tile's model, with the vertex count read from the `TRAK` model header at
`model * 0x80 + 0x6c` — independently confirming both the 128-byte model stride and the
vertex count's position. The **high byte of the first row gives the number of rows**.
Tiles listed in section D then carry a second set of rows for that section's own model,
with the row count carried over from the first set rather than restarted.

### The feature flags are `WFPC`

Four sections are gated on bits of a global the loader reads through
`PTR_DAT_004a8c3c`. Those bits are exactly the contents of the level's **`WFPC` chunk**,
a four-byte word previously unexplained. Taking the flags straight from `WFPC` — no
searching, no per-level fitting — walks all 75 levels exactly:

```
0x10       always set; gates a trailing uint32
0x10000    section D, and with it the second set of per-tile vertex colours (13 levels)
0x200000   the nested group section (55 levels)
0x4000000  widens that section's innermost record from 12 to 16 bytes
```

This matters for editing: a tool that rewrites `MAP` has to keep `WFPC` consistent with
it, or the game will read the chunk at the wrong offsets.

Earlier statistical guesses at this region were wrong. A column-entropy scan and an
autocorrelation probe both suggested a uniform 180-byte record, which the code shows
does not exist; the apparent period came from several different sections interleaving.

The header and tile array are confirmed against the code:

```
uint32 tile_count
uint32 grid_x, grid_z        world grid extents; 4..128, not always square
per tile (24 bytes):
  float  x, y, z             world position, 1.0 grid at .5 offsets
  uint32 rotation            masked with 0xff0 by the loader, which then computes
                             degrees = rotation / 4096 * 360, confirming the 0x1000
                             full turn; anticlockwise about +Y seen from above
  uint32 model_id            index into TRAK
  uint32 flags               0 or 1; non-zero tiles pair with section D in order
```

Evidence: across 34,796 tiles the rotation field takes only those four values, and
every `model_id` falls inside its own level's `TRAK` model count. The tail is large
(13 KB in the main menu, 57 KB in Sailor Village).

The direction of the turn is settled by terrain continuity. Tiles are meant to seam
together, so neighbours share vertex positions exactly; turning them the wrong way
leaves 0 and 180 degree tiles untouched but swaps 90 with 270, tearing the mesh apart
along every seam involving one. Summed over all 77 levels, the anticlockwise reading
shares 220,042 vertices between neighbouring tiles against 160,916 for the other, and
it is what makes Sailor Village's terrain close up into a continuous surface.

This is worth knowing when judging a render: a wrong turn here looks like a *texture*
fault, because an affected tile keeps its footprint but presents its content backwards.
A riverbank facing inland was what exposed it, after a long detour spent looking for a
per-face UV rotation that does not exist.

Confirmed writable — editing tile `y` moves geometry in the running game.

## MAP zone records — fog, camera, music and reverb

The second section of the tail is the level's environment zones: 520 records across all
77 levels, 90 bytes each in the file, 92 in memory. Sailor Village has 14, the largest
level 44, and 33 levels have exactly one.

**These are not object or actor placements.** They were read that way at first, and the
game disproved it — see the dead end at the end of this section.

Fields are numbered by their offset in the 92-byte struct, not in the file; the two stop
agreeing after the `uint16` at `+0x38`.

```
+0x00  uint32   grid x        \
+0x04  uint32   grid y         | box origin, in cells
+0x08  uint32   grid z        /
+0x0c  uint32   size x        \
+0x10  uint32   size y         | box extent in cells, when the record is a box
+0x14  uint32   size z        /
+0x18  uint32
+0x1c  uint32
+0x20  uint32   flags: rotation in the high nibbles, bits 2 and 3 select box vs point
+0x24  uint32
+0x28  uint32   byte offset into the level's STPC blob, or 0
+0x2c  uint32
+0x30  uint32
+0x34  uint32   copied to a global, values 0..3
+0x38  uint16
+0x3c  uint32   fog near, 4.12 fixed point
+0x40  uint32   fog far,  4.12 fixed point
+0x44  uint32   a second near, usually equal to +0x3c
+0x48  uint32   a second far,  usually equal to +0x40
+0x4c  uint32   fog colour, 0x00RRGGBB
+0x50  uint32   passed to FUN_00454da0
+0x54  uint16   always 0
+0x56  uint16   reverb: type in bits 0-3, level in bits 8-14
+0x58  uint32   music track id, or 0 for none
```

The environment fields come straight from `FUN_00423ce0`, which applies a zone when it
becomes the active one:

```
_DAT_004b4238 = rec[+0x3c] * 0.00024414062          // 1/4096, so 4.12 fixed point
_DAT_004b423c = rec[+0x40] * 0.00024414062
_DAT_004b4244 = 1.0 / (far - near)                  // the usual fog scale
FUN_004209d0(rec[+0x4c] >> 16, >> 8, rec[+0x4c], 0xff)   // r, g, b, a
FUN_0047b190(rec[+0x58], 0)                              // start music track
_ADS_SetReverb_8(rec[+0x56] & 0xf, rec[+0x56] >> 8 & 0x7f)
```

The same function rebuilds the camera — angles converted with `* PI/180`, and near/far
planes taken from the fog range. That is what the per-zone rotation is for: Croc 2 swings
its camera to a fixed angle per area, and the zone carries that angle.

Confirmed in the game. Setting every Sailor Village zone to white fog at 1.0..5.0 instead
of grey at 6.0..9.0 made the level, in the tester's words, "foggier than London".

Three things agree with the reading independently:

- **Fog near is below fog far in all 520 records**, with no exceptions.
- **Every fog colour is greyscale**, R = G = B in all 520. That explains an otherwise
  strange branch in the loader, which forces the colour to grey off its green byte when
  a global flag is clear.
- Music ids span 0..46 with 44 distinct values, and reverb splits into a type of 0..8
  and a level of 0..24 — both plausible ranges for what the calls do with them.

### Box or point

The low byte of `+0x20` picks how to read `+0x0c`/`+0x10`/`+0x14`. With bit 2 or bit 3
set the record is a box on the tile grid and those fields are its extent in cells;
otherwise they are a world position in 4.12 fixed point, with z negated to match the
tiles' negative z.

The split holds up on the data. Counting over the 75 levels that fully parse: of the
335 point records, **every one** puts x and z on
half-unit boundaries and 98% land inside their own level's terrain — against 0% if z is
left unnegated. Of the 183 box records, 94-100% give a box that fits inside the grid.

Exactly one record per level has bit 3 set, in all 75 levels. That is the record the
loader copies into a global of its own, and `FUN_00423ce0` reads that global — so it is
the level's **default zone**, applied at load.

The rotation sits in the same 12-bit binary angle as the tiles, but zones use a finer
step: 505 of 518 records are on the 90-degree marks `0x000/0x400/0x800/0xc00`, and the
remaining 13 sit on `0x200` boundaries, so a zone camera can face 45 degrees where a tile
cannot.

### Dead end: these are not spawns

Bit 3 marking exactly one record per level, and that record being copied into a global,
looked exactly like a player spawn point. It is not, and the check that caught it was
cheap: move the record and launch the game.

Moving Sailor Village's flagged record from cell (3,1,0) to (10,0,17), on the far side of
the map, changed nothing about where Croc arrives. Only then did chasing the readers of
that global turn up `ADS_SetReverb` and the fog maths.

Worth keeping in mind for the rest of the tail: 43 of 77 of these flagged records sit off
the terrain entirely, clustered at small x with z = 0, which is fine for a default zone
but should have been a warning against reading them as positions. A "one per level,
stashed in a global" signature says a record is *distinguished*, not what it means.

### +0x28 points at an STPC model

`+0x28` is relocated by the loader against a base pointer, which first suggested a name
string. It is not one. The base is `level+0x1c`, and the `STPC` loader (`FUN_00421f40`)
allocates its chunk, reads it whole, and stores it at exactly that field — so **the
offset is a byte offset into the `STPC` blob**.

More precisely, it points at a *model*. Walking `STPC`'s model section gives the offset
of every model header, and **all 165 non-null zone offsets land exactly on one of those
boundaries, with none in between**. Since the two numbers come from different chunks
decoded by different routes, that is also the best evidence the `STPC` model walk is
right — the model section has no exact-consumption proof of its own yet.

The offset does not vary per record the way a label would: 58 of 75 levels give every
zone the same value, and the rest use two, usually `0` (which the loader treats as null)
and one real offset. So it is a shared per-level model reference.

What a fog-and-camera zone wants with a mesh is not yet known.

## MAP actor instances — where the objects stand

This is what places objects in the world: 3,205 records across the 75 parseable levels,
36 in Sailor Village, 58 bytes each in the file and 64 in memory. The loader reads them
into the array at `level+0x2a0`, one field at a time, so every width below is the code's
and not a guess.

They were passed over for a long time because the search was aimed at `STPC`, where the
object *names* live. The placements were never there. They are the section this walk had
already been counting and skipping as opaque bytes.

```
+0x00  uint16   rotation x   \
+0x04  uint16   rotation y    | 12-bit binary angle, as the tiles use
+0x08  uint16   rotation z   /
+0x0c  uint32   world x      \
+0x10  uint32   world y       | 4.12 fixed point, z negated
+0x14  uint32   world z      /
+0x18  uint32   byte offset into the STPC blob, landing inside an actor record
+0x1c  uint32
+0x20  uint32   index into MAP's small leading array, or -1
+0x24  uint32
+0x28  uint32
+0x2c  uint32
+0x30  uint32
+0x34  uint32   index into the 40-byte section above, or -1
+0x38  uint32   overwritten from +0x34 in a second pass over the array
+0x3c  uint32
```

Two fields identify themselves by being relocated as the loader reads them, which is the
same tell that cracked the zone records. `+0x18` is added to `level+0x1c`, the base of
the `STPC` blob. `+0x34` is turned into `section_c + n * 0x28` and bounds-checked against
that section's count, with a `"Erruuughh"` bailout if it overruns — so the 40-byte
records after the zones are per-instance data.

Unlike tiles, actors are not confined to quarter turns: the rotation fields use the whole
0..0x1000 range. In Sailor Village only the middle one varies, which is what a
ground-standing actor with a yaw and no pitch or roll looks like.

### Evidence

Rendering them is what settled it. `render_level.py --mode actors` draws each instance
over the textured map, labelled with the name of the actor it points at, and the result
is not a scatter of markers — it reads like a level:

- All three `Bubbles` instances stand in the water, and nowhere else.
- `WalkingCroc` is on the starting beach beside the boat.
- The two `Gobbo Says` markers sit on the two circular pads at the south end.
- The only two markers off the terrain are `CameraPC` and one unnamed actor.

Three counts back it up, and the third is the one that carries weight:

- **97% of all 3,205 instances stand inside their own level's terrain footprint.**
- Every instance resolves to an actor record. On its own this proves little — the actors
  file is one large contiguous span, so a wrong field would often land in it too.
- **Every instance's `+0x18` is word-aligned within its actor, and 84% point at bytes
  beginning `b0 00 00 00 d4 00 00 00`**, with 81 distinct signatures over 3,205 records.
  Alignment holding 3,205 times out of 3,205, and one signature covering most of them,
  is not something an unrelated `uint32` produces.

The PS1 build stores this same record — 0x40 bytes with the actor offset at `+0x18` — in
its level file, which is where the reading came from before any of the above was checked.

### Croc's spawn point — confirmed in the game

`WalkingCroc` is instance 0 in Sailor Village, and **it is the player spawn**. Moving that
instance to the trampoline's position at the far south end of the level, and moving
`Gobbo King 1` to where Croc used to start, does exactly what the reading predicts: Croc
arrives on the stone platform by the trampoline, and the Gobbo King is standing on the
beach he used to appear on. `tools/mod_actor.py` performs that edit.

Both halves matter. Croc arriving somewhere else settles the spawn question, which an
earlier round looked for in the zone records and did not find. The Gobbo arriving where
Croc used to be settles the position fields for actors in general, not just for the one
record the engine happens to spawn the player from.

Both land exactly on the intended spot rather than merely in the right area, which
settles more than the choice of fields: the 4.12 fixed-point scale and the negated z are
right too. A wrong divisor or an unnegated z would still have moved them, just not there.

Only 13 bytes change to do it, and the file size does not move.

## TRAK — terrain mesh library

```
uint32 model_count
model_count * 128-byte headers, contiguous
per-model data, same order, contiguous
```

Header (128 bytes):

```
+0x00  float                unknown
+0x04  float                unknown (bounding radius?)
+0x08  float                unknown
+0x0C  8 * 3 floats         AABB corners, binary-counting order (x fastest, then y, z)
+0x6C  uint16 vertex_count, uint16 triangle_count
+0x70  uint32 zero
+0x74  uint32 zero
+0x78  uint16 quad_count,  uint16 extra_count
+0x7C  float                unknown
```

Data per model:

```
vertex_count   * 24  float x,y,z + float unit normal
triangle_count * 28  the render triangles, see below
(quad+extra)   * 44  render faces, UNKNOWN
```

### The 28-byte render triangle

```
+0x00  uint16      flags; bit 0x10 marks the second triangle of a quad
+0x02  3 * uint16  vertex indices
+0x08  uint32      texture index, into the TEXT entry table
+0x0C  3 * float   face normal
+0x18  float       unknown; close to zero on flat tiles but not the plane offset
```

### Quads and UVs

Triangles are emitted in pairs forming a quad: one with bit `0x10` clear followed by
one with it set, sharing an edge and a texture. Walking the list that way accounts for
93.9% of faces as quads and 6.1% as lone triangles, with 6 exceptions game-wide.

**No UVs are stored anywhere.** The vertex record's second half is a unit normal to
within a float (checked on every vertex sampled), the triangle record is fully
accounted for above, and the 44-byte records are too rare to carry them. Instead the
mapping is implicit: corner *i* of a quad takes corner *i* of its texture rectangle,
in winding order. Rendering a level that way reproduces its art — continuous grass
borders, shorelines, bridges over rivers — and a single ground tile comes out as a
seamless 4x4 repeat with no discontinuity at any quad edge.

The other flag bits are not UV orientation. Rotating the corners for faces with bit
`0x08` or `0x20` set changes how often neighbouring faces agree on a shared corner's
UV by at most +1.2 points, and rotating by 1 and by 3 score alike — noise, where a
real encoding would produce one clear winner. Bits 2, 6 and 7 are never set; bit 11
is set on 96.9% of triangles and bit 15 on 60.3%, so they are likely render state.

`24*verts + 28*tris + 44*(quads+extra)` reproduces the chunk length exactly on 75 of
77 levels. The two that do not fit, `t0i0m006` and `t0l1m002` (demo/attract maps),
are rejected rather than parsed into garbage.

Sanity check: the single model in `t0l0m005` is 25 vertices forming a 5x5 grid over a
1x1 tile with all normals `(0,1,0)` — a flat ground tile subdivided into 16 quads.

Three independent facts pin the layout down, none of which need a disassembler:

- The vector at `+0x0C` is parallel to the normal computed from the triangle's own
  three vertices, for every triangle in every level tested. A field can only pass
  that test if it really is the face normal.
- The `uint32` at `+0x08` is always smaller than the level's texture-entry count, and
  its values are dense from zero — 290 distinct values with a maximum of 290, and so
  on per level. That is what an array index looks like and what an unrelated `uint32`
  does not. Consecutive triangles share it 78–100% of the time, matching quads split
  into triangle pairs.
- Tinting each triangle by the average colour of the texture it selects reproduces
  each level's real terrain: grass, river, beach and sea in Sailor Village, brown
  rock corridors in the cave levels. A wrong field yields noise.

Terrain uses only the leading 0–43% of each texture table; the rest presumably
belongs to actors and sprites.

### The 44-byte record (UNKNOWN)

Not decoded, and not needed for textured geometry. Its layout is clear even though its
meaning is not — byte offsets `0x0a/0x12/0x1a/0x22` and their neighbours only ever hold
`0x00` or `0xff`, which are sign-extension bytes:

```
+0x00  4 bytes     head; byte 0 is a class flag, and bit 0x04 of it is set on
                   exactly the faces counted by extra_count rather than quad_count
+0x04  4 groups    int16 a, int16 b, int32 c
+0x24  8 bytes     tail, usually zero
```

It is *not* the quad geometry: on a wall quad, `b` matches the world edge `dx` while
nothing matches `dy`, so the vertical extent is absent. Nor does record *k* correspond
to triangle pair *k* — correlating the fields against world edges over 15,872 pairs
gives coefficients below 0.21. An early apparent match on a flat tile was coincidence,
since all of that tile's quads share one set of edge vectors, so any ordering "fits".

Note these records are also simply absent from 726 of 6,946 models, which still render
and still reference textures.

## TEXT — textures

Parses on all 77 levels.

```
uint32 palette_count
per palette: uint32 unknown, uint32 colour_count, colour_count * 3 bytes RGB
uint32 texture_count
per texture: uint32 flags, uint32 width, uint32 height
  flags & 0x80 -> uint32 packed_size, then RLE16 data
  flags == 0   -> width*height bytes of palette indices
  else         -> width*height*2 bytes of raw 5:5:5
then the texture table:
  uint16 entry_count, uint16 pad
  per entry (8 bytes): uint8 flags, unknown, page, 0xFF, then u0, u1, v0, v1
```

In practice every level has 0 palettes and 10–19 RLE-compressed 256x256 pages, with
flags `0x81`–`0x87`. 16-bit pixels are `r | g<<5 | b<<10`.

The 8-byte entries carve atlas rectangles out of the pages, on a grid of roughly
32-pixel cells with 2-pixel gutters. 46,524 entries across the game. TRAK render
triangles select an entry by its position in this table, and `(u0,v0)..(u1,v1)` is the
rectangle whose corners the face's corners map onto.

`page` is either a valid page index or exactly `0xFF`, never anything else, across all
46,524 entries; `0xFF` marks a face with no texture (7,191 of them game-wide). The
fourth byte is `0xFF` throughout.

A face with no page is **flat-coloured**, and `u0, u1, v0` are its RGB. That is read off
the art rather than the code: a Gobbo's fur faces select an entry reading
`(255, 100, 0)`, its hat and mouth `(154, 0, 0)`, its eye whites `(255, 255, 255)`, and
drawn that way the model comes out in the right colours. `v1` is a small number (0–5)
of unknown meaning. Most Gobbo geometry is flat-coloured, which is why skipping these
faces drew a floating head. `TextureEntry.colour` returns the triple.

## STPC — object models, animation, and the actors

The largest chunk, 0.5 to 2.7 MB per level. It is not read field by field. The loader
(`FUN_00421f40`) allocates the whole chunk, reads it in one `fread`, and then walks it
patching internal pointers, so every apparent "allocation" is a bump of a cursor through
the blob itself. `croc2/chunks/stpc.py` replays that cursor arithmetic, and
**consumes all 75 parseable levels exactly**, the same bar `MAP` and `TRAK` clear.

```
uint32 n_models
n_models * { 0x88-byte header, then that model's arrays }
uint32 n_animations
n_animations * { 0x20-byte header, then that record's arrays }
[0x100] uint32 n_groups, then batched model groups
uint32 n_actors
n_actors * { uint32 size_in_words, then that many words }
```

Headers and their arrays alternate, one model at a time, because the loader calls its
fixup helper once per record. Only the flag-gated third section passes a batch, and
there the headers are contiguous.

### The models are TRAK meshes

`FUN_00424db0` walks 0x88-byte headers whose sub-arrays are 24, 28 and 44 bytes, read
from counts at `+0x6c`, `+0x6e` and `+0x78`/`+0x7a`. Those are exactly the sizes, at
exactly the offsets, of the terrain meshes in `TRAK`:

```
+0x6c  uint16 vertex_count     -> vertex_count * 24    position + normal
+0x6e  uint16 triangle_count   -> triangle_count * 28  the render triangles
+0x78  uint16 quad_count
+0x7a  uint16 extra_count      -> (quad + extra) * 44  render faces
+0x82  uint16 part_count       -> part_count * 2       vertices per part; TRAK has no room
```

The arrays follow the header in the order the fixup helper allocates them, and it writes
a pointer to each one back into the header as it goes, which names them:

```
+0x84   <- the +0x82 array     first
+0x70   <- vertices            then
+0x74   <- triangles           then
+0x7c   <- the 44-byte faces   last
```

The first 0x80 bytes of the header are `TRAK`'s, so the bounding box at `+0x0C` and the
counts are read from the same offsets. Two things differ, and only two: the array
positions, because `STPC` interleaves each model's header with its own data while `TRAK`
stores all headers first; and the triangle's texture field, which is a `uint16` at `+0x08`
that the loader overwrites with `DAT_004b5a94 + n * 0x14`.

**That table is `TEXT`'s.** It is filled by a loader that reads a `uint16` and six bytes
per entry — the same eight bytes as a `TEXT` atlas entry — and scatters them into 20-byte
structs, and later code indexes it for the page number and the u/v pair exactly as the
terrain renderer does. So object materials index the same texture table as terrain.
What differs is orientation: static STPC models are stored mirrored in X versus the
terrain/world convention, so rendering them correctly applies three steps together:
negate X, reverse face winding, and start UV corners at `+2` (equivalent to `ABCD ->
CDAB` in the old corner labels).

### Two kinds of model, and the split is visible before rendering

`croc2/chunks/stpc.py` decodes these into the same mesh type `TRAK` produces. The result
divides cleanly, on whether the model has the extra `uint16` array:

| | models | face normal matches geometry | header bbox matches vertices |
|---|---|---|---|
| no extra array | 1,596 | **100.00%** | **100.00%** |
| with extra array | 521 | 17.47% | 0.77% |

The first row is the same standard `TRAK`'s meshes meet, and it is what says the layout
above is right rather than merely plausible. Vertex normals are unit length on 99.88% of
the first group.

The second row is not a decoding failure. Those models are **articulated**, and the
numbers are what raw articulated data looks like; posed, they pass (next section).

The extra array and the 44-byte faces are close to mutually exclusive, which is worth
noting for whoever picks this up: 1,083 models have neither, 513 have only faces, 499 have
only the extra array, and 22 have both.

### Articulated models: the part table

The `+0x82` array is the part table: **one `uint16` per part, the number of consecutive
vertices that part owns**, in vertex order. Each part's vertices are stored in that
part's own local space, which is why they heap up at the origin when drawn raw. This is
the PS1 build's "vertex groups", stored as a count table instead of a per-vertex index:
the PC vertex is six floats with no room for one.

The counts sum to `vertex_count` exactly on 438 of the 521 models. On the other 83 they
fall short, and the vertices past the last part are **soft vertices**, which the animation
places separately (below). Croc is the main case: 5 parts own 308 of his 447 vertices.

Three things say this reading is right. The part counts are exactly the per-frame matrix
counts the animations use — Sailor Village's models have 4, 5 and 9 parts, and its 26
animations place 4, 5 or 9 matrices per frame, nothing else. The soft-vertex count matches
too: every animation that fits Croc's model carries data for exactly 139 vertices, his
447 − 308. And posed, the stored face normals agree with the geometry again (below).

### Animation records

`FUN_00424ef0` walks the second section. Each record is a 0x20-byte header, then a body:

```
+0x00  uint32 n0              -> n0 * 4 bytes, leading table (unknown)
+0x08  uint32 frame_count
+0x0C  uint32 gate_0c         -> if 0: frame_count * 8 bytes (unknown)
+0x10  uint32 soft_count      -> if non-zero: frame_count * 4 bytes (unknown)
                                 then always frame_count * 4 bytes (unknown, mostly 0)
+0x18  uint32 part_count
per frame:
  soft-vertex data            frame 0: soft_count * 8 bytes; later frames
                              soft_count * 2 bytes, padded to a word
  part_count * 64 bytes       one 4x4 float matrix per part
```

The matrices are row-major and take **row vectors**: a part-local vertex goes to model
space as `[x y z 1] · M`, translation in the fourth row. Every part has its own matrix
in model space, not relative to a parent — no hierarchy needs walking. Across all 363,729
matrices the fourth column is exactly `(0, 0, 0, 1)`. 76% are pure rotations; most of
the rest are rotations with a per-axis scale between 0.34 and 3.2, which reads as squash
and stretch, so the full 3×3 has to be applied rather than just its rotation.

The soft-vertex data is **not decoded**. Frame 0's eight bytes per vertex look like three
`int16` coordinates and a pad; the two bytes per vertex in later frames are mostly zero,
which suggests packed deltas. No scale tried makes Croc fill his box, so for now soft
vertices are left unplaced and the faces using them are not drawn.

`defanim.wad` is a file of these same records, which is why it never parsed as a chunk
container: a `uint32` byte count, `uint32` record count, another `uint32` byte count,
then 72 records that consume the file exactly. Every one has 5 parts and 139 soft
vertices — Croc's shared animations. `read_animation_file` reads it.

### Posed models pass the normal test

Vertices are stored part-local, but the face normals in the triangle records are stored
in model space **for the pose the model was exported in**. So there is a test that the
pose is right, not just plausible: pose the model by a frame, recompute each triangle's
normal from its posed corners, and compare with the stored one. Raw, Sailor Village's
Gobbo agrees on 2% of triangles; posed by animation 14 frame 0, on 100%.

`StpcChunk.export_pose` searches every frame of every animation that fits a model, and
`tools/survey.py` runs it game-wide over the 438 models without soft vertices:

| | models |
|---|---|
| some frame reproduces every stored face normal | 199 |
| some frame reproduces at least 95% | 4 |
| best frame reproduces less | 140 |
| no animation in the level fits | 95 |

A 100% match cannot happen by chance with hundreds of triangles, so the 199 settle the
matrix convention and the part table. The 140 are most likely models exported in a pose
no shipped frame reproduces: Sailor Village's Clipboard Gobbo matches 77% at best, and
rendered, it is a whole, correctly coloured Gobbo holding its clipboard. Bounding boxes
are a weaker test and not used: 150 models fill theirs exactly in frame 0, but a box can
also have been authored from a pose that never ships.

### Some object models are only flat-coloured

Three of Sailor Village's 28 models have no textured faces at all — every face selects
an entry with no page. This was first read as collision geometry, on the strength of a
`tramp collider` actor. That was wrong for at least two of them: a no-page entry carries
a flat RGB colour (see `TEXT`), and drawn in it they are a pair of binoculars and a
yellow jigsaw piece. The third, eight vertices and 1.4 tiles across, may still be the
collider.

### What the props are

The static models come out as recognisable Croc 2 objects, and they match the actor names
in the same level: the red `?` crate against `Smash Box toolbox`, a purple pot against
`Hub 1 Heart Pot`, a red heart pickup, two purple trampoline surfaces against
`trampoline`, a lettered clipboard against `Clipboard Gobbo`, and four wooden doors
against the level's five `CBKDoorTrigger` instances. The orientation fix above is
observable on live art: the crate face assembles with the frame on the perimeter (not
through the middle), and the board reads `Welcome to oki-doki` under the corrected
object convention.

This also explains something left dangling in the `TRAK` notes: the two `uint32` zeros
at `+0x70` and `+0x74` in every terrain model header. They are pointer slots, and this
is the code that fills them. `STPC`'s header is eight bytes longer than `TRAK`'s to fit
the extra array and its pointer.

So the object meshes are in the same format as the terrain meshes, and the existing
`TRAK` decoder should read them with only the header size changed.

### The actors file

`FUN_00421f40` returns after the third section, leaving 12-19% of the chunk unread on
levels where the `0x100` flag is clear and 1-6% where it is set. That is not a section
the loader skips by accident: nothing in it needs relocating, because `MAP`'s actor
instances point into it by absolute offset instead.

The layout is the PS1 build's, which documents this as its actors file:

```
uint32 n_actors
per actor: uint32 size_in_words, then size_in_words * 4 bytes of payload
```

Reading it that way **consumes `STPC` exactly on all 75 parseable levels**, which is the
proof; before this the chunk had no exact-consumption test of its own and rested on the
zone offsets landing on model boundaries. 949 actor definitions game-wide, 17 in Sailor
Village, from 24 bytes to 52 KB each. The largest is shared across levels and is almost
certainly Croc himself.

The payload is behaviour data and is **not** decoded — the PS1 documentation calls it
Argonaut Strategy Language, and the offsets `MAP` instances point at look like entry
points into it. Names appear inside it as plain nul-terminated strings padded with `0xcd`
(MSVC's uninitialised-memory fill), at no fixed offset, so `Actor.names` still finds them
by scanning. What changed is that a name is now scoped to one actor record rather than to
the whole chunk, which is what lets an instance be labelled. Sailor Village's:

```
WalkingCroc      CameraPC              Gobbo King 1        trampoline
tramp collider   Trampoline Gobbo      Clipboard Gobbo     Reward Gobbo
Topdialog off    setting level flag 6  Smash Box toolbox   Gobbo Says
Hub 1 Heart Pot  Distressed Gobbo      EmptyBoat           Bubbles
CBKDoorTrigger   Throwable Gobbo Pickup                    Croc Splash
```

631 distinct names across the game. Some are actors (`Golden Gobbo`, `BambooCage`,
`HeartFountain`), and some are unmistakably **script or trigger logic**: `setting level
flag 6`, `Topdialog off`, `GGWaitForCroc`, `bonusroomwarp`, `level flag 2 was set`,
`lll open` / `lll not open`. That settles the long-open question of whether level logic
is data or hard-coded: named, per-level, and shipped in the WAD.

The old guess that this chunk was "probably scripts" was half right in a way that would
have been misleading: the bulk of it is models and animation, and the logic is a file of
named actors in the tail, not a single instruction stream. Whether the actor payloads are
themselves bytecode is now the open question, and the PS1 documentation's "Argonaut
Strategy Language" says they are.

The chunk is fully consumed and each actor's extent is known, but what is *inside* an
actor is mostly not. That is where object types, links and behaviour live, and it is
the last large unknown in the level format.

### Actors name their models and animations

One thing inside an actor is decoded. The payloads are bytecode, and the word `124`
pushes the word after it as an immediate. When that immediate is the **absolute `STPC`
offset of a model or animation header**, the actor is naming that record. Entry points
open with it — Clipboard Gobbo's two entries both begin:

```
176 212 <n> 124 <model 6>  22 5 7
            124 <anim 9>   18 5 3
            124 <anim 10>  18 5 4    ... and so on through anim 13
```

With the opcodes decoded (next section) that reads: set the model to 6, then store each
animation into a local variable. The pattern was trusted before a game test, and a
game test has since confirmed it (below):

- **every one of the game's 1,380 animations** is pushed this way by some actor
  (`tools/survey.py` fails if one is not), as are 1,982 of the 2,117 models;
- each actor claims a consecutive run, in file order — Sailor Village's Gobbo King owns
  model 1 and animation 5, Clipboard Gobbo model 6 and animations 9–13, Gobbo Says model 9
  and 14–17, Distressed Gobbo models 12–14 and 18–24;
- the animations an actor pushes fit the model it pushes: same part count, same soft
  vertex count. Of the 836 instances whose model is articulated, 796 have one;
- WalkingCroc, the 52 KB actor, pushes exactly the five Croc animations its level ships.

**Confirmed in game.** `tools/mod_model.py --map 12=9 13=6` rewrote the Distressed
Gobbo's pushes of models 12 and 13 to Gobbo Says' model (purple box hat) and
Clipboard Gobbo's model. Every Distressed Gobbo seen turned into the Clipboard Gobbo,
and none took the purple hat. So the pushes really are the models. "First push"
(model 12) was the wrong choice, though: the entry code picks between them.

This replaced a signature-and-offset heuristic that read a field at a fixed distance
from the entry point. That gave the same number as the model and the animation for most
instances, and drew Croc's mesh on every door trigger.

### The actor VM

The bytecode runs on a stack machine in `Croc2.exe`. The handler for opcode *n* is the
pointer at `0x4af340 + 4n` (file offset `0xaf340`, in `.data`). Ghidra made none of the
handlers into functions, because nothing calls them directly, so they are missing from
`decompiled.c`; disassemble them with `objdump -d --start-address=...` instead. The VM
object holds pc at `+0xc`, the stack pointer at `+0xf8`, the locals through `+0xe4` and
the model at `+0x10`. Numbers are 20.12 fixed point.

| op | operands | effect |
|---|---|---|
| 10, 180 | n | push n |
| 124 | off | push `STPC + off` (negative: a global table), then rewrite itself to `10` |
| 1 | n | push local n |
| 5 | n | push the address of local n |
| 2 / 6 | n | push the code word / its address, n words past the operand |
| 4 | n | push actor property n (`FUN_00485010`) |
| 18 | | pop value, pop address, store |
| 14 | | add |
| 118 | | floor (clear the 12 fraction bits) |
| 152 | | pop n, push a random number below n (MSVC `rand`) |
| 137 | off | switch: pop a value, look it up in the case table `off` words on |
| 132 | n | jump n words |
| 126 | n | call: push the return address, jump n words |
| 22 | | pop a model pointer into the model |
| 176 | a, b | opens every entry; b sets a code pointer at `+0x18` |
| 111, 161, 177, 226 | n | set actor flags |

A case table is a count followed by `(10, value, target)` triples, where target is
counted in words from after the switch operand. If no case matches, execution falls
through.

The Distressed Gobbo's entry, decoded:

```
x = floor(rand(2.0) + 0.5) + 1          1, 2 or 3: a quarter, a half, a quarter
switch x: 1 -> model 12, 2 -> model 13, 3 -> model 14;  local 18 = x
```

`croc2/vm.py` follows entry code through these opcodes, carrying random values as
distributions, until each branch sets a model. `StpcChunk.instance_models` returns the
resulting chances, and `instance_model` the likeliest. For the Gobbo that is 13, and
the game agrees. The walk completes for 2,041 of the 3,205 instances. The rest stop at
an opcode that is not decoded, and fall back to the first model pushed at or after the
entry point (or anywhere in the actor). That fallback agrees with the walk on 2,033 of
the 2,041 instances it can be checked against. The disagreements are the 6 random
Gobbos and 2 `PushableIceBlock`s, whose entry stores two models in locals and sets the
second.

The fallback is still a guess. One Distressed Gobbo (instance 9, entry `+5024`) never
sets a model in its entry code. The fallback gives it model 12, but it did not show up
in a purple hat when 12 was swapped, so its model comes from somewhere not yet traced.
Which animation plays first is not decoded either: `StpcChunk.instance_animations`
returns the fitting ones in code order, and the tools use the first.

**The switch is confirmed in game.** `tools/mod_model.py --word 756=1` made the draw
`rand(1)`, which is always 0. Every Distressed Gobbo then wore model 12's red hat, as
the walk predicts.

The draw is random in the code but fixed in practice. `rand` is MSVC's LCG, with its
state at `0x4b0f40`. The state starts at 1, and nothing but `rand` itself touches it:
the game never calls `srand`. So every boot replays the same numbers, which is why
the Gobbos look the same after a restart. That the six of them all landed on 13 is just
where the sequence happened to fall. `instance_model` returns the likeliest branch,
which happens to match the game here. Predicting the actual draw would mean counting
every `rand` call (46 call sites) made before the level is set up.

## Level addressing

`Croc2.exe` has the format string `"Couldnt load wad tribe %d, level %d, map %d,
type %d"`, and WAD names follow `t<tribe><type><level>m<map>`:

```
tribe      l     i     b     s
0          7     7     0     0     front end; includes t0i0m999, the main menu
1          8     3     3     1
2          7     1     2     1
3          7     1     4     1
4          6     2     2     1
5         18     0     0     0     unidentified
```

`s` is the secret level, one per tribe, confirmed against the game. `b` is bosses,
`l` ordinary levels, `i` most likely the walkable overworld, of which tribe 0 holds
the front end. Tribe 5's 18 levels sit outside that pattern entirely.

The type slot is therefore *structural*, not mechanical: it says whether this is a
level, a boss or a secret, not how it plays. The snowball and hang-glider stages are
ordinary `l` levels, so whatever selects those mechanics lives elsewhere — hardcoded
against level ids, carried by an actor in the level, or in `STPC`. Decoding the `MAP`
tail should discriminate: if a mechanic rides on an actor, a glider level will contain
an actor type that ordinary levels never use.

## Not looked at

`SPRT`, `FONT`, `TRAK`/`WFPC` flag meanings, `SMPC`/`AMPC` audio, `LGPC` strings.
