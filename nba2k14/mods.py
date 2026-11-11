"""Install and uninstall mods without ever losing the game's original files.

A mod is a folder:

    mods/<mod name>/mod.json          {"name": ..., "version": ..., "description": ...}
    mods/<mod name>/files/<path>      replacement files, paths relative to the game folder

Installing copies each file into the game folder. Before a game file is replaced
for the first time, the original is copied to backups/<path>. Uninstalling
restores those originals (or deletes files the game didn't have). Two installed
mods can't replace the same file unless forced; the state lives in state.json.
"""
import json
import os
import shutil
import subprocess
import time

DEFAULT_GAME_DIR = r"C:\Program Files (x86)\2K Sports\NBA 2K14"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ModError(Exception):
    pass


def game_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq nba2k14.exe"],
                             capture_output=True, text=True, creationflags=0x08000000).stdout
        return "nba2k14.exe" in out.lower()
    except OSError:
        return False


class ModManager:
    def __init__(self, game_dir=DEFAULT_GAME_DIR, home=None, check_game=True):
        self.game_dir = game_dir
        self.home = home or os.path.join(HERE, "modmanager")
        self.mods_dir = os.path.join(self.home, "mods")
        self.backup_dir = os.path.join(self.home, "backups")
        self.state_path = os.path.join(self.home, "state.json")
        self.check_game = check_game
        os.makedirs(self.mods_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)
        self.state = self._load_state()

    # ----- state -----
    def _load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"installed": {}, "files": {}}

    def _save_state(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_path)

    # ----- mods -----
    def create(self, name, description="", version="1.0"):
        """Make an empty mod folder and return its files/ path."""
        folder = os.path.join(self.mods_dir, name)
        os.makedirs(os.path.join(folder, "files"), exist_ok=True)
        meta = os.path.join(folder, "mod.json")
        if not os.path.exists(meta):
            with open(meta, "w", encoding="utf-8") as f:
                json.dump({"name": name, "version": version, "description": description}, f, indent=1)
        return os.path.join(folder, "files")

    def available(self):
        mods = []
        for name in sorted(os.listdir(self.mods_dir)):
            meta = os.path.join(self.mods_dir, name, "mod.json")
            if os.path.isfile(meta):
                with open(meta, encoding="utf-8") as f:
                    info = json.load(f)
                info["folder"] = name
                info["files"] = self.mod_files(name)
                info["installed"] = name in self.state["installed"]
                mods.append(info)
        return mods

    def mod_files(self, name):
        root = os.path.join(self.mods_dir, name, "files")
        out = []
        for dirpath, _, files in os.walk(root):
            for fn in files:
                out.append(os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/"))
        return sorted(out)

    # ----- install / uninstall -----
    def _require_closed(self):
        if self.check_game and game_running():
            raise ModError("Close NBA 2K14 first.")

    def conflicts(self, name):
        return {rel: self.state["files"][rel]["owner"] for rel in self.mod_files(name)
                if rel in self.state["files"] and self.state["files"][rel]["owner"] != name}

    def install(self, name, force=False):
        self._require_closed()
        files = self.mod_files(name)
        if not files:
            raise ModError(f"Mod '{name}' has no files.")
        clash = self.conflicts(name)
        if clash and not force:
            raise ModError("These files are already changed by other mods: " +
                           ", ".join(f"{f} ({o})" for f, o in clash.items()))
        src_root = os.path.join(self.mods_dir, name, "files")
        for rel in files:
            dst = os.path.join(self.game_dir, rel)
            entry = self.state["files"].get(rel)
            if entry is None:
                had_original = os.path.exists(dst)
                if had_original:
                    bak = os.path.join(self.backup_dir, rel)
                    os.makedirs(os.path.dirname(bak), exist_ok=True)
                    if not os.path.exists(bak):
                        shutil.copy2(dst, bak)
                entry = {"original": had_original}
            entry["owner"] = name
            self.state["files"][rel] = entry
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(src_root, rel), dst)
            self._save_state()  # after every file, so a crash never loses track of a backup
        self.state["installed"][name] = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "files": files}
        self._save_state()

    def uninstall(self, name):
        self._require_closed()
        if name not in self.state["installed"]:
            raise ModError(f"Mod '{name}' isn't installed.")
        for rel in self.state["installed"][name]["files"]:
            entry = self.state["files"].get(rel)
            if not entry or entry["owner"] != name:
                continue  # another mod took this file over; leave it
            self._restore_original(rel, entry)
            del self.state["files"][rel]
            self._save_state()
        del self.state["installed"][name]
        self._save_state()

    def _restore_original(self, rel, entry):
        dst = os.path.join(self.game_dir, rel)
        bak = os.path.join(self.backup_dir, rel)
        if entry.get("original"):
            if not os.path.exists(bak):
                raise ModError(f"Backup of {rel} is missing - not touching it.")
            shutil.copy2(bak, dst)
        elif os.path.exists(dst):
            os.remove(dst)

    def adopt_original(self, rel, original_path):
        """Register an existing manual backup (e.g. file.iff.orig) as the original of `rel`."""
        bak = os.path.join(self.backup_dir, rel)
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        if not os.path.exists(bak):
            shutil.copy2(original_path, bak)
        return bak

    def restore_original(self, rel):
        """Put one game file back to its original, whoever changed it."""
        self._require_closed()
        bak = os.path.join(self.backup_dir, rel)
        if not os.path.exists(bak):
            raise ModError(f"No original saved for {rel}.")
        shutil.copy2(bak, os.path.join(self.game_dir, rel))
        entry = self.state["files"].pop(rel, None)
        if entry:
            owner = entry["owner"]
            inst = self.state["installed"].get(owner)
            if inst and rel in inst["files"]:
                inst["files"].remove(rel)
        self._save_state()
