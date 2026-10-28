#!/usr/bin/env python3
"""djvu-recover - the Windows app.

A window around the same engine recover.py uses: pick the encrypted folders, pick an
output folder, press Start. Nothing on this computer is decrypted - STOP/Djvu only
overwrites the first ~150 KB of each file, so the rest is rebuilt around the damage.
"""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import recover


# ---------------------------------------------------------------- worker
# The rebuild loop itself lives in recover.run_recovery(), shared with the command line.


# ---------------------------------------------------------------- window
class App:
    def __init__(self, root):
        self.root = root
        root.title("djvu-recover - rebuild STOP/Djvu photos and videos")
        root.geometry("900x660")
        root.minsize(760, 560)
        self.messages = queue.Queue()
        self.thread = None
        self.stop_flag = threading.Event()
        self.result = None
        self.out_default = tk.StringVar()
        self.grey = tk.StringVar(value="keep")
        self.rotate = tk.StringVar(value="0")
        self.overwrite = tk.BooleanVar(value=False)
        self.folders, self.donors = [], []
        self._build()
        self.root.after(120, self._poll)

    # ------------------------------------------------------------ layout
    def _build(self):
        pad = dict(padx=10, pady=6)
        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        box1 = ttk.LabelFrame(top, text="1.  Folders holding the encrypted files")
        box1.pack(side="left", fill="both", expand=True)
        self.folder_list = tk.Listbox(box1, height=4, exportselection=False)
        self.folder_list.pack(fill="both", expand=True, padx=8, pady=6)
        row = ttk.Frame(box1)
        row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(row, text="Add folder...", command=self.add_folder).pack(side="left")
        ttk.Button(row, text="Remove", command=lambda: self._remove(self.folder_list, self.folders)).pack(side="left", padx=6)

        box2 = ttk.LabelFrame(top, text="3.  Healthy photos/videos from the same phone (optional)")
        box2.pack(side="left", fill="both", expand=True)
        self.donor_list = tk.Listbox(box2, height=4, exportselection=False)
        self.donor_list.pack(fill="both", expand=True, padx=8, pady=6)
        row = ttk.Frame(box2)
        row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(row, text="Add folder...", command=self.add_donor).pack(side="left")
        ttk.Button(row, text="Remove", command=lambda: self._remove(self.donor_list, self.donors)).pack(side="left", padx=6)
        ttk.Label(box2, text="Better colours, and videos whose index was\ndestroyed can be rebuilt.",
                  foreground="#555").pack(fill="x", padx=8, pady=(0, 8))

        box3 = ttk.LabelFrame(self.root, text="2.  Where the recovered files go")
        box3.pack(fill="x", **pad)
        row = ttk.Frame(box3)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Entry(row, textvariable=self.out_default).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self.pick_out).pack(side="left", padx=6)

        opts = ttk.LabelFrame(self.root, text="Options")
        opts.pack(fill="x", **pad)
        row = ttk.Frame(opts)
        row.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(row, text="Grey strip at the top:").pack(side="left")
        for value, label in (("keep", "keep it"), ("crop", "crop it off"), ("both", "write both")):
            ttk.Radiobutton(row, text=label, value=value, variable=self.grey).pack(side="left", padx=6)
        ttk.Label(row, text="     Rotate photos:").pack(side="left")
        ttk.Combobox(row, textvariable=self.rotate, values=("0", "90", "180", "270"),
                     width=4, state="readonly").pack(side="left")
        ttk.Checkbutton(row, text="overwrite files already recovered",
                        variable=self.overwrite).pack(side="left", padx=14)
        row2 = ttk.Frame(opts)
        row2.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(row2, text="Use 180 if photos come out upside down (the EXIF rotation flag was destroyed).",
                  foreground="#555").pack(side="left")

        row = ttk.Frame(self.root)
        row.pack(fill="x", padx=10)
        self.start_btn = ttk.Button(row, text="Start recovery", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(row, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(row, text="Open output folder", command=self.open_out).pack(side="left", padx=6)
        self.status = ttk.Label(row, text="Ready.")
        self.status.pack(side="left", padx=14)

        self.bar = ttk.Progressbar(self.root, mode="determinate", maximum=100)
        self.bar.pack(fill="x", padx=10, pady=(6, 0))

        self.log = tk.Text(self.root, height=12, state="disabled", wrap="word",
                           background="#111", foreground="#ddd", insertbackground="#ddd")
        self.log.pack(fill="both", expand=True, padx=10, pady=10)

        for line in ("This rebuilds the part of each file that survived; it does not decrypt anything.",
                     "Photos: JPEG (.jpg).  Videos: .mp4 .m4v .mov .3gp .3g2.  The first ~150 KB of each file is lost."):
            self._write(line)

    # ------------------------------------------------------------ actions
    def add_folder(self):
        self._add(self.folder_list, self.folders)

    def add_donor(self):
        self._add(self.donor_list, self.donors)

    def _add(self, listbox, store):
        chosen = filedialog.askdirectory(title="Choose a folder")
        if chosen and chosen not in store:
            store.append(chosen)
            listbox.insert("end", chosen)

    def _remove(self, listbox, store):
        for index in reversed(listbox.curselection()):
            listbox.delete(index)
            del store[index]

    def pick_out(self):
        chosen = filedialog.askdirectory(title="Where should the recovered files go?")
        if chosen:
            self.out_default.set(chosen)

    def open_out(self):
        path = self.out_default.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showinfo("djvu-recover", "No output folder yet.")
            return
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}" &')

    def start(self):
        folders = [f for f in self.folders if os.path.isdir(f)]
        if not folders:
            messagebox.showwarning("djvu-recover", "Add at least one folder holding the encrypted files.")
            return
        out = self.out_default.get().strip() or os.path.join(folders[0], "recovered")
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("djvu-recover", f"Cannot create the output folder:\n{exc}")
            return
        self.out_default.set(out)
        self.stop_flag.clear()
        self.result = None
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.bar.config(mode="indeterminate")
        self.bar.start(12)
        self.status.config(text="Working ...")
        self._write("-" * 60)
        self.thread = threading.Thread(
            target=self._work, daemon=True,
            args=(folders, out, [d for d in self.donors if os.path.isdir(d)],
                  self.grey.get(), int(self.rotate.get()), self.overwrite.get()))
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        self.status.config(text="Stopping after the current file ...")

    def _work(self, folders, out, donors, grey, rotate, overwrite):
        say = lambda text="": self.messages.put(("log", str(text)))
        progress = lambda done, total: self.messages.put(("progress", done, total))
        try:
            self.messages.put(("log", "Reading healthy photos to learn the camera's tables ..."))
            lib = recover.build_library(donors, say)
            refs = recover.build_video_refs(donors, say)
            self.messages.put(("log", "Looking for encrypted files ..."))
            found, unsupported = recover.scan(folders)
            self.messages.put(("log", f"Found {len(found)} file(s) to rebuild."))
            if unsupported:
                say(f"Found {len(unsupported)} more encrypted file(s) that cannot be rebuilt "
                    f"(PNG, HEIC, PDF, ZIP ...) - skipping them.")
            if not found:
                say("Nothing rebuildable here.")
                result = {"counts": {"ok": 0, "review": 0, "skipped": 0, "failed": 0},
                          "failures": [], "total": 0}
            else:
                result = recover.run_recovery(found, out, lib, refs, grey, rotate, overwrite,
                                              say=say, progress=progress,
                                              should_stop=self.stop_flag.is_set)
            self.messages.put(("done", result))
        except Exception as exc:
            self.messages.put(("fatal", str(exc) or exc.__class__.__name__))

    # ------------------------------------------------------------ ui updates
    def _poll(self):
        try:
            while True:
                kind, *payload = self.messages.get_nowait()
                if kind == "log":
                    self._write(payload[0])
                elif kind == "progress":
                    done, total = payload
                    self.bar.stop()
                    self.bar.config(mode="determinate", maximum=total or 1, value=done)
                    self.status.config(text=f"{done} of {total} files")
                elif kind == "done":
                    self._finish(payload[0])
                elif kind == "fatal":
                    self._write(f"ERROR: {payload[0]}")
                    self._finish(None)
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _finish(self, result):
        self.result = result
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.bar.stop()
        self.bar.config(mode="determinate", value=0)
        if result is None:
            self.status.config(text="Failed - see the log.")
            return
        counts = result["counts"]
        recovered = counts["ok"] + counts["review"]
        self.status.config(text=f"Done: {recovered} recovered, {counts['skipped']} skipped, "
                                f"{counts['failed']} failed")
        self._write("-" * 60)
        self._write(f"Recovered: {recovered}    Skipped: {counts['skipped']}    "
                    f"Failed: {counts['failed']}")
        self._write(f"Output folder: {self.out_default.get()}")
        if counts["review"]:
            self._write("Some files are marked 'review' - open them and have a look.")
        if counts["failed"]:
            self._write("Could not recover:")
            for rel, reason in result["failures"]:
                self._write(f"  - {rel}: {reason}")

    def _write(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def on_close(self):
        if self.thread and self.thread.is_alive():
            if not messagebox.askyesno("djvu-recover", "A recovery is running. Stop it and close?"):
                return
            self.stop_flag.set()
        self.root.destroy()


def set_dpi_awareness():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def main():
    set_dpi_awareness()
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
