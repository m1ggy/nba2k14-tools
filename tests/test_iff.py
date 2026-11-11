"""Round-trip tests for nba2k14.iff against the installed game files.

    python tests/test_iff.py            # every .iff in the game folder
    python tests/test_iff.py 50         # first 50 files only
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from nba2k14.iff import IffError, IffFile  # noqa: E402

GAME_DIR = os.environ.get("NBA2K14_DIR", r"C:\Program Files (x86)\2K Sports\NBA 2K14")


def check(path):
    f = IffFile.load(path)
    # 1. unchanged rebuild is byte-identical
    if f.build() != f.raw:
        return "rebuild differs"
    # 2. forced recompress unpacks to the same data and re-parses
    originals = [bytes(c.data) for c in f.chunks]
    for c in f.chunks:
        c.data = c.data
    g = IffFile(f.build())
    if [bytes(c.data) for c in g.chunks] != originals:
        return "recompressed data differs"
    if len(g.resources) != len(f.resources) or g.trailer != f.trailer:
        return "resources/trailer changed"
    if f.resource_count and not f.resources:
        return "resource table not parsed"
    # 3. VRAM placement: unpack to final layout and repack gives the stored bytes back
    if f.placement:
        h = IffFile(f.raw)
        stored = bytes(h.chunk("VRAM").data)
        h.vram_view()
        h.commit_vram_view()
        if bytes(h.chunk("VRAM").data) != stored:
            return "placement repack differs"
    return None


def main():
    files = sorted(glob.glob(os.path.join(GAME_DIR, "*.iff")))
    if len(sys.argv) > 1:
        files = files[:int(sys.argv[1])]
    t0 = time.time()
    bad, not_iff, placed = [], 0, 0
    for i, p in enumerate(files):
        try:
            err = check(p)
        except IffError:
            not_iff += 1
            continue
        except Exception as e:  # report and keep going
            err = f"{type(e).__name__}: {e}"
        if err:
            bad.append((os.path.basename(p), err))
        elif IffFile.load(p).placement:
            placed += 1
        if (i + 1) % 500 == 0:
            print(f"  {i + 1}/{len(files)} ...", flush=True)
    print(f"{len(files)} files in {time.time() - t0:.0f}s: {len(files) - len(bad) - not_iff} OK, "
          f"{len(bad)} failed, {not_iff} other formats; {placed} use a VRAM placement table")
    for name, err in bad[:30]:
        print(f"  FAIL {name}: {err}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
