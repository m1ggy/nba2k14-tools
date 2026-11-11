"""NBA 2K14 MyCareer ratings editor (.CMG saves).

My Player tab
  Ratings live in a 42-byte block at a fixed offset in every .CMG save.
  Each byte is one rating: raw = (rating - 25) * 3, so raw 255 is about 110
  (menus show anything above 99 as 99).
  Caps (the "/90" part) are plain 8-bit values packed at a bit offset.

League players tab
  The save holds the whole league as 3644-bit player records (not byte-aligned).
  Each record has [last-name table id, offset, first-name table id, offset] as 16-bit
  fields pointing into UTF-16 name tables, and 42 ratings (same raw format)
  RATING_OFF bits after that name field.

The first 4 bytes of the file are a big-endian CRC32 of the rest, fixed on every save.
Edit only while the game is closed, or the game will overwrite the save.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import tkinter as tk
import zlib
from collections import Counter
from tkinter import messagebox, ttk

import numpy as np

SAVES = os.path.join(os.environ["APPDATA"], "2K Sports", "NBA 2K14", "Saves")
HERE = os.path.dirname(os.path.abspath(__file__))
NAMES_FILE = os.path.join(HERE, "ratings_names.json")
CMG_SIZE = 8195264

BLOCK = 0x29E81A          # MyPlayer ratings (byte offset)
COUNT = 42
MAX_RAW = 255
CAP_BIT = 0x702616 * 8 + 6  # MyPlayer caps (bit offset); slots 0-39 only
CAP_COUNT = 40
CAP_MAX = 99

RECORD_BITS = 3644        # league player record size
RATING_OFF = 2812         # ratings start, in bits after a record's name field

# slots 40-41 are Post Hook and Post Fadeaway (order between the two unconfirmed)
DEFAULT_NAMES = {"0": "Shot Close", "1": "Mid-Range", "3": "3PT",
                 "40": "Post Hook / Fadeaway", "41": "Post Fadeaway / Hook"}


# ---------- helpers ----------

def load_names():
    try:
        with open(NAMES_FILE, encoding="utf-8") as f:
            names = json.load(f)
    except (OSError, ValueError):
        names = dict(DEFAULT_NAMES)
        with open(NAMES_FILE, "w", encoding="utf-8") as f:
            json.dump(names, f, indent=2)
    return [names.get(str(i), f"Rating {i + 1} (?)") for i in range(COUNT)]


def to_rating(raw):
    return round(raw / 3 + 25)


def to_raw(rating):
    return MAX_RAW if rating >= 110 else max(0, min(MAX_RAW, round((rating - 25) * 3)))


def get_bits(data, bitpos, n=8):
    v = 0
    for k in range(n):
        b = bitpos + k
        v = (v << 1) | ((data[b >> 3] >> (7 - (b & 7))) & 1)
    return v


def put_bits(data, bitpos, value, n=8):
    for k in range(n):
        b = bitpos + k
        mask = 1 << (7 - (b & 7))
        if (value >> (n - 1 - k)) & 1:
            data[b >> 3] |= mask
        else:
            data[b >> 3] &= ~mask & 0xFF


def game_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq nba2k14.exe"],
                             capture_output=True, text=True, creationflags=0x08000000).stdout
        return "nba2k14.exe" in out.lower()
    except OSError:
        return False


def check_save(data):
    """Return an error message, or None if this looks like a MyCareer save we can edit."""
    if len(data) != CMG_SIZE:
        return "Unexpected file size - not a NBA 2K14 MyCareer save."
    if zlib.crc32(data[4:]) != struct.unpack(">I", data[:4])[0]:
        return "Checksum doesn't match - file may be damaged."
    block = data[BLOCK:BLOCK + COUNT]
    # most ratings are stored as multiples of 3; a couple of slots aren't
    if sum(b % 3 == 0 for b in block) < 34:
        return "Ratings block not found at the expected spot."
    return None


def caps_ok(data):
    """Caps follow a 255 marker and are all normal rating values."""
    return get_bits(data, CAP_BIT - 8) == 255 and all(
        25 <= get_bits(data, CAP_BIT + 8 * i) <= 99 for i in range(CAP_COUNT))


# ---------- league scan ----------

def find_name_tables(data):
    """Return {table_id: byte offset of first string} for big UTF-16 name tables."""
    strs = [(m.start(), m.group()[:-2]) for m in re.finditer(rb"(?:[\x20-\x7e]\x00){2,}\x00\x00", data)]
    tables, group, end = {}, [], -10**9

    def looks_like_names(g):
        # real name tables: lots of different strings, mostly letters (filters out junk runs)
        texts = [s.decode("utf-16-le") for _, s in g]
        if len(set(texts)) < 50:
            return False
        letters = sum(sum(c.isalpha() for c in x) >= 2 for x in texts)
        return letters >= 0.5 * len(texts)  # tables also hold codes like "f004"; junk runs score ~0

    for pos, s in strs + [(10**12, b"")]:
        if pos - end > 64:
            if group and looks_like_names(group):
                start = group[0][0]
                tid = int.from_bytes(data[start - 5:start - 3], "big")
                tables[tid] = start
            group = []
        group.append((pos, s))
        end = pos + len(s) + 2
    return tables


def read_name(data, tables, tid, off):
    base = tables.get(tid)
    if base is None:
        return None
    a = base + 2 * off
    e = a
    while e + 1 < len(data) and data[e:e + 2] != b"\0\0":
        e += 2
    try:
        return data[a:e].decode("utf-16-le")
    except UnicodeDecodeError:
        return None


def scan_league(data):
    """Return list of (name, name_field_bit) for every player record, or raise ValueError."""
    tables = find_name_tables(data)
    if not tables:
        raise ValueError("This save's roster stores names in a format\nthe League tab can't read yet.\n(My Player tab still works.)")
    # string start offsets per table, as lookup arrays
    tid_list = list(tables)
    tid_index = np.full(65536, -1, np.int16)
    valid = np.zeros((len(tid_list), 65536), bool)
    for i, tid in enumerate(tid_list):
        tid_index[tid] = i
        base = tables[tid]
        valid[i, 0] = True
        seg = np.frombuffer(data[base:base + 2 * 65536], dtype="<u2")
        z = np.flatnonzero(seg == 0)
        valid[i, (z + 1)[(z + 1) < 65536]] = True

    big = int.from_bytes(data, "big")
    nbits = len(data) * 8
    mask = (1 << nbits) - 1
    hits = []
    for s in range(8):
        b = data if s == 0 else ((big << s) & mask).to_bytes(len(data), "big")
        a = np.frombuffer(b, np.uint8).astype(np.int32)
        w = (a[:-1] << 8) | a[1:]           # big-endian 16-bit at every byte
        n = len(w) - 6
        t1 = tid_index[w[:n]]
        t2 = tid_index[w[4:n + 4]]
        ok = (t1 >= 0) & (t2 >= 0)
        k = np.flatnonzero(ok)
        k = k[valid[t1[k], w[k + 2]] & valid[t2[k], w[k + 6]] & ((w[k + 2] != 0) | (w[k + 6] != 0))]
        hits.extend((8 * k + s).tolist())
    if not hits:
        raise ValueError("No player records found.")
    # real records all sit on one 3644-bit grid; pick the busiest grid
    residue = Counter(h % RECORD_BITS for h in hits).most_common(1)[0][0]
    anchors = sorted(h for h in hits if h % RECORD_BITS == residue)
    # sanity check: the MyPlayer ratings block must sit on the same grid
    if (BLOCK * 8 - RATING_OFF - residue) % RECORD_BITS != 0:
        raise ValueError("Player records don't line up with the MyPlayer block in this save.")
    players = []
    for p in anchors:
        if p + RATING_OFF + 8 * COUNT > len(data) * 8:
            continue
        last = read_name(data, tables, get_bits(data, p, 16), get_bits(data, p + 16, 16))
        first = read_name(data, tables, get_bits(data, p + 32, 16), get_bits(data, p + 48, 16))
        if first is None or last is None:
            continue
        name = f"{first} {last}".strip()
        if name and all(32 <= ord(c) < 0x2500 for c in name):
            players.append((name, p))
    return players


# ---------- UI ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("2K14 MyPlayer Ratings")
        self.geometry("620x820")
        self.names = load_names()
        self.path = None
        self.data = None
        self.vars = []
        self.cap_vars = []
        self.players = []
        self.scanned_for = None
        self.league_vars = []
        self.league_bit = None

        steps = ttk.LabelFrame(self, text="How to use", padding=8)
        steps.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(steps, justify="left", text=(
            "1. Close NBA 2K14 (the light below must be green).\n"
            "2. Pick your MyCareer save (newest is picked for you).\n"
            "3. My Player tab: click MAX MY PLAYER (ratings + caps), or edit and Save changes.\n"
            "   League players tab: search a player, edit, and Save player.\n"
            "4. Start the game and load your MyCareer.")).pack(anchor="w")

        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        self.light = tk.Label(bar, text="", font=("", 10, "bold"), padx=8, pady=4)
        self.light.pack(side="left")
        ttk.Button(bar, text="Restore backup", command=self.restore).pack(side="right")

        top = ttk.Frame(self, padding=(8, 0))
        top.pack(fill="x")
        ttk.Label(top, text="MyCareer save:").pack(side="left")
        self.file_var = tk.StringVar()
        self.combo = ttk.Combobox(top, textvariable=self.file_var, state="readonly", width=28)
        self.combo.pack(side="left", padx=6)
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.open_file())
        ttk.Button(top, text="Refresh", command=self.refresh_files).pack(side="left")
        self.status = ttk.Label(top, text="")
        self.status.pack(side="left", padx=8)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=6)
        self.mp_tab = ttk.Frame(self.tabs)
        self.lg_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.mp_tab, text="My Player")
        self.tabs.add(self.lg_tab, text="League players")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.maybe_scan())
        self.build_my_player_tab()
        self.build_league_tab()

        ttk.Label(self, text="Close the game before saving. Menus show anything above 99 as 99.",
                  foreground="gray", padding=(8, 0, 8, 8)).pack(fill="x")
        self.update_light()
        self.refresh_files()

    # ----- shared -----

    def update_light(self):
        if game_running():
            self.light.config(text="●  Game is OPEN - close it before saving", fg="white", bg="#b3261e")
        else:
            self.light.config(text="●  Game closed - safe to save", fg="white", bg="#1f7a3a")
        self.after(2000, self.update_light)

    def refresh_files(self):
        files = [f for f in os.listdir(SAVES) if f.upper().endswith(".CMG")]
        files.sort(key=lambda f: os.path.getmtime(os.path.join(SAVES, f)), reverse=True)
        self.combo["values"] = files
        if files and self.file_var.get() not in files:
            self.file_var.set(files[0])  # newest save first, usually the one just created
        self.open_file()

    def open_file(self):
        name = self.file_var.get()
        self.data = None
        self.players = []
        self.scanned_for = None
        self.league_bit = None
        if name:
            self.path = os.path.join(SAVES, name)
            with open(self.path, "rb") as f:
                data = bytearray(f.read())
            err = check_save(data)
            if err:
                self.status.config(text=err)
            else:
                self.data = data
                self.status.config(text=f"Loaded {name}")
        self.fill_my_player()
        self.clear_league()
        self.maybe_scan()

    def write_file(self):
        """Fix checksum, back up, write. Returns True on success."""
        if game_running():
            messagebox.showwarning("Game running", "Close NBA 2K14 first, or the game will overwrite this save.")
            return False
        struct.pack_into(">I", self.data, 0, zlib.crc32(self.data[4:]))
        shutil.copy2(self.path, self.path + time.strftime(".%Y%m%d-%H%M%S.bak"))
        with open(self.path, "wb") as f:
            f.write(self.data)
        self.status.config(text="Saved (backup made)")
        messagebox.showinfo("Saved", f"{os.path.basename(self.path)} updated.\nYou can start the game now.")
        return True

    def restore(self):
        if not self.path:
            return
        folder, base = os.path.split(self.path)
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

    @staticmethod
    def scroll_area(parent):
        wrap = ttk.Frame(parent)
        canvas = tk.Canvas(wrap, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind("<Enter>", lambda e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(-ev.delta // 120, "units")))
        return wrap, inner

    # ----- My Player tab -----

    def build_my_player_tab(self):
        quick = ttk.Frame(self.mp_tab, padding=(0, 6))
        quick.pack(fill="x")
        tk.Button(quick, text="MAX MY PLAYER", font=("", 11, "bold"), bg="#1f7a3a", fg="white",
                  activebackground="#2a9a4b", padx=14, pady=2, command=self.max_and_save).pack(side="left")
        ttk.Button(quick, text="Max all", command=lambda: self.set_all(None)).pack(side="left", padx=(10, 0))
        ttk.Button(quick, text="Set all to 99", command=lambda: self.set_all(99)).pack(side="left", padx=6)
        ttk.Button(quick, text="Max caps", command=self.max_caps).pack(side="left")
        wrap, self.mp_inner = self.scroll_area(self.mp_tab)
        wrap.pack(fill="both", expand=True)
        bottom = ttk.Frame(self.mp_tab, padding=(0, 6))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Save changes", command=self.save_my_player).pack(side="right")
        ttk.Button(bottom, text="Reload", command=self.open_file).pack(side="right", padx=6)

    def fill_my_player(self):
        for w in self.mp_inner.winfo_children():
            w.destroy()
        self.vars, self.cap_vars = [], []
        if self.data is None:
            return
        has_caps = caps_ok(self.data)
        ttk.Label(self.mp_inner, text="Rating", font=("", 9, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(self.mp_inner, text="Value (25-110)", font=("", 9, "bold")).grid(row=0, column=1, columnspan=2)
        ttk.Label(self.mp_inner, text="Cap" if has_caps else "Cap (not found)",
                  font=("", 9, "bold")).grid(row=0, column=3)
        for i in range(COUNT):
            var = tk.IntVar(value=to_rating(self.data[BLOCK + i]))
            self.vars.append(var)
            ttk.Label(self.mp_inner, text=self.names[i], width=22).grid(row=i + 1, column=0, sticky="w")
            ttk.Scale(self.mp_inner, from_=25, to=110, variable=var, length=170,
                      command=lambda v, var=var: var.set(round(float(v)))).grid(row=i + 1, column=1, padx=4)
            ttk.Spinbox(self.mp_inner, from_=25, to=110, textvariable=var, width=5).grid(row=i + 1, column=2)
            if has_caps and i < CAP_COUNT:
                cvar = tk.IntVar(value=get_bits(self.data, CAP_BIT + 8 * i))
                self.cap_vars.append(cvar)
                ttk.Spinbox(self.mp_inner, from_=25, to=CAP_MAX, textvariable=cvar, width=4).grid(
                    row=i + 1, column=3, padx=(8, 0))

    def set_all(self, value):
        for var in self.vars:
            var.set(110 if value is None else value)

    def max_caps(self):
        if not self.cap_vars:
            messagebox.showinfo("Caps", "Caps weren't found in this save.")
            return
        for cvar in self.cap_vars:
            cvar.set(CAP_MAX)

    def max_and_save(self):
        if self.data is None:
            messagebox.showinfo("No save", "Pick a MyCareer save first.")
            return
        if not messagebox.askyesno("Max my player",
                                   f"Set every rating and cap in {self.file_var.get()} to max and save?\n"
                                   "A backup is made first."):
            return
        self.set_all(None)
        for cvar in self.cap_vars:
            cvar.set(CAP_MAX)
        self.save_my_player()

    def save_my_player(self):
        if self.data is None:
            return
        for i, var in enumerate(self.vars):
            try:
                rating = int(var.get())
            except (tk.TclError, ValueError):
                messagebox.showerror("Bad value", f"{self.names[i]} has an invalid number.")
                return
            if to_rating(self.data[BLOCK + i]) != rating:  # untouched values stay bit-for-bit
                self.data[BLOCK + i] = to_raw(rating)
        for i, cvar in enumerate(self.cap_vars):
            try:
                cap = max(25, min(CAP_MAX, int(cvar.get())))
            except (tk.TclError, ValueError):
                messagebox.showerror("Bad value", f"Cap for {self.names[i]} has an invalid number.")
                return
            if get_bits(self.data, CAP_BIT + 8 * i) != cap:
                put_bits(self.data, CAP_BIT + 8 * i, cap)
        if self.write_file():
            self.open_file()

    # ----- League players tab -----

    def build_league_tab(self):
        left = ttk.Frame(self.lg_tab, padding=(0, 6))
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Search player:").pack(anchor="w")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.fill_player_list())
        ttk.Entry(left, textvariable=self.search_var, width=26).pack(anchor="w", pady=(0, 4))
        lb_wrap = ttk.Frame(left)
        lb_wrap.pack(fill="y", expand=True)
        self.player_list = tk.Listbox(lb_wrap, width=26, exportselection=False)
        sb = ttk.Scrollbar(lb_wrap, orient="vertical", command=self.player_list.yview)
        self.player_list.configure(yscrollcommand=sb.set)
        self.player_list.pack(side="left", fill="y", expand=True)
        sb.pack(side="right", fill="y")
        self.player_list.bind("<<ListboxSelect>>", lambda e: self.select_player())
        self.lg_count = ttk.Label(left, text="")
        self.lg_count.pack(anchor="w", pady=(4, 0))

        right = ttk.Frame(self.lg_tab, padding=(10, 6, 0, 6))
        right.pack(side="left", fill="both", expand=True)
        self.lg_title = ttk.Label(right, text="Pick a player on the left.", font=("", 11, "bold"))
        self.lg_title.pack(anchor="w")
        quick = ttk.Frame(right, padding=(0, 4))
        quick.pack(fill="x")
        ttk.Button(quick, text="-2 all", command=lambda: self.shift_league(-2)).pack(side="left")
        ttk.Button(quick, text="+2 all", command=lambda: self.shift_league(2)).pack(side="left", padx=4)
        ttk.Label(quick, text="Set all to").pack(side="left", padx=(8, 2))
        self.setall_var = tk.IntVar(value=75)
        ttk.Spinbox(quick, from_=25, to=110, textvariable=self.setall_var, width=4).pack(side="left")
        ttk.Button(quick, text="Apply", command=self.setall_league).pack(side="left", padx=4)
        wrap, self.lg_inner = self.scroll_area(right)
        wrap.pack(fill="both", expand=True)
        ttk.Button(right, text="Save player", command=self.save_league_player).pack(anchor="e", pady=(6, 0))

    def clear_league(self):
        self.player_list.delete(0, "end")
        for w in self.lg_inner.winfo_children():
            w.destroy()
        self.league_vars = []
        self.league_bit = None
        self.lg_title.config(text="Pick a player on the left.")
        self.lg_count.config(text="")

    def maybe_scan(self):
        if self.tabs.index(self.tabs.select()) != 1 or self.data is None:
            return
        if self.scanned_for == self.path:
            return
        self.scanned_for = self.path
        self.lg_count.config(text="Scanning league...")
        data, path = bytes(self.data), self.path
        box = {}

        def work():  # background thread: no Tk calls here
            try:
                box["result"] = (scan_league(data), None)
            except ValueError as e:
                box["result"] = ([], str(e))

        def poll():  # main thread picks up the result
            if "result" in box:
                self.scan_done(path, *box["result"])
            else:
                self.after(100, poll)
        threading.Thread(target=work, daemon=True).start()
        self.after(100, poll)

    def scan_done(self, path, players, err):
        if path != self.path:
            return  # user switched saves meanwhile
        self.players = players
        if err:
            self.lg_count.config(text=err)
            return
        self.fill_player_list()

    def fill_player_list(self):
        q = self.search_var.get().strip().lower()
        self.shown = [(n, b) for n, b in self.players if q in n.lower()]
        self.player_list.delete(0, "end")
        for n, _ in self.shown:
            self.player_list.insert("end", n)
        if self.players:
            self.lg_count.config(text=f"{len(self.shown)} of {len(self.players)} players")

    def select_player(self):
        sel = self.player_list.curselection()
        if not sel or self.data is None:
            return
        name, bit = self.shown[sel[0]]
        self.league_bit = bit
        self.lg_title.config(text=name)
        for w in self.lg_inner.winfo_children():
            w.destroy()
        self.league_vars = []
        base = bit + RATING_OFF
        for i in range(COUNT):
            var = tk.IntVar(value=to_rating(get_bits(self.data, base + 8 * i)))
            self.league_vars.append(var)
            ttk.Label(self.lg_inner, text=self.names[i], width=22).grid(row=i, column=0, sticky="w")
            ttk.Spinbox(self.lg_inner, from_=25, to=110, textvariable=var, width=5).grid(row=i, column=1, padx=4)

    def shift_league(self, delta):
        for var in self.league_vars:
            try:
                var.set(max(25, min(110, int(var.get()) + delta)))
            except (tk.TclError, ValueError):
                pass

    def setall_league(self):
        try:
            v = max(25, min(110, int(self.setall_var.get())))
        except (tk.TclError, ValueError):
            return
        for var in self.league_vars:
            var.set(v)

    def save_league_player(self):
        if self.data is None or self.league_bit is None:
            return
        base = self.league_bit + RATING_OFF
        for i, var in enumerate(self.league_vars):
            try:
                rating = int(var.get())
            except (tk.TclError, ValueError):
                messagebox.showerror("Bad value", f"{self.names[i]} has an invalid number.")
                return
            if to_rating(get_bits(self.data, base + 8 * i)) != rating:
                put_bits(self.data, base + 8 * i, to_raw(rating))
        if self.write_file():
            self.fill_my_player()  # in case the edited player is your MyPlayer


if __name__ == "__main__":
    App().mainloop()
