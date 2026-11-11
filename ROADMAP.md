# Roadmap

Goal: an open, documented modding toolkit for NBA 2K14 (PC) that does what the
community tools do (rosters, players, faces, uniforms, courts, arenas, shoes)
and does it better:

- **Open formats.** Every file format we decode is written down in `docs/`.
- **Reproducible mods.** A mod is source data plus a build script, not a hand-edited binary.
  Anyone can rebuild, update or fork it.
- **Non-destructive.** The game folder is never edited by hand. A mod manager installs
  and uninstalls files with automatic backups.
- **Automated.** Rosters from public data, faces from photos, uniforms from templates,
  and validation before anything touches the game.

---

## What we know today

| Area | Status |
|---|---|
| Save CRC, MyCareer ratings, caps, sliders, difficulty | Done (Ratings Tool, Live Sliders) |
| Real slider block in memory | Done (Live Sliders) |
| `.ROS` player records (3644 bits), names, 42 ratings | Partly decoded |
| `.IFF` container (`94 EF 3B FF`, zlib chunks, resource table) | Partly decoded. The game does **not** verify the trailer checksum (tested with `tunedata.iff`). |
| `tunedata.iff` (Game Style presets) | Decoded |

### Game asset files (found in the game folder)

| Files | Count | Contents |
|---|---|---|
| `png####.iff` | 1289 | **Cyberfaces**: `face_color`, `face_normal`, `face_occlusion`, `hair_color`, `hair_custom`, `chest_color` textures + `hihead` / `lohead` head meshes |
| `uh###.iff` / `ua###.iff` | 133 / 134 | **Home / away uniforms**: jersey, shorts, numbers, name font, bump and spec maps, `unidata` |
| `r1h` / `r1a` / `r2h` / `r3h` / `r4a`… | ~100 | Alternate and retro uniforms (same layout) |
| `f###.iff` | 114 | **Courts / floors**: `floor` texture + gloss/normal detail |
| `s###.iff` | ~70 | **Arenas**: stadium mesh, baskets, scoreboard, crowd, props, arena lights |
| `team_###.iff` | 128 | Team crowd colours, cheerleaders, mascot, flags |
| `logo###.iff` | 353 | Team logos |
| `led_###.iff` | 64 | LED ribbon / cube board animations |
| `dorna###.iff` | 80 | Courtside ad board slot layouts |
| `shoe_ed_###.iff` | 134 | Shoes (textures + masks) |
| `signature_####.iff` | 93 | Player signatures |
| `global.iff`, `gamedata.iff`, `career.iff`… | | Menus, UI, shared assets |
| `*.bin`, `audio_*.iff` | | Commentary and audio (out of scope for now) |

---

## Architecture

```
nba2k14/                 core library (pure Python, tested)
  crc.py                 save checksums
  bits.py                bit-packed read/write
  iff/                   IFF container: read, list, extract, rebuild, write
  texture/               DXT1 / DXT5 / BC5 decode + encode, PNG import/export
  mesh/                  SCNE meshes: vertices, normals, UVs, skin weights
  roster/                .ROS / .CMG player, team, string tables (field map in fields.json)
  saves/                 MyCareer / Association save helpers
apps/
  live_sliders, ratings_tool, slider_tool   (existing)
  roster_studio          roster editor (players, teams, trades, new players)
  face_studio            photo -> cyberface generator
  uniform_studio         jersey / shorts designer
  court_studio           floor / arena designer
  mod_manager            install / uninstall mod packs with backups
tools/
  rosdiff                field-mapping diff tool (in-game edit -> changed bits)
  iffdump                inspect any .iff
data/                    our own data only (field maps, templates, formulas)
docs/                    format documentation
tests/                   round-trip and golden-file tests
```

All apps run locally. A browser UI can be added on top of the same local
program (needed for live memory features, which a website can't do).

---

## Phases

### Phase 0: Foundation (do first)

1. Restructure the repo into the layout above. Keep the current apps working.
2. **IFF library**: list resources, extract a resource, replace it, rebuild the file.
   Test: unpack and rebuild every `.iff` in the game; the rebuilt files load in-game.
3. **Texture codec**: decode and encode DXT1/DXT5/BC5. Export any texture to PNG and import a PNG back.
   Test: export, import unchanged, and the game shows it identically.
4. **Mod manager v1**: a mod = folder with `mod.json` plus replacement files.
   Install copies the original to `backups/` first; uninstall restores it. Detect conflicts between mods.
5. `iffdump` CLI and docs for the IFF format.

**Deliverable:** swap a texture (for example a floor) with a PNG, install it, see it in the game, uninstall cleanly.

### Phase 1: Roster field map

1. `rosdiff`: compare two `.ROS` files and print changed bit ranges per player record.
2. Mapping sessions: make one edit in the game's Edit Player / Edit Team screens, save,
   diff, and record the field in `fields.json`. Covers vitals, position, team, jersey,
   ratings, tendencies, hot zones, signature skills, badges, contract, appearance,
   **cyberface ID**, shoes, accessories.
3. String tables: decode allocation so names can be added and renamed directly.
4. Roster library: read and write every mapped field. Test: reading and writing back gives an identical file.
5. Export and import full rosters as CSV / JSON.

**Deliverable:** `roster.csv` in and out of any `.ROS` with every field.

### Phase 2: Roster Studio

1. Editor app: search, edit any field, bulk edits (e.g. "all rookies -3 3PT"), undo, compare two rosters.
2. Teams: rosters, depth charts, trades, free agents, team colours and names.
3. **New players**: use free or created-player slots, set names through the string tables,
   then fill every field from CSV.
4. Apply a roster to an existing MyCareer or Association save (league table in `.CMG`).

### Phase 3: Asset studios (uniforms, courts, arenas, more)

Built on the IFF and texture libraries.

1. **Uniform Studio**
   - Export a team's jersey, shorts and numbers to layered PNGs with a UV guide.
   - Designer: base colours, stripes, wordmark and logo placement, number font and colour, name bar.
   - Generates all texture maps (colour, bump, spec) and writes `uh` / `ua` / alternate files.
   - Assign uniforms to teams and schedule alternates.
2. **Court Studio**
   - Export and import floor textures. Designer: wood type, paint and key colours, centre logo, baseline text, sideline art.
   - Arena: scoreboard and ad textures, LED boards (`led_`), courtside ads (`dorna`), crowd colours (`team_`).
3. **Logos, shoes, accessories**: import logos; recolour shoes from templates; headbands and sleeves later.
4. Live preview in the app (3D view of jersey, floor, head) before installing.

### Phase 4: Face Studio (photos to cyberface)

1. Decode `SCNE` head meshes (`hihead`, `lohead`): vertex format, UVs, skin weights. Rebuild unchanged. Test in-game.
2. **Texture-only faces**: put a photo onto an existing head that has a similar build.
   Face landmark detection (runs locally) aligns the photo to the template's UV layout.
   Skin-tone matching and edge blending fill the back of the head and the neck.
3. **Shaped heads**: deform the template head mesh toward landmarks from front and side
   photos (jaw, nose, eyes, forehead), keeping the topology the game expects.
4. Generate normal and occlusion maps from the new shape; choose hair from templates.
5. Write a new `png####.iff` with a free ID and assign it to the player (cyberface ID field from Phase 1).
6. Quality loop: side-by-side preview with the photos, sliders to adjust fit and skin.

### Phase 5: Real-world roster updates

1. Import current teams, players, positions, heights and contracts from public data sources (respecting their terms).
2. **Open ratings formula**: calculate the 42 ratings, tendencies and hot zones from real stats.
   The formula lives in `data/` and can be tuned by anyone.
3. Match players to existing cyberfaces by name; queue the rest for Face Studio.
4. One command builds a full updated roster mod from data.

### Phase 6: Packaging and sharing

1. Mod pack format (manifest, files, version, dependencies) and one-click install in the Mod Manager.
2. Browser UI for the studios (local server).
3. Docs site with the format specs.

---

## Risks and unknowns

| Risk | Plan |
|---|---|
| Mesh format (`SCNE`) is undocumented | Decode with round-trip tests; start with texture-only faces |
| Some textures may use unknown formats | Check headers across all files in Phase 0 before building studios |
| The game may cache or verify some files | Test every new file type in-game early, one at a time |
| Roster fields may depend on each other (e.g. counts, indexes) | Map with many small diffs; validate rosters before writing |
| Photo faces may look uncanny at first | Ship texture-only first; improve shape fitting iteratively |

## Rules

- Single-player use. No online features.
- Never publish 2K's files, other modders' files, or other people's photos without permission.
  Mods we share contain only our own data and generated assets.
- Only make faces from photos you have the right to use, and never of private people without their consent.
- Every write makes a backup first.
