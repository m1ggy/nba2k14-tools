"""Mod manager tests on a fake game folder.

    python tests/test_mods.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from nba2k14.mods import ModError, ModManager  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


def main():
    tmp = tempfile.mkdtemp()
    try:
        game = os.path.join(tmp, "game")
        write(os.path.join(game, "logo000.iff"), "original logo")
        write(os.path.join(game, "f000.iff"), "original floor")
        mm = ModManager(game_dir=game, home=os.path.join(tmp, "mm"), check_game=False)

        a = mm.create("Logos")
        write(os.path.join(a, "logo000.iff"), "new logo")
        write(os.path.join(a, "newfile.iff"), "brand new")
        b = mm.create("Court")
        write(os.path.join(b, "f000.iff"), "new floor")
        write(os.path.join(b, "logo000.iff"), "court logo")

        mm.install("Logos")
        assert read(os.path.join(game, "logo000.iff")) == "new logo"
        assert read(os.path.join(game, "newfile.iff")) == "brand new"

        try:
            mm.install("Court")
            raise AssertionError("conflict not detected")
        except ModError:
            pass
        assert read(os.path.join(game, "f000.iff")) == "original floor", "nothing written on conflict"

        mm.install("Court", force=True)
        assert read(os.path.join(game, "logo000.iff")) == "court logo"
        mm.uninstall("Logos")  # Court owns logo000 now: must stay
        assert read(os.path.join(game, "logo000.iff")) == "court logo"
        assert not os.path.exists(os.path.join(game, "newfile.iff")), "added file removed"
        mm.uninstall("Court")
        assert read(os.path.join(game, "logo000.iff")) == "original logo"
        assert read(os.path.join(game, "f000.iff")) == "original floor"
        assert mm.state == {"installed": {}, "files": {}}

        # reload from disk
        mm2 = ModManager(game_dir=game, home=os.path.join(tmp, "mm"), check_game=False)
        assert mm2.state == {"installed": {}, "files": {}}
        print("mod manager: all tests passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
