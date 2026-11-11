"""NBA 2K14 slider editor for MyCareer (.CMG), Association (.FXG) and slider (.STG) saves.

The save stores sliders as 126 big-endian floats (0.0-1.0 = 0-100): 63 User/CPU
pairs (names in live_sliders.NAMES). In a MyCareer save they sit at 0x6A84E1;
in other files the block is found by scanning. The first 4 bytes of every save
are a big-endian CRC32 of the rest, which is recomputed on save.

MyCareer saves also hold the difficulty (byte 0x691427: 0 Rookie .. 4 Hall of Fame).
The "Reset to" presets come from the game's tunedata.iff (Game Style presets).
"""
import os
import shutil
import struct
import subprocess
import time
import tkinter as tk
import zlib
from tkinter import messagebox, ttk

from live_sliders import NAMES

SAVES = os.path.join(os.environ["APPDATA"], "2K Sports", "NBA 2K14", "Saves")
GAME_DIR = r"C:\Program Files (x86)\2K Sports\NBA 2K14"
BLOCK_FLOATS = 126
PAIRS = BLOCK_FLOATS // 2
CMG_SIZE = 8195264
CMG_SLIDERS = 0x6A84E1
CMG_DIFFICULTY = 0x691427
DIFFICULTIES = ["Rookie", "Pro", "All-Star", "Superstar", "Hall of Fame"]
# guessed from their values (the game doesn't store preset names)
PRESET_NAMES = ["Default", "Casual", "Simulation", "Simulation 2"]


def block_ok(data, off):
    if off < 0 or off + BLOCK_FLOATS * 4 > len(data):
        return False
    vals = struct.unpack_from(">126f", data, off)
    return all(0.0 <= v <= 1.0 for v in vals)


def valid(data, off):
    raw = data[off:off + 4]
    if raw == b"\0\0\0\0":
        return True
    v = struct.unpack(">f", raw)[0]
    return 0.001 <= v <= 1.0


def scan_block(data):
    """Find the slider block in files without a fixed offset: the last 126 floats of a long 0-1 run."""
    best = None
    for align in range(4):
        off = align
        end = len(data) - 4
        while off < end:
            start = off
            while off < end and valid(data, off):
                off += 4
            count = (off - start) // 4
            if count >= BLOCK_FLOATS:
                blk = off - BLOCK_FLOATS * 4
                nonzero = sum(data[blk + 4 * k:blk + 4 * k + 4] != b"\0\0\0\0" for k in range(BLOCK_FLOATS))
                if nonzero >= 100 and (best is None or nonzero > best[1]):
                    best = (blk, nonzero)
            off = off + 4 if off == start else off
    return best[0] if best else None


def find_block(data):
    if len(data) == CMG_SIZE and block_ok(data, CMG_SLIDERS):
        return CMG_SLIDERS
    return scan_block(data)


def load_presets():
    """Game Style presets from tunedata.iff (prefers the untouched backup if one exists)."""
    presets = {"All 50": [50] * BLOCK_FLOATS}
    for name in ("tunedata.iff.orig", "tunedata.iff"):
        try:
            with open(os.path.join(GAME_DIR, name), "rb") as f:
                d = f.read()
        except OSError:
            continue
        i = d.find(b"ZLIB")
        try:
            raw = zlib.decompressobj().decompress(d[i + 16:]) if i >= 0 else d[84:84 + 2016]
            for t, pname in enumerate(PRESET_NAMES):
                vals = struct.unpack_from("<126f", raw, 504 * t)
                if all(0.0 <= v <= 1.0 for v in vals):
                    presets[f"Game Style: {pname}"] = [round(v * 100) for v in vals]
        except (zlib.error, struct.error):
            continue
        break
    return presets


def game_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq nba2k14.exe"],
                             capture_output=True, text=True, creationflags=0x08000000).stdout
        return "nba2k14.exe" in out.lower()
    except OSError:
        return False


def backups_of(path):
    """Backups of a save, newest first: (filename, modified time)."""
    folder, base = os.path.split(path)
    baks = [f for f in os.listdir(folder) if f.startswith(base + ".") and f.endswith(".bak")]
    baks = [(f, os.path.getmtime(os.path.join(folder, f))) for f in baks]
    return sorted(baks, key=lambda x: x[1], reverse=True)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("2K14 Slider Tool")
        self.geometry("660x760")
        self.data = None
        self.block = None
        self.path = None
        self.vars = []
        self.presets = load_presets()

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Save:").pack(side="left")
        self.file_var = tk.StringVar()
        self.combo = ttk.Combobox(top, textvariable=self.file_var, state="readonly", width=34)
        self.combo.pack(side="left", padx=6)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.open_file())
        ttk.Button(top, text="Refresh", command=self.refresh_files).pack(side="left")

        opts = ttk.Frame(self, padding=(8, 0, 8, 4))
        opts.pack(fill="x")
        ttk.Label(opts, text="Difficulty:").pack(side="left")
        self.diff_var = tk.StringVar()
        self.diff_combo = ttk.Combobox(opts, textvariable=self.diff_var, values=DIFFICULTIES,
                                       state="disabled", width=13)
        self.diff_combo.pack(side="left", padx=(4, 16))
        ttk.Label(opts, text="Reset sliders to:").pack(side="left")
        self.preset_var = tk.StringVar(value="Pick a preset...")
        preset_combo = ttk.Combobox(opts, textvariable=self.preset_var, values=list(self.presets),
                                    state="readonly", width=24)
        preset_combo.pack(side="left", padx=4)
        preset_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_preset())

        filt = ttk.Frame(self, padding=(8, 0))
        filt.pack(fill="x")
        ttk.Label(filt, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.build_rows())
        ttk.Entry(filt, textvariable=self.filter_var, width=25).pack(side="left", padx=6)
        self.status = ttk.Label(filt, text="")
        self.status.pack(side="left", padx=8)

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=8, pady=6)
        self.canvas = tk.Canvas(wrap, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(-e.delta // 120, "units"))

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Save changes", command=self.save).pack(side="right")
        ttk.Button(bottom, text="Reload", command=self.open_file).pack(side="right", padx=6)
        ttk.Button(bottom, text="Restore backup...", command=self.restore).pack(side="left")

        self.refresh_files()

    def is_cmg(self):
        return self.data is not None and len(self.data) == CMG_SIZE

    def refresh_files(self):
        files = [f for f in os.listdir(SAVES) if f.upper().endswith((".CMG", ".FXG", ".STG"))]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(SAVES, f)), reverse=True)
        self.combo["values"] = files
        if files and self.file_var.get() not in files:
            self.file_var.set(files[0])
        self.open_file()

    def open_file(self):
        name = self.file_var.get()
        if not name:
            return
        self.path = os.path.join(SAVES, name)
        with open(self.path, "rb") as f:
            self.data = bytearray(f.read())
        crc_ok = zlib.crc32(self.data[4:]) == struct.unpack(">I", self.data[:4])[0]
        self.block = find_block(self.data) if crc_ok else None
        self.vars = []
        if crc_ok and self.is_cmg() and self.data[CMG_DIFFICULTY] < len(DIFFICULTIES):
            self.diff_var.set(DIFFICULTIES[self.data[CMG_DIFFICULTY]])
            self.diff_combo.config(state="readonly")
        else:
            self.diff_var.set("")
            self.diff_combo.config(state="disabled")
        if not crc_ok:
            self.status.config(text="This file's checksum is wrong - it may be damaged. Try Restore backup.")
        elif self.block is None:
            self.status.config(text="No slider data found in this file.")
        else:
            self.status.config(text=f"Sliders at 0x{self.block:X}")
            for i in range(PAIRS):
                o = self.block + 8 * i
                u = round(struct.unpack_from(">f", self.data, o)[0] * 100)
                c = round(struct.unpack_from(">f", self.data, o + 4)[0] * 100)
                self.vars.append((tk.IntVar(value=u), tk.IntVar(value=c)))
        self.build_rows()

    def build_rows(self):
        for w in self.inner.winfo_children():
            w.destroy()
        if not self.vars:
            ttk.Label(self.inner, text=self.status.cget("text"), foreground="#a33").grid(row=0, column=0, padx=8, pady=8)
            return
        ttk.Label(self.inner, text="Slider", font=("", 9, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(self.inner, text="User", font=("", 9, "bold")).grid(row=0, column=1, columnspan=2)
        ttk.Label(self.inner, text="CPU", font=("", 9, "bold")).grid(row=0, column=3, columnspan=2)
        q = self.filter_var.get().lower()
        r = 1
        for i, (uv, cv) in enumerate(self.vars):
            if q and q not in NAMES[i].lower():
                continue
            ttk.Label(self.inner, text=NAMES[i], width=24).grid(row=r, column=0, sticky="w")
            for col, var in ((1, uv), (3, cv)):
                ttk.Scale(self.inner, from_=0, to=100, variable=var, length=130,
                          command=lambda v, var=var: var.set(round(float(v)))).grid(row=r, column=col, padx=2)
                ttk.Spinbox(self.inner, from_=0, to=100, textvariable=var, width=4).grid(row=r, column=col + 1, padx=(0, 10))
            r += 1

    def apply_preset(self):
        name = self.preset_var.get()
        vals = self.presets.get(name)
        if not vals or not self.vars:
            return
        if messagebox.askyesno("Reset sliders", f"Set all sliders to '{name}'?\n(Nothing is written until you click Save changes.)"):
            for i, (uv, cv) in enumerate(self.vars):
                uv.set(vals[2 * i])
                cv.set(vals[2 * i + 1])
            self.status.config(text=f"Loaded '{name}' - click Save changes to keep it")
        self.preset_var.set("Pick a preset...")

    def backup_current(self):
        backup = self.path + time.strftime(".%Y%m%d-%H%M%S.bak")
        shutil.copy2(self.path, backup)
        return backup

    def save(self):
        if not self.vars:
            return
        if game_running():
            messagebox.showwarning("Game running", "Close NBA 2K14 first, or the game will overwrite this save.")
            return
        for i, (uv, cv) in enumerate(self.vars):
            o = self.block + 8 * i
            for off, var in ((o, uv), (o + 4, cv)):
                try:
                    val = max(0, min(100, int(var.get())))
                except (tk.TclError, ValueError):
                    messagebox.showerror("Bad value", f"{NAMES[i]} has an invalid number.")
                    return
                old = round(struct.unpack_from(">f", self.data, off)[0] * 100)
                if val != old:  # leave untouched values bit-for-bit as they were
                    struct.pack_into(">f", self.data, off, val / 100)
        if self.is_cmg() and self.diff_var.get() in DIFFICULTIES:
            self.data[CMG_DIFFICULTY] = DIFFICULTIES.index(self.diff_var.get())
        struct.pack_into(">I", self.data, 0, zlib.crc32(self.data[4:]))
        self.backup_current()
        with open(self.path, "wb") as f:
            f.write(self.data)
        self.status.config(text="Saved (backup made)")

    def restore(self):
        if not self.path:
            return
        baks = backups_of(self.path)
        if not baks:
            messagebox.showinfo("No backup", "No backup found for this save.")
            return
        win = tk.Toplevel(self)
        win.title("Restore backup")
        win.geometry("520x360")
        win.transient(self)
        ttk.Label(win, padding=8, wraplength=490, text=(
            f"Pick a backup of {os.path.basename(self.path)} to restore (newest first). "
            "Your current file is backed up first, so this can be undone.")).pack(anchor="w")
        lb = tk.Listbox(win, height=12)
        lb.pack(fill="both", expand=True, padx=8)
        for f, t in baks:
            lb.insert("end", f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))}   {f}")
        lb.selection_set(0)

        def do_restore():
            sel = lb.curselection()
            if not sel:
                return
            if game_running():
                messagebox.showwarning("Game running", "Close NBA 2K14 first.", parent=win)
                return
            f = baks[sel[0]][0]
            src = os.path.join(os.path.dirname(self.path), f)
            if not messagebox.askyesno("Restore", f"Replace {os.path.basename(self.path)} with\n{f}?", parent=win):
                return
            safety = self.backup_current()
            shutil.copy2(src, self.path)
            os.utime(self.path)  # mark as newest so the game and tools see it as current
            win.destroy()
            self.open_file()
            messagebox.showinfo("Restored", f"Restored {f}.\nYour previous file was saved as\n{os.path.basename(safety)}")

        btns = ttk.Frame(win, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="Restore selected", command=do_restore).pack(side="right")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=6)


if __name__ == "__main__":
    App().mainloop()
