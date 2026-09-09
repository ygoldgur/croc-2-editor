# Croc 2 PC WAD format

What we have verified against the US retail disc. Anything marked UNKNOWN is not
guessed at in code — it is preserved as opaque bytes so edits stay lossless.

Prior art this builds on: [hdc0/Croc-2-mods](https://github.com/hdc0/Croc-2-mods)
(chunk list), [zeroKilo/Croc2ExplorerWV](https://github.com/zeroKilo/Croc2ExplorerWV)
(texture/RLE code), [OverSurge/PS1-BRender-Reverse](https://github.com/OverSurge/PS1-BRender-Reverse)
(PS1 equivalents). `TRAK` and `MAP` are documented as "unknown purpose" in those
sources; the layouts below are new work.

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

Sections after the tile array, in read order, as far as the decompilation has been
walked (the middle of the function is long and not yet fully transcribed):

```
uint32 n, n * uint32                    the small leading array; 20 in Sailor Village
uint32 n, n * 90 bytes                  92-byte struct in memory; field at +0x28 is
                                        relocated against a string base, so these
                                        records are named - the actor candidates
uint32 n, n * 30 bytes                  40-byte struct in memory
256 bytes                               fixed: 32 pairs of uint32
grid_x * grid_z * uint32                one word per grid cell
tile_count * 24 bytes
tile_count * uint32
...                                     further sections, not yet transcribed
uint32 n, n * 48 bytes                  read byte by byte
uint32 n, uint32, n * 64-byte structs   entries cross-reference earlier sections
...                                     two sections gated on global feature flags
uint16                                  final field
```

Earlier statistical guesses at this region were wrong. A column-entropy scan and an
autocorrelation probe both suggested a uniform 180-byte record, which the code shows
does not exist; the apparent period came from several different sections interleaving.
Nothing here should be trusted until the parser walks the whole chunk and consumes it
exactly, the same test that validated `TRAK`.

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
  uint32 flags               0 or 1
<UNKNOWN tail>               actor/object instances live here
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

## STPC — largest chunk, contents unknown

Often described as holding scripts, on the strength of the name alone; the community
doc that says so marks it "probably". Nothing verifies it yet, and what can be measured
does not obviously support it:

- 0.5 to 2.7 MB per level, far more than scripts would need
- level-specific: `t1l1m001` and `t2l1m001` share no common prefix at all, and only
  6.2% of 512-byte blocks occur in more than one level
- no text of any kind; entropy is 6.5 bits/byte and the byte histogram clusters at
  0x3D-0x3F, the high bytes of small IEEE floats

That profile fits per-level model and animation data much better than bytecode. A
script section could still be embedded in it. `Croc2.exe` contains no strings naming
opcodes, script files or a compiler, which is weak evidence either way, since a
numeric-opcode interpreter needs no strings.

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
