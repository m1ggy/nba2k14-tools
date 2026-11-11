"""NBA 2K14 Player Creator (v1).

Pick a save or roster, find a player, then edit his basics and ratings, and give him
a face: either an existing face ID, or a new face made from a photo (installed as a
mod with the mod manager, so it can be undone).
"""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from nba2k14 import texture as T
from nba2k14.iff import IffFile
from nba2k14.mods import DEFAULT_GAME_DIR, ModError, ModManager, game_running
from nba2k14.roster import POSITIONS, Roster, height_text, rating_from_raw, raw_from_rating

HERE = os.path.dirname(os.path.abspath(__file__))
SAVES = os.path.join(os.environ.get("APPDATA", ""), "2K Sports", "NBA 2K14", "Saves")
FACE_MOD = "Player Faces"
ROSTER_MOD = "Roster Edits"
BUILT_IN = "Game roster (vanilla 2013-14)"
FALLBACK_TEMPLATE = 1260


def face_file(face_id):
    return f"png{face_id:04d}.iff"


def load_rating_names():
    try:
        with open(os.path.join(HERE, "ratings_names.json"), encoding="utf-8") as f:
            names = json.load(f)
    except (OSError, ValueError):
        names = {}
    return [names.get(str(i), f"Rating {i + 1}") for i in range(42)]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("2K14 Player Creator")
        self.geometry("1040x780")
        self.minsize(900, 640)
        self.mm = ModManager(check_game=False)
        self.roster = None
        self.player = None
        self.shown = []
        self.new_face = None      # (IffFile, preview image) waiting to be installed
        self.jobs = queue.Queue()
        self.rating_names = load_rating_names()

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="File:").pack(side="left")
        self.file_var = tk.StringVar()
        self.file_combo = ttk.Combobox(top, textvariable=self.file_var, state="readonly", width=38)
        self.file_combo.pack(side="left", padx=6)
        self.file_combo.bind("<<ComboboxSelected>>", lambda e: self.load_file())
        ttk.Button(top, text="Refresh", command=self.refresh_files).pack(side="left")
        self.status = ttk.Label(top, text="")
        self.status.pack(side="left", padx=12)

        body = ttk.Frame(self, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *a: self.fill_list())
        ttk.Entry(left, textvariable=self.search, width=30).pack(fill="x")
        self.listbox = tk.Listbox(left, width=34, activestyle="none")
        self.listbox.pack(fill="y", expand=True, pady=4)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.select_player())
        self.count = ttk.Label(left, text="")
        self.count.pack(anchor="w")

        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)
        self.name_label = ttk.Label(right, text="Pick a file and a player", font=("", 14, "bold"))
        self.name_label.grid(row=0, column=0, columnspan=4, sticky="w")

        # --- basics
        basics = ttk.LabelFrame(right, text="Basics", padding=8)
        basics.grid(row=1, column=0, sticky="nw")
        self.height_in = tk.IntVar()
        self.weight = tk.IntVar()
        self.jersey = tk.IntVar()
        self.position = tk.StringVar()
        self.height_label = ttk.Label(basics, text="")
        rows = [("Height (inches)", ttk.Spinbox(basics, from_=60, to=96, textvariable=self.height_in, width=6,
                                                command=self.update_height_label)),
                ("Weight (lb)", ttk.Spinbox(basics, from_=120, to=400, textvariable=self.weight, width=6)),
                ("Jersey", ttk.Spinbox(basics, from_=0, to=99, textvariable=self.jersey, width=6)),
                ("Position", ttk.Combobox(basics, textvariable=self.position, values=POSITIONS, state="readonly",
                                          width=5))]
        self.jersey_w, self.position_w = rows[2][1], rows[3][1]
        for i, (lab, w) in enumerate(rows):
            ttk.Label(basics, text=lab).grid(row=i, column=0, sticky="w", pady=2)
            w.grid(row=i, column=1, sticky="w", padx=6)
        self.height_label.grid(row=0, column=2, sticky="w")

        # --- face
        facebox = ttk.LabelFrame(right, text="Face", padding=8)
        facebox.grid(row=1, column=1, sticky="nw", padx=12)
        self.face_id = tk.IntVar()
        f0 = ttk.Frame(facebox)
        f0.pack(anchor="w")
        ttk.Label(f0, text="Face ID").pack(side="left")
        ttk.Spinbox(f0, from_=0, to=65535, textvariable=self.face_id, width=8,
                    command=self.show_face).pack(side="left", padx=6)
        ttk.Button(f0, text="Show", command=self.show_face).pack(side="left")
        self.face_img = ttk.Label(facebox)
        self.face_img.pack(pady=6)
        self.face_note = ttk.Label(facebox, text="", wraplength=260, foreground="#555")
        self.face_note.pack(anchor="w")
        fb = ttk.Frame(facebox)
        fb.pack(anchor="w", pady=4)
        ttk.Button(fb, text="New face from photo...", command=self.face_from_photo).pack(side="left")
        self.install_btn = ttk.Button(fb, text="Install face", command=self.install_face, state="disabled")
        self.install_btn.pack(side="left", padx=6)
        ft = ttk.Frame(facebox)
        ft.pack(anchor="w")
        ttk.Label(ft, text="Template face (head shape)").pack(side="left")
        self.template_id = tk.IntVar(value=FALLBACK_TEMPLATE)
        ttk.Spinbox(ft, from_=0, to=65535, textvariable=self.template_id, width=8).pack(side="left", padx=6)

        # --- ratings
        rbox = ttk.LabelFrame(right, text="Ratings", padding=8)
        rbox.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=8)
        right.rowconfigure(2, weight=1)
        self.rating_vars = []
        for i in range(42):
            v = tk.IntVar()
            r, c = i % 14, i // 14
            ttk.Label(rbox, text=self.rating_names[i], width=20).grid(row=r, column=2 * c, sticky="w")
            ttk.Spinbox(rbox, from_=25, to=110, textvariable=v, width=5).grid(row=r, column=2 * c + 1, padx=(0, 14))
            self.rating_vars.append(v)

        bottom = ttk.Frame(right)
        bottom.grid(row=3, column=0, columnspan=2, sticky="e")
        ttk.Button(bottom, text="Save player", command=self.save_player).pack(side="right")

        self.refresh_files()
        self.after(100, self.poll)

    # ----- background work
    def run_bg(self, fn, done):
        def work():
            try:
                self.jobs.put((done, fn(), None))
            except Exception as e:  # report on the UI thread
                self.jobs.put((done, None, e))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                done, result, err = self.jobs.get_nowait()
                done(result, err)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    # ----- files and players
    def refresh_files(self):
        try:
            files = [f for f in os.listdir(SAVES) if f.upper().endswith((".CMG", ".FXG", ".ROS"))]
        except OSError:
            files = []
        files.sort(key=lambda f: os.path.getmtime(os.path.join(SAVES, f)), reverse=True)
        files = [BUILT_IN] + files
        self.file_combo["values"] = files
        if files and self.file_var.get() not in files:
            self.file_var.set(files[0])
            self.load_file()

    def current_path(self):
        if self.file_var.get() == BUILT_IN:
            return os.path.join(DEFAULT_GAME_DIR, "roster.iff")
        return os.path.join(SAVES, self.file_var.get())

    def load_file(self):
        path = self.current_path()
        self.status.config(text="Loading players...")
        self.listbox.delete(0, "end")

        def done(roster, err):
            if err:
                self.status.config(text=f"Can't read this file: {err}")
                return
            self.roster = roster
            self.status.config(text=f"{len(roster.players)} players" + ("" if roster.crc_ok else " (checksum bad!)"))
            self.fill_list()
        self.run_bg(lambda: Roster(path), done)

    def fill_list(self):
        self.listbox.delete(0, "end")
        if not self.roster:
            return
        self.shown = self.roster.find(self.search.get()) if self.search.get() else self.roster.players
        for p in self.shown:
            self.listbox.insert("end", p.name)
        self.count.config(text=f"{len(self.shown)} shown")

    def select_player(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        p = self.player = self.shown[sel[0]]
        self.name_label.config(text=p.name)
        self.height_in.set(round(p.get("height_cm") / 2.54))
        self.update_height_label()
        self.weight.set(round(p.get("weight_lb")))
        try:
            self.jersey.set(p.get("jersey"))
            pos = p.get("position")
            self.position.set(POSITIONS[pos] if pos < 5 else "")
            self.jersey_w.config(state="normal")
            self.position_w.config(state="readonly")
        except KeyError:  # not mapped for the built-in roster yet
            self.jersey.set(0)
            self.position.set("")
            self.jersey_w.config(state="disabled")
            self.position_w.config(state="disabled")
        self.face_id.set(p.get("face_id"))
        for v, raw in zip(self.rating_vars, p.ratings()):
            v.set(rating_from_raw(raw))
        self.new_face = None
        self.install_btn.config(state="disabled")
        if os.path.exists(os.path.join(DEFAULT_GAME_DIR, face_file(p.get("face_id")))):
            self.template_id.set(p.get("face_id"))
        self.show_face()

    def update_height_label(self):
        try:
            self.height_label.config(text=height_text(self.height_in.get() * 2.54))
        except tk.TclError:
            pass

    # ----- faces
    def face_path(self, fid):
        p = os.path.join(DEFAULT_GAME_DIR, face_file(fid))
        return p if os.path.exists(p) else None

    def set_preview(self, rgb):
        im = Image.fromarray(rgb[..., :3]).resize((220, 220))
        self._photo = ImageTk.PhotoImage(im)
        self.face_img.config(image=self._photo)

    def show_face(self):
        try:
            fid = int(self.face_id.get())
        except tk.TclError:
            return
        path = self.face_path(fid)
        if not path:
            self.face_img.config(image="")
            self.face_note.config(text=f"No face file {face_file(fid)} in the game: the player shows a generic face. "
                                       "Make one from a photo to give him a real face.")
            return
        try:
            tex = next(t for t in T.textures(IffFile.load(path)) if t.name == "face_color")
            self.set_preview(tex.image())
            owner = self.mm.state["files"].get(face_file(fid), {}).get("owner")
            self.face_note.config(text=f"{face_file(fid)}" + (f" (changed by mod '{owner}')" if owner else ""))
        except Exception as e:
            self.face_note.config(text=f"Can't show this face: {e}")

    def face_from_photo(self):
        if not self.player:
            messagebox.showinfo("Pick a player", "Select a player first.")
            return
        photo = filedialog.askopenfilename(title="Front-facing photo",
                                           filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp"), ("All", "*.*")])
        if not photo:
            return
        try:
            tid = int(self.template_id.get())
        except tk.TclError:
            tid = FALLBACK_TEMPLATE
        tpath = os.path.join(self.mm.backup_dir, face_file(tid))
        if not os.path.exists(tpath):
            tpath = self.face_path(tid)
        if not tpath:
            messagebox.showerror("Template missing", f"No face file {face_file(tid)} to use as template.")
            return
        self.face_note.config(text="Making the face... (a few seconds)")

        def work():
            from nba2k14 import face
            iff = IffFile.load(tpath)
            img = face.apply_to_iff(iff, face.load_photo(photo))
            return iff, img

        def done(result, err):
            if err:
                self.face_note.config(text=f"Couldn't make a face: {err}")
                return
            self.new_face = result
            self.set_preview(result[1])
            self.face_note.config(text=f"New face ready (template {face_file(tid)}). "
                                       f"Click Install face to use it as {face_file(int(self.face_id.get()))}.")
            self.install_btn.config(state="normal")
        self.run_bg(work, done)

    def install_face(self):
        if not self.new_face:
            return
        if game_running():
            messagebox.showwarning("Game running", "Close NBA 2K14 first.")
            return
        fid = int(self.face_id.get())
        out = os.path.join(self.mm.create(FACE_MOD, "Faces made with the Player Creator"), face_file(fid))
        self.new_face[0].save(out)
        try:
            self.mm.install(FACE_MOD, force=True)
        except ModError as e:
            messagebox.showerror("Install failed", str(e))
            return
        self.new_face = None
        self.install_btn.config(state="disabled")
        note = ""
        if self.player and self.player.get("face_id") != fid:
            note = "\nRemember to Save player so he uses this face ID."
        messagebox.showinfo("Installed", f"Installed {face_file(fid)} in mod '{FACE_MOD}'.{note}\n"
                                         "Uninstall anytime: python tools/modmgr.py uninstall \"Player Faces\"")
        self.show_face()

    # ----- saving
    def save_player(self):
        p = self.player
        if not p:
            return
        if game_running():
            messagebox.showwarning("Game running", "Close NBA 2K14 first, or it will overwrite the file.")
            return
        try:
            # only write height / weight when changed, so untouched values keep their exact decimals
            if self.height_in.get() != round(p.get("height_cm") / 2.54):
                p.set("height_cm", self.height_in.get() * 2.54)
            if self.weight.get() != round(p.get("weight_lb")):
                p.set("weight_lb", float(self.weight.get()))
            if str(self.jersey_w.cget("state")) != "disabled":
                p.set("jersey", self.jersey.get())
                if self.position.get() in POSITIONS:
                    p.set("position", POSITIONS.index(self.position.get()))
            p.set("face_id", self.face_id.get())
            raws = p.ratings()
            for i, v in enumerate(self.rating_vars):
                if rating_from_raw(raws[i]) != v.get():   # leave unchanged ratings bit-for-bit
                    raws[i] = raw_from_rating(v.get())
            p.set_ratings(raws)
        except (tk.TclError, ValueError) as e:
            messagebox.showerror("Bad value", str(e))
            return
        if self.roster.built_in:
            # never edit the game file directly: save the roster into a mod and install it
            out = os.path.join(self.mm.create(ROSTER_MOD, "Edits to the game's built-in roster"), "roster.iff")
            self.roster.save(path=out)
            try:
                self.mm.install(ROSTER_MOD, force=True)
            except ModError as e:
                messagebox.showerror("Install failed", str(e))
                return
            messagebox.showinfo("Saved", f"Saved {p.name} into mod '{ROSTER_MOD}' (installed).\n"
                                         "The original roster is backed up; uninstall the mod to undo.\n"
                                         "Play Now and new saves use it; existing saves keep their own league.")
            return
        bak = self.roster.save()
        messagebox.showinfo("Saved", f"Saved {p.name}.\nBackup: {os.path.basename(bak)}")


if __name__ == "__main__":
    App().mainloop()
