# NBA 2K14 `.iff` container format

Status: header, chunks, resource table and standalone textures decoded and tested.
`SCNE` (scenes with meshes and embedded textures) not decoded yet.

All integers are little-endian unless noted. Names and types are stored as
**CRC32 of their ASCII text** (e.g. `TXTR` = `0x5C369069`, `SCNE` = `0xE26C9B5D`,
`DRAM` = `0xBB05A9C1`, `VRAM` = `0x411536D5`).

## Layout

```
header   (header_size bytes)
chunks   one per memory pool, back to back
trailer  name table, copied unchanged on rebuild
```

### Header

| Offset | Size | Meaning |
|---|---|---|
| 0x00 | u32 | magic `0xFF3BEF94` (bytes `94 EF 3B FF`) |
| 0x04 | u32 | header size = file offset of the first chunk |
| 0x08 | u32 **big-endian** | end of chunk data = file offset of the trailer |
| 0x0C | u32 | 0 |
| 0x10 | u32 | chunk count |
| 0x14 | u32 | 13 (format version) |
| 0x18 | u32 | resource count |
| 0x1C | u32 | unknown |
| 0x20 | 32 × chunk count | chunk table |
| ... | | file-type specific words, then the resource table, padded with zeros to header size |

### Chunk table entry (8 × u32)

| Word | Meaning |
|---|---|
| 0, 1 | pool hash (same value twice): `DRAM` (CPU data), `VRAM` (GPU data), `SRAM` (sound) |
| 2 | alignment |
| 3 | unpacked (allocation) size |
| 4 | zlib level |
| 5 | file offset |
| 6 | packed size (including the 16-byte ZLIB header) |
| 7 | flags (usually 0) |

### Chunk data

Compressed chunks start with a 16-byte header, then a zlib stream:

```
"ZLIB"  u32 BE unpacked size  u32 BE packed size (incl. this header)  u32 BE level
```

Chunks without that header are stored uncompressed.

### Resource table

At the end of the header, `resource count` entries:

```
u32 name hash, u32 type hash, u32 n, u32 offset[n]   (offset into chunk 0..n-1)
```

Types seen: `TXTR` (texture), `SCNE` (scene: meshes + embedded textures),
`FxTweakables`, `UnAD` (uniform data), `NAME`, `RDAT`, `AUDO`, `TUNEDATA_RESOURCE`.

### Trailer

`u32 0x161517AA`, `u32 length of the rest`, then tables including the UTF-16
names of every resource. The game does not validate it; rebuilt files keep it unchanged.

## Rebuilding

Rewrite each chunk (recompress if changed), then update each chunk's file offset
and packed size in the chunk table, the ZLIB header, and the end-of-data offset
at header `0x08`. Unchanged chunks are copied byte-for-byte, so an unedited
rebuild is identical to the original. Tested on all 4,329 container files of the
PC game (306 other files use different formats).

## Textures (`TXTR`)

The resource's DRAM offset points to a record:

| Offset | Meaning |
|---|---|
| 0x00 | hash (shader/sampler, not always the resource name) |
| 0x08 | format: FourCC `DXT1` / `DXT5`, or D3D format number (21 = A8R8G8B8, 41 = P8) |
| 0x64 | packed flags (`02 1A 00 05` etc., not decoded) |
| 0x68 | u16 width |
| 0x6A | u16 height |

Pixel data: VRAM at the resource's VRAM offset, full mip chain, linear.
Verified visually for logos, uniforms (warmups, bump, spec) and floor detail maps.
**Verified in-game:** a rebuilt `logo000.iff` with a replaced logo texture loads and shows in the 76ers team select (2026-11-11).

### Textures inside scenes (`SCNE`)

Scene data in DRAM contains texture records with the same layout; +0x74 holds
(VRAM offset + 1) relative to the scene's VRAM offset. The record hash is the
CRC32 of 2K's original source path (listed in the trailer). Court floors
(`f###.iff`) expose all their art this way.
**Verified in-game:** replacing the 76ers centre logo in `f000.iff` (installed via the mod manager) shows on the court (2026-11-11).

### VRAM placement table (faces, uniforms and others) - SOLVED

Right after the chunk table some files have a **placement table** for the VRAM chunk:

```
u32 count, then count x (u32 code, u32 final offset)
code = (size_in_bytes << 10) | kind
kind = 0x80 + mip level | 0x9F mip tail | 0xBF raw data (e.g. head meshes)
```

The VRAM chunk stores the pieces **back to back in table order** (typically:
mesh block, all mip tails, all level 3s, ..., all level 0s). The game moves each
piece to its final offset; all resource and texture offsets refer to that final
layout. Sizes sum exactly to the stored chunk size. Files without the table
(logos, floors) store VRAM in final order already. `IffFile.vram_view()` gives
the final layout and `build()` repacks it.

Found by matching face file data against the running game's memory (each
texture sits in memory as a normal linear mip chain).
**Verified in-game:** striped `face_color` in 12 cyberfaces shows on the players (2026-11-11).

Note: in uniforms, `unifspec` / `shortspec` hold the jersey and shorts colour
art, and `unifbump` / `shortbump` are normal maps.
