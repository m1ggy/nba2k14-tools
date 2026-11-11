"""NBA 2K14 MyCareer live sliders.

MyCareer ignores the slider files and resets the slider table when a game loads,
but during play the game reads its real slider values from a settings block in
memory. This app finds that block in the running game and keeps your chosen
values in it (it re-applies them every half second, so resets at game load are
undone right away).

The live part writes nothing to disk. "Write to save" makes the locked values
permanent in a MyCareer save (.CMG): the save's slider block (126 big-endian
floats at 0x6A84E1, same pair order) is loaded into the game's real slider block
when MyCareer starts. Saves are backed up and their CRC is fixed.
"""
import ctypes as C
import json
import os
import shutil
import struct
import subprocess
import time
import tkinter as tk
import zlib
from tkinter import messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, "live_sliders.json")
SAVES = os.path.join(os.environ.get("APPDATA", ""), "2K Sports", "NBA 2K14", "Saves")
CMG_SIZE = 8195264
CMG_SLIDERS = 0x6A84E1

# Pointer chain found by tracing the game's slider getter (nba2k14.exe, Oct 2013 build):
#   block = [[ROOT] + OBJ_OFF] + BLOCK_OFF ; 4-byte header, then 126 floats (63 User/CPU pairs)
ROOT = 0x1F207A8
OBJ_OFF = 0x350CA4
BLOCK_OFF = 0xBBE30
# bytes of the game's "get slider block" function, used to check the game version
SIGNATURE_ADDR = 0xA09EF0
SIGNATURE = bytes.fromhex("a1a807f2018b80a40c350085c07501c3")

NAMES = [
    "Stealing", "Blocking", "Speed", "Ball Handling", "Dunking Ability",
    "Offensive Awareness", "Defensive Awareness", "Offensive Rebounding",
    "Defensive Rebounding", "Strength", "Quickness", "Hustle", "Consistency",
    "Durability", "Stamina", "Hands", "Vertical", "On-Ball Defense",
    "Alley-Oop Success", "Contact Shot Success", "Inside Shot Success",
    "Close Shot Success", "Mid-Range Success", "3PT Success", "Layup Success",
    "Dunk In Traffic Frequency", "Dunk In Traffic Success", "Backdoor Cuts",
    "Pass Accuracy", "Steal Success", "Driving Contact Frequency",
    "Inside Contact Frequency", "Layup Defense Takeoff", "Layup Defense Release",
    "Jumpshot Defense Gather", "Jumpshot Defense Release", "Over The Back Foul",
    "Charging Foul", "Blocking Foul", "Reaching Foul", "Shooting Foul",
    "Loose Ball Foul", "Injury Frequency", "Injury Severity", "Fatigue Rate",
    "Unknown 46", "Unknown 47", "Attempt Dunks", "Unknown 49", "Attempt Putbacks",
    "Unknown 51", "Play Passing Lanes", "Go For On-Ball Steals", "Contest Shots",
    "Post Shots", "Help Defense Strength", "Take Inside Shots", "Take Close Shots",
    "Take Mid-Range Shots", "Take 3PT Shots", "Attack The Basket",
    "Look For Post Players", "Throw Alley-Oops",
]
PAIRS = len(NAMES)  # 63
SHOOTING = range(18, 29)  # Alley-Oop .. Pass Accuracy (the set tested in-game)

k32 = C.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = C.c_void_p
k32.ReadProcessMemory.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k32.WriteProcessMemory.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k32.CloseHandle.argtypes = [C.c_void_p]


def find_pid():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq nba2k14.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, creationflags=0x08000000).stdout
    except OSError:
        return None
    if "nba2k14" not in out.lower():
        return None
    try:
        return int(out.split('","')[1])
    except (IndexError, ValueError):
        return None


class Game:
    """Connection to the running game."""

    def __init__(self):
        self.pid = None
        self.h = None

    def connect(self):
        pid = find_pid()
        if pid != self.pid:
            if self.h:
                k32.CloseHandle(self.h)
            self.h = k32.OpenProcess(0x0438, False, pid) if pid else None
            self.pid = pid if self.h else None
        return self.h is not None

    def read(self, addr, n):
        buf = C.create_string_buffer(n)
        got = C.c_size_t()
        if not k32.ReadProcessMemory(self.h, C.c_void_p(addr), buf, n, C.byref(got)) or got.value != n:
            return None
        return buf.raw

    def write(self, addr, data):
        done = C.c_size_t()
        return bool(k32.WriteProcessMemory(self.h, C.c_void_p(addr), data, len(data), C.byref(done))) \
            and done.value == len(data)

    def u32(self, addr):
        b = self.read(addr, 4)
        return struct.unpack("<I", b)[0] if b else None

    def version_ok(self):
        return self.read(SIGNATURE_ADDR, len(SIGNATURE)) == SIGNATURE

    def block(self):
        """Address of the 126 slider floats, or None if MyCareer/settings data isn't loaded."""
        root = self.u32(ROOT)
        if not root:
            return None
        obj = self.u32(root + OBJ_OFF)
        if not obj:
            return None
        addr = obj + BLOCK_OFF + 4
        raw = self.read(addr, 504)
        if not raw:
            return None
        vals = struct.unpack("<126f", raw)
        if not all(0.0 <= v <= 1.0 for v in vals):
            return None
        return addr


def read_save_sliders(path):
    """Return (data, 126 values) of a MyCareer save, or raise ValueError."""
    with open(path, "rb") as f:
        data = bytearray(f.read())
    if len(data) != CMG_SIZE:
        raise ValueError("This doesn't look like a MyCareer save (wrong size).")
    if zlib.crc32(data[4:]) != struct.unpack(">I", data[:4])[0]:
        raise ValueError("The save's checksum doesn't match - it may be damaged.")
    vals = struct.unpack_from(">126f", data, CMG_SLIDERS)
    if not all(0.0 <= v <= 1.0 for v in vals):
        raise ValueError("Slider data not found where expected in this save.")
    return data, vals


def default_settings():
    s = {"enabled": True, "rows": {}}
    for p in SHOOTING:
        s["rows"][str(p)] = {"lock": True, "user": 100, "cpu": 0}
    return s


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            s = json.load(f)
        s.setdefault("enabled", True)
        s.setdefault("rows", {})
        return s
    except (OSError, ValueError):
        return default_settings()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("2K14 MyCareer Live Sliders")
        self.geometry("660x820")
        self.minsize(560, 500)
        self.game = Game()
        self.settings = load_settings()
        self.rows = []  # (lock_var, user_var, cpu_var)
        self.fixes = 0

        head = ttk.Frame(self, padding=(10, 8))
        head.pack(fill="x")
        ttk.Label(head, text="MyCareer Live Sliders", font=("", 13, "bold")).pack(anchor="w")
        ttk.Label(head, justify="left", foreground="#444", text=(
            "1. Start NBA 2K14 and keep this window open while you play.\n"
            "2. Tick 'Lock' on the sliders you want and set User / CPU values.\n"
            "3. Locked values are forced into the game every half second, also in MyCareer.\n"
            "Live changes end when you close this app. Use Write to save to keep them."
        )).pack(anchor="w", pady=(2, 6))

        bar = ttk.Frame(self, padding=(10, 0))
        bar.pack(fill="x")
        self.dot = tk.Label(bar, text="●", font=("", 14), fg="#999")
        self.dot.pack(side="left")
        self.status = ttk.Label(bar, text="Starting...")
        self.status.pack(side="left", padx=4)
        self.enabled = tk.BooleanVar(value=self.settings.get("enabled", True))
        ttk.Checkbutton(bar, text="Keep applying", variable=self.enabled,
                        command=self.save_settings).pack(side="right")

        quick = ttk.Frame(self, padding=(10, 6))
        quick.pack(fill="x")
        ttk.Button(quick, text="Shooting: me 100 / CPU 0", command=lambda: self.quick(100, 0)).pack(side="left")
        ttk.Button(quick, text="Shooting: me 75 / CPU 35", command=lambda: self.quick(75, 35)).pack(side="left", padx=6)
        ttk.Button(quick, text="Unlock all", command=self.unlock_all).pack(side="left")
        ttk.Button(quick, text="Read from game", command=self.read_from_game).pack(side="right")

        savebar = ttk.LabelFrame(self, text="Make it permanent (game must be closed)", padding=(8, 4))
        savebar.pack(fill="x", padx=10, pady=(0, 6))
        self.save_var = tk.StringVar()
        self.save_combo = ttk.Combobox(savebar, textvariable=self.save_var, state="readonly", width=24)
        self.save_combo.pack(side="left")
        ttk.Button(savebar, text="Write to save", command=self.write_to_save).pack(side="left", padx=6)
        ttk.Button(savebar, text="Load from save", command=self.load_from_save).pack(side="left")
        ttk.Button(savebar, text="↻", width=3, command=self.refresh_saves).pack(side="right")
        self.refresh_saves()

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        canvas = tk.Canvas(wrap, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-e.delta // 120, "units"))

        for col, text in enumerate(("Lock", "Slider", "User (you)", "CPU")):
            ttk.Label(inner, text=text, font=("", 9, "bold")).grid(row=0, column=col, sticky="w", padx=4)
        for p, name in enumerate(NAMES):
            r = self.settings["rows"].get(str(p), {})
            lock = tk.BooleanVar(value=r.get("lock", False))
            user = tk.IntVar(value=r.get("user", 50))
            cpu = tk.IntVar(value=r.get("cpu", 50))
            for var in (lock, user, cpu):
                var.trace_add("write", lambda *a: self.save_settings())
            ttk.Checkbutton(inner, variable=lock).grid(row=p + 1, column=0, padx=4)
            ttk.Label(inner, text=name, width=26).grid(row=p + 1, column=1, sticky="w")
            ttk.Spinbox(inner, from_=0, to=100, textvariable=user, width=5).grid(row=p + 1, column=2, padx=4)
            ttk.Spinbox(inner, from_=0, to=100, textvariable=cpu, width=5).grid(row=p + 1, column=3, padx=4)
            self.rows.append((lock, user, cpu))

        self.after(300, self.tick)

    # ----- settings -----
    def save_settings(self):
        rows = {}
        for p, (lock, user, cpu) in enumerate(self.rows):
            try:
                rows[str(p)] = {"lock": bool(lock.get()), "user": int(user.get()), "cpu": int(cpu.get())}
            except (tk.TclError, ValueError):
                return  # half-typed number; save on the next change
        self.settings = {"enabled": bool(self.enabled.get()), "rows": rows}
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=1)
        except OSError:
            pass

    def quick(self, me, cpu):
        for p in SHOOTING:
            lock, u, c = self.rows[p]
            lock.set(True)
            u.set(me)
            c.set(cpu)

    def unlock_all(self):
        for lock, _, _ in self.rows:
            lock.set(False)

    def read_from_game(self):
        if not self.game.connect() or not self.game.version_ok():
            messagebox.showinfo("Not available", "Start NBA 2K14 first.")
            return
        addr = self.game.block()
        if addr is None:
            messagebox.showinfo("Not available", "The game hasn't loaded its slider data yet.")
            return
        vals = struct.unpack("<126f", self.game.read(addr, 504))
        for p, (lock, u, c) in enumerate(self.rows):
            if not lock.get():
                u.set(round(vals[2 * p] * 100))
                c.set(round(vals[2 * p + 1] * 100))

    # ----- save file -----
    def refresh_saves(self):
        try:
            files = [f for f in os.listdir(SAVES) if f.upper().endswith(".CMG")]
        except OSError:
            files = []
        files.sort(key=lambda f: os.path.getmtime(os.path.join(SAVES, f)), reverse=True)
        self.save_combo["values"] = files
        if files and self.save_var.get() not in files:
            self.save_var.set(files[0])

    def save_path(self):
        name = self.save_var.get()
        if not name:
            messagebox.showinfo("No save", "No MyCareer save (.CMG) found.")
            return None
        return os.path.join(SAVES, name)

    def write_to_save(self):
        path = self.save_path()
        if not path:
            return
        if find_pid():
            messagebox.showwarning("Game running", "Close NBA 2K14 first, or it will overwrite the save.")
            return
        try:
            data, _ = read_save_sliders(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Can't use this save", str(e))
            return
        changed = 0
        for p, (lock, u, c) in enumerate(self.rows):
            if not lock.get():
                continue
            try:
                vals = (max(0, min(100, int(u.get()))) / 100, max(0, min(100, int(c.get()))) / 100)
            except (tk.TclError, ValueError):
                messagebox.showerror("Bad value", f"{NAMES[p]} has an invalid number.")
                return
            for side, v in enumerate(vals):
                off = CMG_SLIDERS + 4 * (2 * p + side)
                if round(struct.unpack_from(">f", data, off)[0] * 100) != round(v * 100):
                    struct.pack_into(">f", data, off, v)
                    changed += 1
        if not changed:
            messagebox.showinfo("Nothing to do", "The save already has these values (only locked rows are written).")
            return
        name = os.path.basename(path)
        if not messagebox.askyesno("Write to save", f"Write {changed} slider values into {name}?\nA backup is made first."):
            return
        struct.pack_into(">I", data, 0, zlib.crc32(data[4:]))
        backup = path + time.strftime(".%Y%m%d-%H%M%S.bak")
        shutil.copy2(path, backup)
        with open(path, "wb") as f:
            f.write(data)
        messagebox.showinfo("Saved", f"Done. {name} now has your locked sliders.\nBackup: {os.path.basename(backup)}\n\n"
                            "Tip: don't change the Game Style in the game menu, or it may replace them.")

    def load_from_save(self):
        path = self.save_path()
        if not path:
            return
        try:
            _, vals = read_save_sliders(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Can't use this save", str(e))
            return
        for p, (lock, u, c) in enumerate(self.rows):
            u.set(round(vals[2 * p] * 100))
            c.set(round(vals[2 * p + 1] * 100))

    # ----- main loop -----
    def set_status(self, text, color):
        self.status.config(text=text)
        self.dot.config(fg=color)

    def tick(self):
        try:
            self.apply_once()
        finally:
            self.after(500, self.tick)

    def apply_once(self):
        if not self.game.connect():
            self.set_status("Game not running - start NBA 2K14", "#999")
            return
        if not self.game.version_ok():
            self.set_status("Unsupported game version - nothing changed", "#c62828")
            return
        addr = self.game.block()
        if addr is None:
            self.set_status("Game running - waiting for slider data to load", "#f9a825")
            return
        if not self.enabled.get():
            self.set_status("Connected - applying is paused", "#f9a825")
            return
        locked = 0
        for p, (lock, u, c) in enumerate(self.rows):
            if not lock.get():
                continue
            try:
                want = struct.pack("<ff", max(0, min(100, int(u.get()))) / 100,
                                   max(0, min(100, int(c.get()))) / 100)
            except (tk.TclError, ValueError):
                continue
            locked += 1
            at = addr + 8 * p
            if self.game.read(at, 8) != want and self.game.write(at, want):
                self.fixes += 1
        self.set_status(f"Active - {locked} sliders locked, re-applied {self.fixes} times", "#2e7d32")


if __name__ == "__main__":
    App().mainloop()
