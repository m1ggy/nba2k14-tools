"""NBA 2K14 slider editor for MyCareer (.CMG), Association (.FXG) and slider (.STG) saves.

The save stores sliders as a block of 126 big-endian floats (0.0-1.0 = 0-100):
4 leading settings, then 61 User/CPU pairs. The first 4 bytes of the file are a
big-endian CRC32 of the rest, which is recomputed on save.

Slider names live in names.json next to this script. Only the first five are
confirmed; rename the rest there once you identify them in-game.
"""
import json
import os
import shutil
import struct
import subprocess
import time
import tkinter as tk
import zlib
from tkinter import messagebox, ttk

SAVES = os.path.join(os.environ["APPDATA"], "2K Sports", "NBA 2K14", "Saves")
HERE = os.path.dirname(os.path.abspath(__file__))
NAMES_FILE = os.path.join(HERE, "names.json")
BLOCK_FLOATS = 126
LEAD = 4
PAIRS = (BLOCK_FLOATS - LEAD) // 2

DEFAULT_NAMES = ["Inside Shot Success", "Close Shot Success", "Mid-Range Success",
                 "3PT Success", "Layup Success"] + [f"Slider {i}" for i in range(6, PAIRS + 1)]


def load_names():
    try:
        with open(NAMES_FILE, encoding="utf-8") as f:
            names = json.load(f)
        return (names + DEFAULT_NAMES[len(names):])[:PAIRS]
    except (OSError, ValueError):
        with open(NAMES_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_NAMES, f, indent=2)
        return DEFAULT_NAMES[:]


def valid(data, off):
    raw = data[off:off + 4]
    if raw == b"\0\0\0\0":
        return True
    v = struct.unpack(">f", raw)[0]
    return 0.01 <= v <= 1.0


def find_block(data):
    """Return offset of the slider block: the last 126 floats of a long run of 0-1 values."""
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


def game_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq nba2k14.exe"],
                             capture_output=True, text=True, creationflags=0x08000000).stdout
        return "nba2k14.exe" in out.lower()
    except OSError:
        return False


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("2K14 Slider Tool")
        self.geometry("620x720")
        self.names = load_names()
        self.data = None
        self.block = None
        self.path = None
        self.backed_up = set()
        self.vars = []

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Save:").pack(side="left")
        self.file_var = tk.StringVar()
        self.combo = ttk.Combobox(top, textvariable=self.file_var, state="readonly", width=40)
        self.combo.pack(side="left", padx=6)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.open_file())
        ttk.Button(top, text="Refresh", command=self.refresh_files).pack(side="left")

        filt = ttk.Frame(self, padding=(8, 0))
        filt.pack(fill="x")
        ttk.Label(filt, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.build_rows())
        ttk.Entry(filt, textvariable=self.filter_var, width=25).pack(side="left", padx=6)
        self.status = ttk.Label(filt, text="")
        self.status.pack(side="left", padx=8)

        # scrollable slider list
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
        ttk.Button(bottom, text="Restore backup", command=self.restore).pack(side="left")

        self.refresh_files()

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
        self.block = find_block(self.data)
        crc_ok = zlib.crc32(self.data[4:]) == struct.unpack(">I", self.data[:4])[0]
        if self.block is None or not crc_ok:
            self.status.config(text="Slider block not found" if crc_ok else "Unknown file format")
            self.vars = []
        else:
            self.status.config(text=f"Block at 0x{self.block:X}")
            self.vars = []
            for i in range(PAIRS):
                o = self.block + (LEAD + 2 * i) * 4
                u = round(struct.unpack_from(">f", self.data, o)[0] * 100)
                c = round(struct.unpack_from(">f", self.data, o + 4)[0] * 100)
                self.vars.append((tk.IntVar(value=u), tk.IntVar(value=c)))
        self.build_rows()

    def build_rows(self):
        for w in self.inner.winfo_children():
            w.destroy()
        if not self.vars:
            return
        ttk.Label(self.inner, text="Slider", font=("", 9, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(self.inner, text="User", font=("", 9, "bold")).grid(row=0, column=1, columnspan=2)
        ttk.Label(self.inner, text="CPU", font=("", 9, "bold")).grid(row=0, column=3, columnspan=2)
        q = self.filter_var.get().lower()
        r = 1
        for i, (uv, cv) in enumerate(self.vars):
            if q and q not in self.names[i].lower():
                continue
            ttk.Label(self.inner, text=self.names[i], width=24).grid(row=r, column=0, sticky="w")
            for col, var in ((1, uv), (3, cv)):
                ttk.Scale(self.inner, from_=0, to=100, variable=var, length=130,
                          command=lambda v, var=var: var.set(round(float(v)))).grid(row=r, column=col, padx=2)
                ttk.Spinbox(self.inner, from_=0, to=100, textvariable=var, width=4).grid(row=r, column=col + 1, padx=(0, 10))
            r += 1

    def save(self):
        if not self.vars:
            return
        if game_running():
            messagebox.showwarning("Game running", "Close NBA 2K14 first, or the game will overwrite this save.")
            return
        for i, (uv, cv) in enumerate(self.vars):
            o = self.block + (LEAD + 2 * i) * 4
            for off, var in ((o, uv), (o + 4, cv)):
                try:
                    val = max(0, min(100, int(var.get())))
                except (tk.TclError, ValueError):
                    messagebox.showerror("Bad value", f"{self.names[i]} has an invalid number.")
                    return
                old = round(struct.unpack_from(">f", self.data, off)[0] * 100)
                if val != old:  # leave untouched values bit-for-bit as they were
                    struct.pack_into(">f", self.data, off, val / 100)
        struct.pack_into(">I", self.data, 0, zlib.crc32(self.data[4:]))
        if self.path not in self.backed_up:
            shutil.copy2(self.path, self.path + time.strftime(".%Y%m%d-%H%M%S.bak"))
            self.backed_up.add(self.path)
        with open(self.path, "wb") as f:
            f.write(self.data)
        self.status.config(text="Saved")

    def restore(self):
        if not self.path:
            return
        folder, base = os.path.split(self.path)
        # plain "<save>.bak" is the oldest; timestamped ones sort by name
        baks = sorted((f for f in os.listdir(folder) if f.startswith(base + ".") and f.endswith(".bak")),
                      key=lambda f: (f != base + ".bak", f))
        if not baks:
            messagebox.showinfo("No backup", "No backup found for this save.")
            return
        if game_running():
            messagebox.showwarning("Game running", "Close NBA 2K14 first.")
            return
        latest = baks[-1]
        if messagebox.askyesno("Restore", f"Replace {base} with {latest}?"):
            shutil.copy2(os.path.join(folder, latest), self.path)
            self.open_file()


if __name__ == "__main__":
    App().mainloop()
