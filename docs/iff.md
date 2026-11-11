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

**Exception: cyberfaces (`png####.iff`) - partly solved.**

The header words between the chunk table and the resource table are a **mip
placement table**:

```
u32 count, u32 hash, u32 0, then count x (u32 code, u32 final VRAM offset)
code = (size_in_bytes << 10) | 0x80 | mip_level      (level 0x9F = packed mip tail)
```

The final (virtual) VRAM layout is linear and matches the resource table, but
in face files the stored VRAM chunk is **not** in final order: after the
`hihead` data, the mip pieces are stored grouped by level (tails, level 3, 2,
1, then level 0), and the `lohead` data follows them. Each level group's start
is therefore known. Open questions:

- the order of pieces *within* a level group (the table order fits two of four textures);
- `chest_color` decodes noisy in every order tried (format may differ from its record);
- uncompressed textures (e.g. `hair_color`, A8R8G8B8) aren't listed in the placement table.

Measured in `png0045.iff` (file offsets, found by matching downscaled images):

| level | face_color | chest_color |
|---|---|---|
| 3 | 0x47C70 | 0x48C70 |
| 2 | 0x4AD40 | ~0x4ED40 |
| 1 | 0x57900 | 0x67900 |
| 0 | 0x8A870 | 0xCA870 |

So pieces are stored level by level (small to large), textures in table order
within a level, with gaps between levels (other textures' pieces + padding,
not yet modelled). Texture data starts ~0x46C20, after both head meshes.

Uniforms (`uh###.iff`) have the same table, but their stored order equals the
final order, so they decode directly.
