# NBA 2K14 player records

Player records are bit-packed, **3644 bits** each, back to back. The same layout
is used in roster files (`.ROS`) and in the league table inside saves (`.CMG`,
`.FXG`). Positions below are bit offsets from the record's **name anchor** (the
last-name string-table id). All values are big-endian bit fields unless noted.

Found 2026-11-11 by matching fields against known 2013-14 player data in a
MyCareer save (24-28 well-known players) and against faces loaded in a running game.

| Field | Offset | Format | Notes |
|---|---|---|---|
| Last name | +0 | 16-bit table id + 16-bit string offset | UTF-16 string tables |
| First name | +32 | 16-bit table id + 16-bit string offset | |
| Height | +96 | float32 | centimetres (LeBron 203.2 = 6'8") |
| Weight | +128 | float32 | pounds |
| **Face (CF ID)** | +224 | 16-bit | face file `png%04d.iff`; unique per player; verified against faces loaded in game |
| Jersey number | +268 | 8-bit | 22 of 24 test players match |
| Position | +424 | 3-bit | 0 PG, 1 SG, 2 SF, 3 PF, 4 C |
| Birth date | ~+2560 | ? | correlates with birth year (0.97), packed format unknown |
| Ratings | +2812 | 42 × 8-bit | rating = raw / 3 + 25 (see Ratings Tool for slot names) |

Not in the player record: **team**. Records are stored grouped by team, but no
per-player team field was found; team membership is probably kept in team
records. To be mapped with an in-game edit (move a player, diff the roster).

## Files

- Saves (`.CMG`): string tables byte-aligned; `ratings_tool.scan_league()` finds every record.
- Roster files (`.ROS`): string tables are stored shifted by 5 bits. Shift the
  whole file left by 5 bits, then the same scan works (positions + 5).
- The MX 2024 roster: 1853 player records.

## Tools

- `tools/rosdiff.py before.ROS after.ROS` - which bits changed, per player record.
