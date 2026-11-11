"""NBA 2K14 mod manager (command line).

    python tools/modmgr.py list
    python tools/modmgr.py install "My Court"
    python tools/modmgr.py uninstall "My Court"
    python tools/modmgr.py restore logo000.iff        # put one game file back to the original
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from nba2k14.mods import ModError, ModManager  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "install", "uninstall", "restore"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--force", action="store_true", help="install even if another mod changed the same files")
    args = ap.parse_args()
    mm = ModManager()
    try:
        if args.action == "list":
            mods = mm.available()
            if not mods:
                print(f"No mods yet. Put them in {mm.mods_dir}")
            for m in mods:
                flag = "[installed]" if m["installed"] else "           "
                print(f"{flag} {m['folder']}  v{m.get('version', '?')}  ({len(m['files'])} files)  {m.get('description', '')}")
        elif not args.name:
            ap.error("give a mod name (or a file for restore)")
        elif args.action == "install":
            mm.install(args.name, force=args.force)
            print(f"Installed {args.name}.")
        elif args.action == "uninstall":
            mm.uninstall(args.name)
            print(f"Uninstalled {args.name}; originals restored.")
        elif args.action == "restore":
            mm.restore_original(args.name)
            print(f"Restored original {args.name}.")
    except ModError as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
