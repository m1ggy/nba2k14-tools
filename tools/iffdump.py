"""List what's inside NBA 2K14 .iff files.

    python tools/iffdump.py "C:\\Program Files (x86)\\2K Sports\\NBA 2K14\\f000.iff"
    python tools/iffdump.py file.iff --extract out_dir     # write each chunk's raw data
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from nba2k14.iff import IffFile  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--extract", metavar="DIR", help="write each chunk's unpacked data to DIR")
    args = ap.parse_args()
    for path in args.files:
        f = IffFile.load(path)
        print(f.summary())
        if args.extract:
            os.makedirs(args.extract, exist_ok=True)
            base = os.path.splitext(os.path.basename(path))[0]
            for c in f.chunks:
                out = os.path.join(args.extract, f"{base}.{c.index}.{c.pool}.bin")
                with open(out, "wb") as fh:
                    fh.write(c.data)
                print(f"  wrote {out}")


if __name__ == "__main__":
    main()
