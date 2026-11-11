"""Make a cyberface from a photo.

    python tools/face_from_photo.py photo.jpg --template png0805.iff --preview out.png
    python tools/face_from_photo.py photo.jpg --template png0805.iff --mod "My Face"

--template is an existing face file whose head shape and layout are reused.
--mod writes the result into a mod (as the template's file name, i.e. it replaces
that player's face when installed). --preview saves the new texture as a PNG.
Use a sharp, front-facing photo with even light and a neutral expression.
"""
import argparse
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from nba2k14 import face  # noqa: E402
from nba2k14.iff import IffFile  # noqa: E402
from nba2k14.mods import DEFAULT_GAME_DIR, ModManager  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photo")
    ap.add_argument("--template", required=True, help="face file to build on, e.g. png0805.iff")
    ap.add_argument("--mod", help="put the new face file into this mod")
    ap.add_argument("--preview", help="save the new face texture as PNG")
    ap.add_argument("--strength", type=float, default=1.0, help="skin tone matching, 0-1")
    ap.add_argument("--skin", choices=["body", "photo"], default="body",
                    help="body: keep the player's skin tone (matches his body in game); photo: use the photo's")
    args = ap.parse_args()

    mm = ModManager(check_game=False)
    base = os.path.basename(args.template)
    src = next((p for p in (args.template, os.path.join(mm.backup_dir, base), os.path.join(DEFAULT_GAME_DIR, base))
                if os.path.isfile(p)), None)
    if not src:
        raise SystemExit(f"Can't find {args.template}")
    iff = IffFile.load(src)
    try:
        img = face.apply_to_iff(iff, face.load_photo(args.photo), strength=args.strength, skin=args.skin)
    except face.FaceError as e:
        raise SystemExit(f"Error: {e}")
    if args.preview:
        Image.fromarray(img[..., :3]).save(args.preview)  # colour only; alpha is a shading mask
        print(f"preview: {args.preview}")
    if args.mod:
        out = os.path.join(mm.create(args.mod), base)
        iff.save(out)
        print(f"Wrote {out}. Install with: python tools/modmgr.py install \"{args.mod}\"")
    if not (args.preview or args.mod):
        print("Nothing saved: add --preview and/or --mod")


if __name__ == "__main__":
    main()
