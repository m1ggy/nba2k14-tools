# NBA 2K14 PC Tools

Small Windows tools for editing NBA 2K14 (PC) single-player saves and sliders,
with a focus on MyCareer.

> Unofficial fan tools. Not affiliated with 2K Sports. For offline/single-player
> use only. Always keep backups of your saves (the tools make one before every write).

## Tools

| Launcher | Script | What it does |
|---|---|---|
| `Live Sliders.bat` | `live_sliders.py` | Edit the game's **real** slider values while you play (works in MyCareer), and write them permanently into a MyCareer save. |
| `Ratings Tool.bat` | `ratings_tool.py` | Max or edit your MyPlayer's ratings and rating caps, and edit any league player's ratings in a MyCareer save. |
| `Slider Tool.bat` | `slider_tool.py` | Edit the slider block in `.CMG` / `.FXG` / `.STG` save files. |

### Requirements

- Windows, NBA 2K14 PC (tested with the October 2013 `nba2k14.exe`)
- Python 3.10+ with Tkinter (the standard python.org installer includes it)

Run a tool by double-clicking its `.bat` file. Keep the console window open while using it.

## Live Sliders (MyCareer)

1. Start NBA 2K14, then open `Live Sliders.bat`.
2. Tick **Lock** on the sliders you want and set the **User** / **CPU** values
   (or use a quick button, e.g. *Shooting: me 100 / CPU 0*).
3. While the light is green, locked values are kept in the game every half second.
4. To make them permanent, close the game, pick your save and click **Write to save**.

Don't change the **Game Style** in the game's menu afterwards, it can replace your sliders with a preset.

The app checks the game version before touching memory and does nothing on an unknown build.

## Modding toolkit (in progress, see ROADMAP.md)

- `tools/iffdump.py` - list what's inside any `.iff` game file.
- `tools/textures.py` - export textures (logos, courts, uniforms, faces) to PNG and import edited PNGs into a mod.
- `tools/modmgr.py` - install / uninstall mods; original game files are backed up in `modmanager/backups`.
- `tools/face_from_photo.py` - make a cyberface texture from a front-facing photo.

Setup for the toolkit: `python -m pip install --user -r requirements.txt`, and for faces
download the MediaPipe face model into `models/`:
`curl -L -o models/face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`

## Ratings Tool

- **My Player tab:** `MAX MY PLAYER` sets all ratings and caps to the maximum; or edit each rating.
- **League players tab:** search any player and raise/lower his ratings (e.g. -2 all).

The game must be closed when writing a save.

## How it works (format notes)

- Every save file starts with a big-endian CRC32 of the rest of the file; the tools recompute it.
- **Sliders** are 126 floats (0.0–1.0) = 63 User/CPU pairs, in this order:
  Stealing, Blocking, Speed, Ball Handling, Dunking Ability, Offensive Awareness,
  Defensive Awareness, Offensive Rebounding, Defensive Rebounding, Strength,
  Quickness, Hustle, Consistency, Durability, Stamina, Hands, Vertical,
  On-Ball Defense, Alley-Oop, Contact Shot, Inside Shot, Close Shot, Mid-Range,
  3PT, Layup, Dunk In Traffic Frequency, Dunk In Traffic Success, Backdoor Cuts,
  Pass Accuracy, Steal Success, Driving Contact, Inside Contact, Layup Defense
  (Takeoff, Release), Jumpshot Defense (Gather, Release), fouls, injuries,
  fatigue, and CPU tendencies (see `NAMES` in `live_sliders.py`).
  - In a MyCareer save (`.CMG`) they are big-endian at offset `0x6A84E1`.
  - In game memory the real values live at `[[0x1F207A8] + 0x350CA4] + 0xBBE30 + 4`
    (little-endian). The table at `0x130C970` is only a display copy.
- **MyPlayer ratings** in a `.CMG`: 42 bytes at `0x29E81A`, `raw = (rating - 25) * 3`.
- **Rating caps:** bit-packed 8-bit values starting at bit `0x702616*8 + 6`.
- **League players:** bit-packed 3644-bit records; ratings start 2812 bits after the name field.
- **Difficulty** in a `.CMG`: byte `0x691427` (0 Rookie … 4 Hall of Fame).
- `tunedata.iff` (game folder) holds the four Game Style slider presets.

## Credits

Slider names were cross-checked against community Cheat Engine tables for NBA 2K14.
