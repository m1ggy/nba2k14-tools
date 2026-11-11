"""Export and import textures in NBA 2K14 .iff files.

    python tools/textures.py list    logo000.iff
    python tools/textures.py export  logo000.iff               # all textures -> PNGs next to you
    python tools/textures.py export  logo000.iff logo --out logo.png
    python tools/textures.py import  logo000.iff logo my_logo.png --mod "My Logos"

`import` never writes to the game folder: it puts the rebuilt file into the mod
folder (modmanager/mods/<mod>/files/), ready for `modmgr.py install`.
Files are looked up in the game folder when not found as given.
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from nba2k14 import texture as T  # noqa: E402
from nba2k14.iff import IffFile  # noqa: E402
from nba2k14.mods import DEFAULT_GAME_DIR, ModManager  # noqa: E402


def open_iff(name, mm):
    """Prefer the mod's current copy, then the saved original, then the game folder."""
    for cand in (name, os.path.join(mm.backup_dir, name), os.path.join(DEFAULT_GAME_DIR, name)):
        if os.path.isfile(cand):
            return IffFile.load(cand)
    raise SystemExit(f"Can't find {name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "export", "import"])
    ap.add_argument("file")
    ap.add_argument("texture", nargs="?")
    ap.add_argument("image", nargs="?")
    ap.add_argument("--out", help="output PNG (export) ")
    ap.add_argument("--mod", help="mod to put the rebuilt file in (import)")
    args = ap.parse_args()
    mm = ModManager(check_game=False)
    base = os.path.basename(args.file)

    if args.action == "import" and args.mod:
        # build on the mod's existing copy so several textures can go into one file
        existing = os.path.join(mm.mods_dir, args.mod, "files", base)
        iff = IffFile.load(existing) if os.path.isfile(existing) else open_iff(args.file, mm)
    else:
        iff = open_iff(args.file, mm)
    texs = {t.name: t for t in T.textures(iff)}

    if args.action == "list":
        for t in texs.values():
            note = "" if t.editable else "  (not editable yet)"
            print(f"{t.name:28} {T.format_name(t.format):9} {t.width}x{t.height}{note}")
        return 0

    if args.action == "export":
        names = [args.texture] if args.texture else [n for n, t in texs.items() if t.editable]
        for n in names:
            t = texs.get(n)
            if not t or not t.editable:
                print(f"skip {n}: not an editable texture")
                continue
            out = args.out if (args.out and args.texture) else f"{os.path.splitext(base)[0]}_{n}.png"
            Image.fromarray(t.image()).save(out)
            print(f"wrote {out} ({t.width}x{t.height})")
        return 0

    # import
    if not (args.texture and args.image and args.mod):
        ap.error("import needs: FILE TEXTURE IMAGE --mod NAME")
    t = texs.get(args.texture)
    if not t or not t.editable:
        raise SystemExit(f"{args.texture} isn't an editable texture in {base}")
    img = Image.open(args.image).convert("RGBA")
    if img.size != (t.width, t.height):
        print(f"resizing {img.size[0]}x{img.size[1]} -> {t.width}x{t.height}")
        img = img.resize((t.width, t.height), Image.LANCZOS)
    t.set_image(np.array(img))
    files_dir = mm.create(args.mod)
    out = os.path.join(files_dir, base)
    iff.save(out)
    print(f"Put {args.texture} into mod '{args.mod}' ({out}). Install with: python tools/modmgr.py install \"{args.mod}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
