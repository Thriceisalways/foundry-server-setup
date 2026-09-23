"""Foundry Server Setup: a step-by-step wizard for self-hosting Foundry VTT on Windows."""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import setup_tasks as T
from fsm_common import (APP_HOME, C, COMPRESSOR_LABELS, CONFIG_PATH, FONT, FOUNDRY_DEFAULT_DATA,
                        ICON_PATH, MONO, NO_WINDOW, SYMBOL_PRESETS, apply_style, count_worlds,
                        has_admin_password, licence_path,
                        detect_foundry_data, find_compressors, is_within, load_config, make_backup,
                        open_folder, safe_name, save_config, styled_button, synced_folder_name,
                        valid_symbol)

WRAP = 600
DEFAULT_FOUNDRY_DIR = r"C:\FoundryVTT"
DEFAULT_BACKUP_DIR = os.path.join(os.path.expanduser("~"), "FoundryVTTBackups")
CF_MODES = ("manual", "api", "existing")

URL_FOUNDRY = "https://foundryvtt.com/me/licenses"
URL_CF_DASH = "https://dash.cloudflare.com/"
URL_CF_ZERO_TRUST = "https://one.dash.cloudflare.com/"
URL_CF_TOKENS = "https://dash.cloudflare.com/profile/api-tokens"


# ================================================================ widgets

def h1(parent, text):
    tk.Label(parent, text=text, font=(FONT, 18, "bold"), bg=C["bg"], fg=C["text"],
             anchor="w").pack(fill="x", pady=(0, 6))


def para(parent, text="", fg=None, size=10, pady=(0, 10), bold=False):
    lbl = tk.Label(parent, text=text, font=(FONT, size, "bold" if bold else "normal"),
                   bg=C["bg"], fg=fg or C["text"], wraplength=WRAP, justify="left", anchor="w")
    lbl.pack(fill="x", pady=pady)
    return lbl


def section(parent, text):
    tk.Label(parent, text=text.upper(), font=(FONT, 9, "bold"), bg=C["bg"], fg=C["muted"],
             anchor="w").pack(fill="x", pady=(10, 4))


def path_row(parent, var, kind=None, show=None, filetypes=None):
    """Entry with an optional Browse button. kind: 'dir', 'file', or None."""
    row = tk.Frame(parent, bg=C["bg"])
    entry = tk.Entry(row, textvariable=var, font=(FONT, 10), bg=C["panel"], fg=C["text"],
                     insertbackground=C["text"], relief="flat", highlightthickness=1,
                     highlightbackground=C["border"], highlightcolor=C["accent"], show=show or "")
    entry.pack(side="left", fill="x", expand=True, ipady=5)
    if kind:
        def browse():
            if kind == "dir":
                chosen = filedialog.askdirectory(initialdir=var.get() or None)
            else:
                chosen = filedialog.askopenfilename(filetypes=filetypes or [("All files", "*.*")])
            if chosen:
                var.set(os.path.normpath(chosen))
        styled_button(row, "Browse", browse, "grey").pack(side="left", padx=(8, 0))
    row.pack(fill="x", pady=(0, 8))
    return row


def labelled(parent, text):
    tk.Label(parent, text=text, font=(FONT, 10), bg=C["bg"], fg=C["muted"],
             anchor="w").pack(fill="x", pady=(4, 2))


def radio(parent, text, var, value, command=None, desc=None):
    frame = tk.Frame(parent, bg=C["bg"])
    rb = tk.Radiobutton(frame, text=text, variable=var, value=value, command=command,
                        font=(FONT, 10, "bold"), bg=C["bg"], fg=C["text"], selectcolor=C["panel"],
                        activebackground=C["bg"], activeforeground=C["text"], anchor="w",
                        highlightthickness=0, bd=0)
    rb.pack(fill="x")
    if desc:
        tk.Label(frame, text=desc, font=(FONT, 9), bg=C["bg"], fg=C["muted"], wraplength=WRAP - 30,
                 justify="left", anchor="w").pack(fill="x", padx=(26, 0))
    frame.pack(fill="x", pady=3)
    return rb


def checkbox(parent, text, var):
    cb = tk.Checkbutton(parent, text=text, variable=var, font=(FONT, 10), bg=C["bg"], fg=C["text"],
                        selectcolor=C["panel"], activebackground=C["bg"], activeforeground=C["text"],
                        anchor="w", highlightthickness=0, bd=0)
    cb.pack(fill="x", pady=2)
    return cb


CALLOUT_STYLES = {
    #          border        background   icon
    "warn":   (C["amber"], "#2a2412", "⚠"),
    "info":   (C["blue"],  "#132033", "ℹ"),
    "ok":     (C["green"], "#10261f", "✓"),
    "danger": (C["red"],   "#2c1517", "⚠"),
}


def callout(parent, title, text, kind="info", pack=True):
    """A highlighted box with a coloured left edge, for warnings and tips."""
    border, bg, icon = CALLOUT_STYLES[kind]
    outer = tk.Frame(parent, bg=border)
    inner = tk.Frame(outer, bg=bg)
    inner.pack(fill="both", expand=True, padx=(4, 0))
    if title:
        tk.Label(inner, text=f"{icon}  {title}", font=(FONT, 10, "bold"), bg=bg, fg=border,
                 anchor="w").pack(fill="x", padx=12, pady=(10, 2))
    tk.Label(inner, text=text, font=(FONT, 10), bg=bg, fg=C["text"], wraplength=WRAP - 40,
             justify="left", anchor="w").pack(fill="x", padx=12, pady=(0 if title else 10, 10))
    if pack:
        outer.pack(fill="x", pady=(0, 10))
    return outer


def log_box(parent, height=7):
    box = scrolledtext.ScrolledText(parent, height=height, bg=C["panel"], fg=C["log_default"],
                                    font=(MONO, 9), relief="flat", bd=0, state="disabled")
    for tag in ("error", "warn", "info", "default"):
        box.tag_config(tag, foreground=C[f"log_{tag}"])
    box.pack(fill="both", expand=True, pady=(6, 0))
    return box


# ================================================================ page base

class Page(tk.Frame):
    title = ""

    def __init__(self, wiz):
        super().__init__(wiz.content, bg=C["bg"])
        self.wiz = wiz
        self.s = wiz.state
        self.log_widget = None
        self.progress = None
        self.build()

    def applies(self):
        return True

    def build(self):
        pass

    def on_enter(self):
        pass

    def validate(self):
        return True

    def add_progress(self):
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100,
                                        style="Wizard.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(6, 0))

    def log_line(self, msg, tag="default"):
        if not self.log_widget:
            return
        self.log_widget.config(state="normal")
        self.log_widget.insert("end", msg + "\n", tag)
        self.log_widget.see("end")
        self.log_widget.config(state="disabled")

    def set_progress(self, done, total):
        if not self.progress:
            return
        if total:
            self.progress.config(mode="determinate", value=done * 100 / total)
        else:
            self.progress.config(mode="indeterminate")
            self.progress.step(2)

    def task(self, func, on_done):
        self.wiz.run_task(self, func, on_done)

    def fail(self, err, title="Something went wrong"):
        msg = str(err) if isinstance(err, T.SetupError) else f"{type(err).__name__}: {err}"
        self.log_line(msg, "error")
        messagebox.showerror(title, msg)


def status_label(parent):
    return tk.Label(parent, text="", font=(FONT, 10, "bold"), bg=C["bg"], fg=C["muted"],
                    wraplength=WRAP, justify="left", anchor="w")


def set_status(label, text, ok):
    colour = {True: C["green"], False: C["red"], None: C["amber"]}[ok]
    mark = {True: "✓ ", False: "✗ ", None: "! "}[ok]
    label.config(text=mark + text if text else "", fg=colour)


# ================================================================ pages

class WelcomePage(Page):
    title = "Welcome"

    def build(self):
        h1(self, "Foundry Server Setup")
        para(self, "This wizard sets up a self-hosted Foundry VTT server on this PC: Foundry itself, "
                   "the right version of Node.js, an optional Cloudflare Tunnel so players can join "
                   "from your own web address, backups, and a server manager that opens when you log in.")
        para(self, "Before you start, have your foundryvtt.com login handy. For a web address you'll "
                   "also need a Cloudflare account and a domain.", fg=C["muted"])
        if os.path.isfile(CONFIG_PATH):
            callout(self, "Already installed",
                    "The server manager is already set up on this PC. Click Next to change its settings "
                    "(your current choices are filled in), or uninstall it.", "info")
            styled_button(self, "Uninstall...", self.open_uninstall, "red").pack(anchor="w", pady=(0, 6))
        section(self, "System check")
        self.rows = {}
        checks = [("windows", "Windows 10 or 11 (64-bit)"),
                  ("admin", "Running as administrator"),
                  ("disk", "Free disk space"),
                  ("internet", "Internet connection")]
        if os.path.isfile(CONFIG_PATH):
            checks.append(("manager", "Server manager closed"))
        for key, label in checks:
            row = tk.Frame(self, bg=C["bg"])
            tk.Label(row, text=label, font=(FONT, 10), bg=C["bg"], fg=C["text"], width=28,
                     anchor="w").pack(side="left")
            value = tk.Label(row, text="...", font=(FONT, 10, "bold"), bg=C["bg"], fg=C["muted"])
            value.pack(side="left")
            row.pack(fill="x", pady=2)
            self.rows[key] = value
        self.admin_btn = styled_button(self, "Restart as administrator", self.elevate, "accent")
        self.results = {}

    def set_row(self, key, ok, text):
        self.results[key] = ok
        set_status(self.rows[key], text, ok)

    def on_enter(self):
        import platform
        win = T.windows_ok() and platform.machine().endswith("64")
        self.set_row("windows", win, "OK" if win else "Not supported")
        admin = T.is_admin()
        self.set_row("admin", admin, "Yes" if admin else "No")
        if admin:
            self.admin_btn.pack_forget()
        else:
            self.admin_btn.pack(anchor="w", pady=(10, 0))
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        free_gb = shutil.disk_usage(drive).free / 1024 ** 3
        self.set_row("disk", free_gb >= 3 or None, f"{free_gb:.1f} GB free on {drive}")
        if "manager" in self.rows:
            closed = not T.manager_running()
            self.set_row("manager", closed, "Yes" if closed else "No, close it (stop the server first)")
        self.rows["internet"].config(text="Checking...", fg=C["muted"])
        self.task(lambda emit, progress: T.internet_ok(),
                  lambda ok, err: self.set_row("internet", bool(ok) or None,
                                               "OK" if ok else "Offline (downloads will fail)"))

    def elevate(self):
        if T.relaunch_as_admin():
            self.wiz.root.destroy()

    def open_uninstall(self):
        if not T.is_admin():
            messagebox.showerror("Administrator needed", "Click 'Restart as administrator' first.")
            return
        top = tk.Toplevel(self.wiz.root)
        top.title("Uninstall")
        top.geometry("720x760")
        top.configure(bg=C["bg"])
        top.transient(self.wiz.root)
        top.grab_set()
        view = UninstallView(top, on_cancel=top.destroy, on_done=self.wiz.root.destroy)
        view.pack(fill="both", expand=True, padx=30, pady=24)
        top.protocol("WM_DELETE_WINDOW", view.cancel)

    def validate(self):
        if not self.results.get("windows"):
            messagebox.showerror("Not supported", "This setup needs 64-bit Windows 10 or 11.")
            return False
        if not self.results.get("admin"):
            messagebox.showerror("Administrator needed",
                                 "Setup needs administrator rights to install the tunnel service "
                                 "and firewall rules.\n\nClick 'Restart as administrator'.")
            return False
        if "manager" in self.rows:
            closed = not T.manager_running()
            self.set_row("manager", closed, "Yes" if closed else "No, close it (stop the server first)")
            if not closed:
                messagebox.showerror("Server manager open",
                                     "Stop the server and close the server manager before running "
                                     "setup, so it can be updated.")
                return False
        if self.results.get("internet") is None:
            return messagebox.askyesno("No internet",
                                       "Setup couldn't reach the internet. Downloads will fail unless "
                                       "you already have the Foundry zip.\n\nContinue anyway?")
        return True


class NamePage(Page):
    title = "Server name"

    def build(self):
        h1(self, "Name your server")
        para(self, "The name and symbol appear on the server manager window. The name is also used "
                   "for the desktop shortcut and startup task.")
        self.var = tk.StringVar(value=self.s["server_name"])
        path_row(self, self.var)

        section(self, "Symbol")
        grid = tk.Frame(self, bg=C["bg"])
        grid.pack(anchor="w")
        self.symbol = tk.StringVar(value=self.s.get("symbol", SYMBOL_PRESETS[0]))
        self.symbol_buttons = {}
        for i, sym in enumerate(SYMBOL_PRESETS):
            b = tk.Button(grid, text=sym, font=(FONT, 15), width=3, relief="flat", bd=0,
                          cursor="hand2", highlightthickness=0,
                          command=lambda v=sym: self.symbol.set(v))
            b.grid(row=0, column=i, padx=2, pady=2)
            self.symbol_buttons[sym] = b
        custom = tk.Frame(self, bg=C["bg"])
        custom.pack(fill="x", pady=(6, 0))
        tk.Label(custom, text="Or type your own:", font=(FONT, 10), bg=C["bg"],
                 fg=C["muted"]).pack(side="left")
        tk.Entry(custom, textvariable=self.symbol, width=4, font=(FONT, 13), justify="center",
                 bg=C["panel"], fg=C["text"], insertbackground=C["text"], relief="flat",
                 highlightthickness=1, highlightbackground=C["border"],
                 highlightcolor=C["accent"]).pack(side="left", padx=8, ipady=2)
        tk.Label(custom, text="Win + . opens the Windows symbol picker. Emoji may not display.",
                 font=(FONT, 9), bg=C["bg"], fg=C["muted"]).pack(side="left")

        section(self, "App icon")
        self.icon_mode = tk.StringVar(value=self.s.get("icon_mode", "symbol"))
        radio(self, "Match the symbol", self.icon_mode, "symbol", self.update_preview)
        radio(self, "Use my own image (.png, .jpg, or .ico)", self.icon_mode, "image", self.update_preview)
        self.icon_file = tk.StringVar(value=self.s.get("icon_source", ""))
        self.icon_row_holder = tk.Frame(self, bg=C["bg"])
        self.icon_row_holder.pack(fill="x")
        self.icon_row = tk.Frame(self.icon_row_holder, bg=C["bg"])
        path_row(self.icon_row, self.icon_file, "file",
                 filetypes=[("Images", "*.png *.jpg *.jpeg *.ico *.bmp"), ("All files", "*.*")])

        section(self, "Preview")
        preview = tk.Frame(self, bg=C["panel"])
        preview.pack(fill="x")
        self.icon_preview = tk.Label(preview, bg=C["panel"])
        self.icon_preview.pack(side="left", padx=(16, 0), pady=12)
        self.preview = tk.Label(preview, font=(FONT, 18, "bold"), bg=C["panel"], fg=C["accent"])
        self.preview.pack(side="left", fill="x", expand=True, pady=16)
        self.icon_note = para(self, fg=C["muted"], size=9, pady=(6, 0))
        self._photo = None

        for v in (self.var, self.symbol, self.icon_file):
            v.trace_add("write", lambda *_: self.update_preview())
        self.update_preview()

    def update_preview(self):
        name = self.var.get().strip() or "..."
        sym = self.symbol.get().strip()
        shown = sym if valid_symbol(sym) else "?"
        self.preview.config(text=f"{shown} {name} Server Manager {shown}")
        for value, b in self.symbol_buttons.items():
            active = value == sym
            b.config(bg=C["accent"] if active else C["panel"], fg="white" if active else C["text"],
                     activebackground=C["accent_dark"], activeforeground="white")

        if self.icon_mode.get() == "image":
            self.icon_row.pack(fill="x")
        else:
            self.icon_row.pack_forget()
        self.icon_note.config(text="")
        if not T.pillow_available():
            self.icon_note.config(text="Icon preview needs Pillow (pip install pillow).")
            return
        from PIL import ImageTk
        try:
            if self.icon_mode.get() == "image":
                path = self.icon_file.get().strip()
                img = T.load_icon_image(path, 64) if os.path.isfile(path) else None
            else:
                img = T.render_symbol(shown, 64) if valid_symbol(sym) else None
        except T.SetupError as e:
            img = None
            self.icon_note.config(text=str(e))
        if img is None:
            self.icon_preview.config(image="", text="", width=8)
            return
        self._photo = ImageTk.PhotoImage(img)
        self.icon_preview.config(image=self._photo, width=64)
        self.wiz.root.iconphoto(False, self._photo)

    def validate(self):
        name = self.var.get().strip()
        sym = self.symbol.get().strip()
        if not name:
            messagebox.showerror("Name needed", "Give your server a name.")
            return False
        if not valid_symbol(sym):
            messagebox.showerror("Symbol", "Pick a symbol from the list, or type one or two characters. "
                                           "Most emoji can't be shown in the manager window, so stick "
                                           "to symbols like the ones above.")
            return False
        mode = self.icon_mode.get()
        source = self.icon_file.get().strip()
        prev_source = (self.s.get("prev_config") or {}).get("icon_source", "")
        keep_current = (mode == "image" and not os.path.isfile(source) and source == prev_source
                        and os.path.isfile(ICON_PATH))
        if mode == "image" and not keep_current:
            if not os.path.isfile(source):
                messagebox.showerror("App icon", "Choose an image for the app icon.")
                return False
            if T.pillow_available():
                try:
                    T.load_icon_image(source, 32)
                except T.SetupError as e:
                    messagebox.showerror("App icon", str(e))
                    return False
        self.s.update(server_name=name, safe_name=safe_name(name), symbol=sym,
                      icon_mode=mode, icon_source=source)
        return True


class FoundryPage(Page):
    title = "Foundry VTT"

    def build(self):
        h1(self, "Install Foundry VTT")
        para(self, "This is the Foundry program itself. Your worlds are stored separately, in a data "
                   "folder you'll choose in a later step.")
        para(self, "Setup needs the Node.js build of Foundry, not the Windows desktop app. On "
                   "foundryvtt.com open Purchased Licences, set Operating System to 'Node.js', then "
                   "either download the zip or click 'Timed URL' to copy a link.", fg=C["muted"])
        styled_button(self, "Open foundryvtt.com", lambda: webbrowser.open(URL_FOUNDRY),
                      "grey").pack(anchor="w", pady=(0, 8))

        prev_foundry = (self.s.get("prev_config") or {}).get("foundry_path")
        has_prev = bool(prev_foundry and T.find_main_js(prev_foundry))
        self.mode = tk.StringVar(value="existing" if has_prev else "url")
        for value, text in (("url", "Download with a timed URL"),
                            ("zip", "Install from a zip I've already downloaded"),
                            ("existing", "Foundry is already installed")):
            radio(self, text, self.mode, value, self.refresh)

        self.opts = tk.Frame(self, bg=C["bg"])
        self.opts.pack(fill="x", pady=(8, 0))
        self.url = tk.StringVar()
        self.zip = tk.StringVar()
        self.dest = tk.StringVar(value=DEFAULT_FOUNDRY_DIR)
        self.existing = tk.StringVar(value=prev_foundry if has_prev else DEFAULT_FOUNDRY_DIR)

        self.url_frame = tk.Frame(self.opts, bg=C["bg"])
        labelled(self.url_frame, "Timed URL (they expire after a few minutes)")
        path_row(self.url_frame, self.url)
        self.zip_frame = tk.Frame(self.opts, bg=C["bg"])
        labelled(self.zip_frame, "Foundry zip file")
        path_row(self.zip_frame, self.zip, "file", filetypes=[("Zip files", "*.zip")])
        self.dest_frame = tk.Frame(self.opts, bg=C["bg"])
        labelled(self.dest_frame, "Install the Foundry program to")
        path_row(self.dest_frame, self.dest, "dir")
        self.existing_frame = tk.Frame(self.opts, bg=C["bg"])
        labelled(self.existing_frame, "Folder containing main.js")
        path_row(self.existing_frame, self.existing, "dir")

        actions = tk.Frame(self, bg=C["bg"])
        actions.pack(fill="x", pady=(4, 0))
        self.action_btn = styled_button(actions, "", self.act, "green")
        self.action_btn.pack(side="left")
        self.status = status_label(self)
        self.status.pack(fill="x", pady=(8, 0))
        self.add_progress()
        self.log_widget = log_box(self, 5)
        self.refresh()

    def refresh(self):
        for f in (self.url_frame, self.zip_frame, self.dest_frame, self.existing_frame):
            f.pack_forget()
        mode = self.mode.get()
        if mode == "url":
            self.url_frame.pack(fill="x")
            self.dest_frame.pack(fill="x")
            self.action_btn.config(text="Download and install")
        elif mode == "zip":
            self.zip_frame.pack(fill="x")
            self.dest_frame.pack(fill="x")
            self.action_btn.config(text="Install")
        else:
            self.existing_frame.pack(fill="x")
            self.action_btn.config(text="Check folder")

    def on_enter(self):
        if self.s.get("foundry_dir"):
            set_status(self.status, f"Foundry {self.s.get('foundry_label') or ''} at "
                                    f"{self.s['foundry_dir']}", True)

    def accept(self, folder, gen, label):
        self.s.update(foundry_dir=folder, foundry_gen=gen, foundry_label=label)
        set_status(self.status, f"Foundry {label or '(version unknown)'} at {folder}", True)

    def check_existing(self, quiet=False):
        folder = T.find_main_js(self.existing.get().strip())
        if not folder:
            set_status(self.status, "No main.js found in that folder.", False)
            if not quiet:
                messagebox.showerror("Not found", "That folder doesn't contain Foundry's main.js.\n\n"
                                                  "Pick the folder you extracted the Node.js build into.")
            return False
        gen, label = T.foundry_version(folder)
        self.accept(folder, gen, label)
        return True

    def act(self):
        mode = self.mode.get()
        if mode == "existing":
            self.check_existing()
            return
        source = self.url.get().strip() if mode == "url" else self.zip.get().strip()
        dest = self.dest.get().strip()
        if mode == "url" and not source.lower().startswith("https://"):
            messagebox.showerror("Timed URL", "Paste the full timed URL, starting with https://")
            return
        if mode == "zip" and not os.path.isfile(source):
            messagebox.showerror("Zip file", "Choose the Foundry zip you downloaded.")
            return
        if not dest:
            messagebox.showerror("Install folder", "Choose where to install Foundry.")
            return
        if T.find_main_js(dest):
            if not messagebox.askyesno("Update Foundry?",
                                       f"Foundry is already installed in {dest}.\n\nInstall over it? "
                                       "Worlds live in the data folder, so they won't be touched."):
                return
        elif os.path.isdir(dest) and os.listdir(dest):
            if not messagebox.askyesno("Folder not empty", f"{dest} already has files in it. Continue?"):
                return

        def done(result, err):
            if err:
                self.fail(err, "Foundry install failed")
            else:
                self.accept(*result)
        self.task(lambda emit, progress: T.install_foundry(source, dest, emit, progress), done)

    def validate(self):
        if self.mode.get() == "existing" and not self.s.get("foundry_dir"):
            if not self.check_existing():
                return False
        folder = self.s.get("foundry_dir")
        if not folder or not os.path.isfile(os.path.join(folder, "main.js")):
            messagebox.showerror("Foundry not installed", "Install Foundry (or point setup at an "
                                                          "existing install) before continuing.")
            return False
        if not self.s.get("foundry_gen"):
            v14 = messagebox.askyesno("Foundry version", "Setup couldn't work out which Foundry version "
                                                         "this is.\n\nIs it Version 14 or newer?")
            self.s["foundry_gen"] = 14 if v14 else 13
        return True


class NodePage(Page):
    title = "Node.js"

    def build(self):
        h1(self, "Node.js")
        self.req = para(self)
        para(self, "The recommended option downloads a private copy of Node.js just for Foundry. It "
                   "doesn't touch any other Node.js install on this PC.", fg=C["muted"])
        self.mode = tk.StringVar(value="private")
        self.private_rb = radio(self, "", self.mode, "private", self.refresh)
        self.system_rb = radio(self, "", self.mode, "system", self.refresh)
        radio(self, "Choose node.exe myself", self.mode, "custom", self.refresh)
        self.custom = tk.StringVar()
        self.custom_holder = tk.Frame(self, bg=C["bg"])
        self.custom_holder.pack(fill="x")
        self.custom_frame = tk.Frame(self.custom_holder, bg=C["bg"])
        path_row(self.custom_frame, self.custom, "file", filetypes=[("node.exe", "node.exe")])
        actions = tk.Frame(self, bg=C["bg"])
        actions.pack(fill="x", pady=(6, 0))
        self.action_btn = styled_button(actions, "Set up Node.js", self.act, "green")
        self.action_btn.pack(side="left")
        self.status = status_label(self)
        self.status.pack(fill="x", pady=(8, 0))
        self.add_progress()
        self.log_widget = log_box(self, 5)

    def on_enter(self):
        gen = self.s["foundry_gen"]
        lo, hi = T.node_requirement(gen)
        self.major = lo
        need = f"Node.js {lo}" + (f" (not {hi} or newer)" if hi else " or newer")
        self.req.config(text=f"Foundry V{gen} needs {need}.")
        self.private_rb.config(text=f"Download Node.js {lo} for Foundry (recommended)")
        self.system_node = T.find_system_node()
        sys_major = T.node_major(self.system_node)
        if self.system_node and T.node_compatible(sys_major, gen):
            self.system_rb.config(text=f"Use the Node.js {sys_major} already installed", state="normal")
        else:
            found = f"found v{sys_major}, which won't work" if sys_major else "not found"
            self.system_rb.config(text=f"Use installed Node.js ({found})", state="disabled")
            if self.mode.get() == "system":
                self.mode.set("private")
        if not (self.s.get("node_path") and T.node_compatible(T.node_major(self.s["node_path"]), gen)):
            self.s["node_path"] = T.find_private_node(lo)
        if self.s.get("node_path"):
            set_status(self.status, f"Using {self.s['node_path']}", True)
        else:
            set_status(self.status, "", True)
        self.refresh()

    def refresh(self):
        self.custom_frame.pack_forget()
        if self.mode.get() == "custom":
            self.custom_frame.pack(fill="x")

    def use(self, path):
        major = T.node_major(path)
        if not T.node_compatible(major, self.s["foundry_gen"]):
            set_status(self.status, f"That's Node.js {major or '?'}, which won't run this Foundry version.",
                       False)
            return False
        self.s["node_path"] = path
        set_status(self.status, f"Using Node.js {major}: {path}", True)
        return True

    def act(self):
        mode = self.mode.get()
        if mode == "system":
            self.use(self.system_node)
        elif mode == "custom":
            self.use(self.custom.get().strip())
        else:
            def done(path, err):
                if err:
                    self.fail(err, "Node.js download failed")
                else:
                    self.use(path)
            self.task(lambda emit, progress: T.install_private_node(self.major, emit, progress), done)

    def validate(self):
        path = self.s.get("node_path")
        if not path or not T.node_compatible(T.node_major(path), self.s["foundry_gen"]):
            messagebox.showerror("Node.js needed", "Click 'Set up Node.js' first.")
            return False
        return True


class DataPage(Page):
    title = "Data folder"

    def build(self):
        h1(self, "Where should your worlds live?")
        self.intro = para(self)
        callout(self, "Don't use OneDrive, Dropbox, or Google Drive",
                "Sync services lock files while Foundry is writing to them. That causes lock errors "
                "and can corrupt your worlds. Keep this folder on a local drive and use the server "
                "manager's backups instead.", "warn")
        callout(self, "Already use Foundry on this PC?",
                "Point this at your current data folder, or your existing worlds won't show up. To "
                "check it: in Foundry's Setup screen, open the Configuration tab and look at User "
                "Data Path. In the desktop app, you can also right-click its taskbar icon and choose "
                "Browse User Data.", "info")
        labelled(self, "Data folder")
        self.var = tk.StringVar()
        path_row(self, self.var, "dir")
        self.detected = para(self, fg=C["muted"], size=9, pady=(0, 4))
        self.info = status_label(self)
        self.info.pack(fill="x")
        self.found = None
        self.initialised = False
        self.var.trace_add("write", lambda *_: self.update_info())

    def on_enter(self):
        self.intro.config(text="This is Foundry's User Data folder: your worlds, systems, modules, and "
                               "uploaded assets. It's separate from the Foundry program folder "
                               f"({self.s['foundry_dir']}) and can't be inside it, or Foundry won't start.")
        if not self.initialised:
            self.found, how = detect_foundry_data()
            if self.s.get("data_path"):
                path, note = self.s["data_path"], "Using the data folder from your previous setup."
            elif self.found:
                path = self.found
                note = ("Found your existing Foundry data using the User Data Path set in Foundry."
                        if how == "redirect" else "Found your existing Foundry data in Foundry's "
                                                  "default location.")
            else:
                path = FOUNDRY_DEFAULT_DATA
                note = "No existing Foundry data found, so this is Foundry's default location."
            self.detected.config(text=note)
            self.var.set(path)
            self.initialised = True
        self.update_info()

    def update_info(self):
        path = self.var.get().strip()
        if not path:
            set_status(self.info, "Choose a folder.", False)
            return
        if is_within(path, self.s["foundry_dir"]):
            set_status(self.info, "Can't be inside the Foundry program folder.", False)
            return
        sync = synced_folder_name(path)
        if sync:
            set_status(self.info, f"This looks like a {sync} folder. Pick a local folder instead.", None)
            return
        n = count_worlds(path)
        if n is not None:
            set_status(self.info, f"Existing Foundry data found ({n} world{'s' * (n != 1)}). "
                                  "It will be kept.", True)
        else:
            set_status(self.info, "A new, empty data folder will be created here.", True)

    def validate(self):
        raw = self.var.get().strip()
        if not raw:
            messagebox.showerror("Data folder", "Choose a data folder.")
            return False
        path = os.path.normpath(raw)
        if is_within(path, self.s["foundry_dir"]):
            messagebox.showerror("Data folder", "The data folder can't be inside the Foundry program "
                                                "folder. Foundry refuses to start that way.")
            return False
        sync = synced_folder_name(path)
        if sync and not messagebox.askyesno(
                "Synced folder", f"This folder looks like it's synced by {sync}. That's a common "
                                 "cause of lock errors and corrupted worlds.\n\nUse it anyway?",
                icon="warning"):
            return False
        if (self.found and os.path.normcase(path) != os.path.normcase(self.found)
                and count_worlds(path) is None and not messagebox.askyesno(
                    "Different data folder",
                    f"Your existing Foundry data is in:\n{self.found}\n\nThe folder you chose has no "
                    "worlds, so Foundry will start empty. Continue with the new folder?",
                    icon="warning")):
            return False
        try:
            for sub in ("Config", "Data", "Logs"):
                os.makedirs(os.path.join(path, sub), exist_ok=True)
        except OSError as e:
            messagebox.showerror("Data folder", f"Couldn't create the folder:\n{e}")
            return False
        self.s["data_path"] = path
        return True


class LicencePage(Page):
    title = "Licence"

    def build(self):
        h1(self, "Foundry licence key")
        para(self, "Enter your Foundry software licence key now, or skip this and enter it when Foundry "
                   "first opens. Your key is on foundryvtt.com under Purchased Licences, and in your "
                   "receipt email.")
        styled_button(self, "Open foundryvtt.com", lambda: webbrowser.open(URL_FOUNDRY),
                      "grey").pack(anchor="w", pady=(0, 8))
        self.mode = tk.StringVar(value="enter")
        self.keep_rb = radio(self, "Keep the licence that's already set up", self.mode, "keep", self.refresh)
        radio(self, "Enter my licence key now", self.mode, "enter", self.refresh)
        radio(self, "Skip, I'll enter it when Foundry first opens", self.mode, "skip", self.refresh)
        self.key_holder = tk.Frame(self, bg=C["bg"])
        self.key_holder.pack(fill="x", pady=(6, 0))
        self.key_frame = tk.Frame(self.key_holder, bg=C["bg"])
        labelled(self.key_frame, "Licence key")
        self.key = tk.StringVar()
        row = path_row(self.key_frame, self.key, show="•")
        self.key_entry = row.winfo_children()[0]
        self.show_key = tk.BooleanVar(value=False)
        tk.Checkbutton(self.key_frame, text="Show key", variable=self.show_key, command=self.toggle_show,
                       font=(FONT, 9), bg=C["bg"], fg=C["muted"], selectcolor=C["panel"],
                       activebackground=C["bg"], activeforeground=C["text"], highlightthickness=0,
                       bd=0).pack(anchor="w")
        callout(self, None, "Foundry still shows its licence agreement the first time it opens. Accept "
                            "it once and you're done. The key is stored where Foundry normally keeps it "
                            "(Config\\license.json in your data folder) and nowhere else.", "info")
        self.status = status_label(self)
        self.status.pack(fill="x")

    def toggle_show(self):
        self.key_entry.config(show="" if self.show_key.get() else "•")

    def existing(self):
        path = licence_path(self.s["data_path"])
        try:
            with open(path, encoding="utf-8") as f:
                return bool(json.load(f).get("license"))
        except (OSError, ValueError, AttributeError):
            return False

    def on_enter(self):
        if self.existing():
            self.keep_rb.config(state="normal")
            if not self.s.get("licence_choice"):
                self.mode.set("keep")
            set_status(self.status, "A licence is already set up in this data folder.", True)
        else:
            self.keep_rb.config(state="disabled")
            if self.mode.get() == "keep":
                self.mode.set("enter")
            set_status(self.status, "", True)
        self.refresh()

    def refresh(self):
        if self.mode.get() == "enter":
            self.key_frame.pack(fill="x")
        else:
            self.key_frame.pack_forget()

    def validate(self):
        mode = self.mode.get()
        self.s["licence_choice"] = mode
        if mode != "enter":
            return True
        key = re.sub(r"[^A-Za-z0-9]", "", self.key.get()).upper()
        if not key:
            messagebox.showerror("Licence key", "Paste your licence key, or choose Skip.")
            return False
        if len(key) != 24 and not messagebox.askyesno(
                "Licence key", "That doesn't look like a Foundry licence key (usually six groups of four "
                               "letters and numbers).\n\nUse it anyway?"):
            return False
        path = licence_path(self.s["data_path"])
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"license": key}, f)
        except OSError as e:
            messagebox.showerror("Licence key", f"Couldn't save the licence:\n{e}")
            return False
        self.key.set("")  # don't keep the key around in memory any longer than needed
        self.mode.set("keep")
        self.s["licence_choice"] = "entered"
        return True


class CloudflarePage(Page):
    title = "Cloudflare"

    def build(self):
        h1(self, "How will players connect?")
        callout(self, None, "Players connecting with your IP address? You don't need a tunnel. Choose "
                            "\"I'm not using Cloudflare\". Tunnels are for users with a custom web "
                            "address, e.g. www.my.campaign.com", "info")
        para(self, "Cloudflare Tunnel gives you an address like game.yourdomain.com with HTTPS, "
                   "without opening ports on your router.")
        prev = self.s.get("prev_config") or {}
        prev_mode = prev.get("cf_mode")
        had_tunnel = (prev.get("install_info") or {}).get("tunnel_service") and T.tunnel_service_exists()
        if prev_mode in ("manual", "api") and had_tunnel:
            default = "existing"
        else:
            default = prev_mode or "manual"
        self.mode = tk.StringVar(value=default)
        if prev_mode:
            callout(self, None, "Your previous choice is selected. If your tunnel is already working, "
                                "keep \"I already have a tunnel running\".", "ok")
        radio(self, "Set up a new tunnel (guided)", self.mode, "manual",
              desc="You create the tunnel in the Cloudflare dashboard and paste its token here. "
                   "A checklist at the end covers the rest.")
        radio(self, "Set up a new tunnel (automatic)", self.mode, "api",
              desc="Paste a Cloudflare API token and setup creates the tunnel, the route, and the "
                   "DNS record for you.")
        radio(self, "I already have a tunnel running", self.mode, "existing",
              desc="Skip tunnel setup. You'll just enter your address so Foundry knows about it.")
        radio(self, "I'm not using Cloudflare", self.mode, "none",
              desc="Players connect by IP address, or through a reverse proxy you manage yourself.")

    def validate(self):
        self.s["cf_mode"] = self.mode.get()
        return True


class DomainPage(Page):
    title = "Domain"

    GUIDE = ("1.  Log in at dash.cloudflare.com (or sign up, it's free).\n"
             "2.  Choose 'Add a domain' (sometimes 'Onboard a domain') and enter your domain.\n"
             "3.  Pick the Free plan and review the DNS records Cloudflare finds.\n"
             "4.  Cloudflare shows you two nameservers. Log in to wherever you bought the domain "
             "and replace its nameservers with those two.\n"
             "5.  Wait for the domain to show as Active in Cloudflare. It's often under an hour, "
             "but can take up to 24. Cloudflare emails you when it's done.\n\n"
             "You can carry on with setup while you wait.")
    UNSURE = ("Open the Cloudflare dashboard and look at your account home page.\n\n"
              "Listed and Active: it's set up, choose the first option.\n"
              "Listed but Pending: the nameservers haven't been changed yet (step 4 of the guide).\n"
              "Not listed: follow the guide to add it.")

    def applies(self):
        return self.s.get("cf_mode") in ("manual", "api")

    def build(self):
        h1(self, "Is your domain on Cloudflare?")
        para(self, "Tunnels only work with domains that use Cloudflare for DNS.")
        self.choice = tk.StringVar(value="yes")
        radio(self, "Yes, it's already set up", self.choice, "yes", self.refresh)
        radio(self, "No, show me how to add it", self.choice, "guide", self.refresh)
        radio(self, "Not sure", self.choice, "unsure", self.refresh)
        self.panel = tk.Frame(self, bg=C["panel"])
        self.panel_text = tk.Label(self.panel, font=(FONT, 10), bg=C["panel"], fg=C["text"],
                                   wraplength=WRAP - 30, justify="left", anchor="w")
        self.panel_text.pack(fill="x", padx=15, pady=(12, 8))
        styled_button(self.panel, "Open Cloudflare dashboard", lambda: webbrowser.open(URL_CF_DASH),
                      "grey").pack(anchor="w", padx=15, pady=(0, 12))
        self.refresh()

    def refresh(self):
        choice = self.choice.get()
        if choice == "yes":
            self.panel.pack_forget()
        else:
            self.panel_text.config(text=self.GUIDE if choice == "guide" else self.UNSURE)
            self.panel.pack(fill="x", pady=(10, 0))

    def validate(self):
        if self.choice.get() != "yes":
            return messagebox.askyesno(
                "Domain not confirmed",
                "Your web address won't work until the domain shows as Active in Cloudflare.\n\n"
                "Continue setup anyway?")
        return True


class AccessPage(Page):
    title = "Web address"

    def build(self):
        h1(self, "Web address and port")
        self.intro = para(self)
        labelled(self, "Address players will use")
        self.host = tk.StringVar()
        path_row(self, self.host)
        self.hint = para(self, fg=C["muted"], size=9)
        labelled(self, "Foundry port")
        self.port = tk.StringVar(value="30000")
        path_row(self, self.port)
        self.proxy = tk.BooleanVar(value=True)
        self.proxy_cb = checkbox(self, "Players connect over HTTPS through a proxy or tunnel", self.proxy)
        self.firewall = tk.BooleanVar(value=True)
        self.firewall_cb = checkbox(self, "Allow this port through Windows Firewall", self.firewall)

    def cf(self):
        return self.s.get("cf_mode") in CF_MODES

    def on_enter(self):
        if self.s.get("hostname"):
            self.host.set(self.s["hostname"])
        self.port.set(str(self.s.get("port", 30000)))
        if self.cf():
            self.intro.config(text="Enter the full address players will type, like game.example.com. "
                                   "It must be on a domain in your Cloudflare account.")
            hint = "Use the same address as your tunnel's Public Hostname." \
                if self.s["cf_mode"] == "existing" else "Setup points your tunnel at this address and port."
            self.hint.config(text=hint)
            self.proxy.set(True)
            self.proxy_cb.config(state="disabled")
            self.firewall_cb.pack_forget()
            self.firewall.set(False)
        else:
            self.intro.config(text="Leave the address blank if players connect with your IP address. "
                                   "If you run your own domain and reverse proxy, enter it here.")
            self.hint.config(text="Tick the HTTPS option only if a reverse proxy handles certificates "
                                  "in front of Foundry.")
            self.proxy.set(self.s.get("proxy_ssl", False))
            self.proxy_cb.config(state="normal")
            self.firewall.set(True)
            self.firewall_cb.pack(fill="x", pady=2)

    def validate(self):
        host = self.host.get().strip().lower()
        for prefix in ("https://", "http://"):
            if host.startswith(prefix):
                host = host[len(prefix):]
        host = host.split("/")[0]
        if self.cf() and not host:
            messagebox.showerror("Address needed", "Enter the address players will use.")
            return False
        if host and not T.HOST_RE.match(host):
            messagebox.showerror("Address", f"'{host}' doesn't look like a valid address.")
            return False
        try:
            port = int(self.port.get())
            assert 1024 <= port <= 65535
        except (ValueError, AssertionError):
            messagebox.showerror("Port", "Enter a port between 1024 and 65535 (30000 is Foundry's default).")
            return False
        if T.port_in_use(port) and not messagebox.askyesno(
                "Port in use", f"Something is already using port {port}. If it's an old Foundry "
                               "server, stop it before launching.\n\nContinue?"):
            return False
        proxy = self.proxy.get()
        try:
            T.write_options(self.s["data_path"], host, port, proxy)
        except OSError as e:
            messagebox.showerror("options.json", f"Couldn't write Foundry's options.json:\n{e}")
            return False
        if self.firewall.get():
            rule = f"Foundry VTT ({self.s['safe_name']})"
            if T.add_firewall_rule(rule, port):
                self.s["firewall_rule"] = rule
            else:
                messagebox.showwarning("Firewall", "Couldn't add the firewall rule. You may need to "
                                                   "allow Node.js through Windows Firewall manually.")
        if proxy and host:
            public = f"https://{host}"
        elif host:
            public = f"http://{host}:{port}"
        else:
            public = ""
        self.s.update(hostname=host, port=port, proxy_ssl=proxy, public_url=public)
        return True


class TunnelBase(Page):
    """Shared bits for the two tunnel pages."""

    def confirm_replace_service(self):
        if T.tunnel_service_exists():
            return messagebox.askyesno(
                "Existing tunnel service",
                "A Cloudflare Tunnel service is already installed on this PC. Replace it with this "
                "tunnel?\n\nWhatever tunnel it's running now will stop on this PC.")
        return True

    def installed(self, _result, err):
        if err:
            self.fail(err, "Tunnel setup failed")
            set_status(self.status, "Tunnel not connected.", False)
        else:
            self.s["tunnel_installed"] = True
            set_status(self.status, "Tunnel connected.", True)

    def validate(self):
        if self.s.get("tunnel_installed"):
            return True
        return messagebox.askyesno("Tunnel not set up", "The tunnel hasn't been installed yet. "
                                                        "Skip it for now?")


class TunnelManualPage(TunnelBase):
    title = "Tunnel"

    def applies(self):
        return self.s.get("cf_mode") == "manual"

    def build(self):
        h1(self, "Create your tunnel")
        para(self, "1.  In Cloudflare, open Zero Trust, then Networks > Tunnels. The first time, "
                   "Zero Trust asks for a team name and plan; the Free plan is fine. Menu names move "
                   "around occasionally, so search 'Tunnels' if you can't find it.\n"
                   "2.  Click Create a tunnel, choose Cloudflared, and name it.\n"
                   "3.  On the connector step, choose Windows. Copy the command shown (it contains "
                   "your token) and paste it below. You don't need to run it yourself.")
        styled_button(self, "Open Zero Trust dashboard", lambda: webbrowser.open(URL_CF_ZERO_TRUST),
                      "grey").pack(anchor="w", pady=(0, 8))
        labelled(self, "Paste the command or token")
        self.token = tk.StringVar()
        path_row(self, self.token)
        styled_button(self, "Install and connect", self.act, "green").pack(anchor="w")
        self.status = status_label(self)
        self.status.pack(fill="x", pady=(8, 0))
        section(self, "Then, back in Cloudflare")
        self.route = para(self)
        self.add_progress()
        self.log_widget = log_box(self, 4)

    def on_enter(self):
        self.route.config(text=f"Click Next in the dashboard to add a Public Hostname (route): "
                               f"{self.s['hostname']}, type HTTP, URL localhost:{self.s['port']}.")

    def act(self):
        token = T.extract_tunnel_token(self.token.get())
        if not token:
            messagebox.showerror("Token", "Couldn't find a tunnel token in that text. Copy the whole "
                                          "command from the Cloudflare connector page.")
            return
        if not self.confirm_replace_service():
            return

        def work(emit, progress):
            exe = T.ensure_cloudflared(emit, progress)
            T.install_tunnel_service(exe, token, emit)
        self.task(work, self.installed)


class TunnelApiPage(TunnelBase):
    title = "Tunnel"

    def applies(self):
        return self.s.get("cf_mode") == "api"

    def build(self):
        h1(self, "Automatic tunnel setup")
        para(self, "1.  In Cloudflare, go to My Profile > API Tokens > Create Token > Create Custom Token.\n"
                   "2.  Add these permissions:  Account / Cloudflare Tunnel / Edit,  Zone / DNS / Edit,  "
                   "Zone / Zone / Read.\n"
                   "3.  Under Zone Resources, include your domain.\n"
                   "4.  Create the token and paste it below. You can delete it after setup; the tunnel "
                   "doesn't need it.")
        styled_button(self, "Open API Tokens page", lambda: webbrowser.open(URL_CF_TOKENS),
                      "grey").pack(anchor="w", pady=(0, 8))
        labelled(self, "API token")
        self.token = tk.StringVar()
        path_row(self, self.token, show="•")
        labelled(self, "Tunnel name")
        self.name = tk.StringVar()
        path_row(self, self.name)
        self.target = para(self, fg=C["muted"], size=9)
        styled_button(self, "Create tunnel and connect", self.act, "green").pack(anchor="w")
        self.status = status_label(self)
        self.status.pack(fill="x", pady=(8, 0))
        self.add_progress()
        self.log_widget = log_box(self, 5)

    def on_enter(self):
        if not self.name.get():
            self.name.set(self.s["safe_name"].replace(" ", "-").lower())
        self.target.config(text=f"Will route {self.s['hostname']} to localhost:{self.s['port']}.")

    def act(self):
        token = self.token.get().strip()
        name = self.name.get().strip()
        if not token or not name:
            messagebox.showerror("Missing details", "Paste your API token and give the tunnel a name.")
            return
        if not self.confirm_replace_service():
            return
        host, port = self.s["hostname"], self.s["port"]

        def work(emit, progress):
            run_token = T.cloudflare_auto_setup(token, host, port, name, emit)
            exe = T.ensure_cloudflared(emit, progress)
            T.install_tunnel_service(exe, run_token, emit)
        self.task(work, self.installed)


class BackupPage(Page):
    title = "Backups"

    def build(self):
        h1(self, "Backups")
        para(self, "Backups are compressed copies of your whole data folder. The server needs to be "
                   "stopped while one runs.")
        labelled(self, "Save backups to")
        self.folder = tk.StringVar(value=self.s.get("backup_dir") or DEFAULT_BACKUP_DIR)
        path_row(self, self.folder, "dir")
        self.found = find_compressors()
        tools = [COMPRESSOR_LABELS[k].split(" (")[0] for k in ("winrar", "7zip") if k in self.found]
        para(self, ("Found: " + ", ".join(tools)) if tools else
             "WinRAR and 7-Zip aren't installed, so backups will use built-in ZIP.", fg=C["muted"])
        labelled(self, "Compression")
        self.keys = ["auto"] + [k for k in ("winrar", "7zip") if k in self.found] + ["zip"]
        self.tool = tk.StringVar(value=COMPRESSOR_LABELS["auto"])
        ttk.Combobox(self, textvariable=self.tool, state="readonly", width=30,
                     values=[COMPRESSOR_LABELS[k] for k in self.keys]).pack(anchor="w", pady=(0, 8))
        labelled(self, "Backups to keep")
        self.keep = tk.Spinbox(self, from_=1, to=50, width=5, font=(FONT, 10), bg=C["panel"],
                               fg=C["text"], buttonbackground=C["panel2"], relief="flat",
                               insertbackground=C["text"])
        self.keep.delete(0, "end")
        self.keep.insert(0, str(self.s.get("backup_keep", 3)))
        self.keep.pack(anchor="w")

    def validate(self):
        folder = self.folder.get().strip()
        if not folder:
            messagebox.showerror("Backups", "Choose a backup folder.")
            return False
        folder = os.path.normpath(folder)
        if is_within(folder, self.s["data_path"]):
            messagebox.showerror("Backups", "The backup folder can't be inside the data folder, or "
                                            "each backup would include the previous ones.")
            return False
        try:
            keep = max(1, min(50, int(self.keep.get())))
            os.makedirs(folder, exist_ok=True)
        except ValueError:
            messagebox.showerror("Backups", "Enter how many backups to keep.")
            return False
        except OSError as e:
            messagebox.showerror("Backups", f"Couldn't create the folder:\n{e}")
            return False
        label_to_key = {COMPRESSOR_LABELS[k]: k for k in self.keys}
        self.s.update(backup_dir=folder, backup_keep=keep,
                      compressor=label_to_key.get(self.tool.get(), "auto"))
        return True


class ManagerPage(Page):
    title = "Server manager"

    def build(self):
        h1(self, "Server manager")
        para(self, "The manager starts and stops Foundry, keeps the PC awake while the server runs, "
                   "shows the log, and handles backups.")
        para(self, f"Installs to: {APP_HOME}", fg=C["muted"], size=9)
        prev = self.s.get("prev_config") or {}
        self.login = tk.BooleanVar(value=prev.get("login_task", True))
        self.auto = tk.BooleanVar(value=prev.get("auto_start_server", False))
        self.desktop = tk.BooleanVar(value=prev.get("desktop_shortcut", True))
        self.startmenu = tk.BooleanVar(value=prev.get("startmenu_shortcut", True))
        checkbox(self, "Open the manager when I log in to Windows", self.login)
        checkbox(self, "Start the server automatically when the manager opens", self.auto)
        checkbox(self, "Desktop shortcut", self.desktop)
        checkbox(self, "Start menu shortcut", self.startmenu)
        styled_button(self, "Install server manager", self.act, "green").pack(anchor="w", pady=(10, 0))
        self.status = status_label(self)
        self.status.pack(fill="x", pady=(8, 0))
        self.log_widget = log_box(self, 6)
        self.advance_after = False

    def act(self, advance=False):
        s = self.s
        options = dict(login=self.login.get(), auto=self.auto.get(),
                       desktop=self.desktop.get(), startmenu=self.startmenu.get())
        title = f"{s['safe_name']} Server Manager"
        prev_info = (s.get("prev_config") or {}).get("install_info") or {}

        def work(emit, progress):
            emit("Copying the server manager...")
            target, args = T.install_manager_files()
            icon = ""
            image_mode = s.get("icon_mode") == "image"
            if image_mode and not os.path.isfile(s.get("icon_source", "")) and os.path.isfile(ICON_PATH):
                icon = ICON_PATH
                emit("Keeping your current app icon.", "info")
            elif T.pillow_available():
                if image_mode:
                    img = T.load_icon_image(s["icon_source"])
                else:
                    img = T.render_symbol(s.get("symbol", "⚔"))
                icon = T.save_icon(img, ICON_PATH)
                emit("App icon created.", "info")
            else:
                emit("Pillow isn't installed, so the default icon will be used.", "warn")
            save_config({
                "server_name": s["server_name"],
                "symbol": s.get("symbol", "⚔"),
                "icon_path": icon,
                "icon_mode": s.get("icon_mode", "symbol"),
                "icon_source": s.get("icon_source", ""),
                "cf_mode": s.get("cf_mode", "none"),
                "login_task": options["login"],
                "desktop_shortcut": options["desktop"],
                "startmenu_shortcut": options["startmenu"],
                "last_world": (s.get("prev_config") or {}).get("last_world"),
                "node_path": s["node_path"],
                "foundry_path": s["foundry_dir"],
                "data_path": s["data_path"],
                "port": s["port"],
                "public_url": s.get("public_url", ""),
                "backup_dir": s["backup_dir"],
                "backup_keep": s["backup_keep"],
                "compressor": s["compressor"],
                "auto_start_server": options["auto"],
                "install_info": {
                    "task_name": title,
                    "shortcut_name": title,
                    "firewall_rule": s.get("firewall_rule") or prev_info.get("firewall_rule", ""),
                    "tunnel_service": bool(s.get("tunnel_installed") or prev_info.get("tunnel_service")),
                },
            })
            emit("Saved configuration.", "info")
            old = prev_info.get("task_name")
            if old and old != title:  # server was renamed: clear the old task and shortcuts
                T.remove_logon_task(old)
                for kind in ("Desktop", "Programs"):
                    T.remove_shortcut(kind, prev_info.get("shortcut_name") or old)
            T.remove_logon_task(title)
            for kind, wanted in (("Desktop", options["desktop"]), ("Programs", options["startmenu"])):
                if not wanted:
                    T.remove_shortcut(kind, title)
            if options["login"]:
                T.create_logon_task(title, target, args, APP_HOME)
                emit("Startup task created.", "info")
            if options["desktop"]:
                T.create_shortcut("Desktop", title, target, args, APP_HOME, icon)
                emit("Desktop shortcut created.", "info")
            if options["startmenu"]:
                T.create_shortcut("Programs", title, target, args, APP_HOME, icon)
                emit("Start menu shortcut created.", "info")
            T.register_uninstall(f"{s['server_name']} Server Manager (Foundry VTT)",
                                 T.install_setup_copy(), icon)
            emit("Added to Windows Installed apps (for uninstalling).", "info")
            return target, args

        def done(result, err):
            if err:
                self.fail(err, "Install failed")
                set_status(self.status, "Server manager not installed.", False)
                return
            s["manager_target"], s["manager_args"] = result
            s["manager_installed"] = True
            set_status(self.status, "Server manager installed.", True)
            if advance:
                self.wiz.advance()
        self.task(work, done)

    def validate(self):
        if self.s.get("manager_installed"):
            return True
        self.act(advance=True)
        return False


class FinishPage(Page):
    title = "Finish"

    def build(self):
        h1(self, "Almost done")
        self.summary = para(self, fg=C["muted"])
        self.admin_callout = callout(
            self, "Set an Administrator Password before sharing your address",
            "Without one, anyone who reaches your server can open Foundry's Setup screen, change "
            "settings, and delete worlds. In Foundry: Setup screen > Configuration > Administrator "
            "Password.", "danger")
        section(self, "Test launch")
        para(self, "Starts Foundry briefly to check everything works, then stops it.", size=9,
             fg=C["muted"])
        row = tk.Frame(self, bg=C["bg"])
        row.pack(fill="x")
        styled_button(row, "Run test launch", self.test, "green").pack(side="left")
        self.status = status_label(self)
        self.status.pack(fill="x", pady=(6, 0))
        self.log_widget = log_box(self, 3)
        section(self, "Still to do")
        self.checklist = scrolledtext.ScrolledText(self, height=6, bg=C["panel"], fg=C["text"],
                                                   font=(FONT, 9), relief="flat", bd=0, wrap="word")
        self.checklist.pack(fill="both", expand=True)
        buttons = tk.Frame(self, bg=C["bg"])
        buttons.pack(fill="x", pady=(8, 0))
        styled_button(buttons, "Copy checklist", self.copy, "grey").pack(side="left")
        styled_button(buttons, "Open server manager", self.launch, "accent").pack(side="left", padx=8)
        styled_button(buttons, "Open data folder", lambda: open_folder(self.s["data_path"]),
                      "grey").pack(side="left")

    def items(self):
        s = self.s
        mode, host, port = s.get("cf_mode"), s.get("hostname"), s["port"]
        local = f"http://localhost:{port}"
        if s.get("licence_choice") == "skip":
            first = f"Open {local} on this PC, enter your Foundry licence key, and accept the licence agreement."
        else:
            first = f"Open {local} on this PC and accept Foundry's licence agreement (first launch only)."
        out = [first, "Set an Administrator Password (see the red box above)."]
        if mode == "manual":
            out.append(f"In your Cloudflare tunnel, add a Public Hostname: {host}, type HTTP, "
                       f"URL localhost:{port}. Then check the tunnel shows as Healthy.")
        if mode == "existing":
            out.append(f"Check your tunnel's Public Hostname for {host} points to http://localhost:{port}.")
        if mode == "api":
            out.append("Delete the Cloudflare API token if you like. The tunnel doesn't need it.")
        if mode in CF_MODES:
            out += ["In Cloudflare, check WebSockets is on (your domain > Network). Foundry needs it.",
                    "If Rocket Loader is on (your domain > Speed settings), turn it off. It can break "
                    "Foundry's scripts.",
                    f"Cloudflare's free plan caps uploads at 100 MB per file. Upload big maps or audio on "
                    f"this PC at {local} instead.",
                    "Only run the tunnel connector on this PC. A connector on another machine will send "
                    "players to the wrong computer (502 errors).",
                    f"Players join at https://{host}"]
        if mode == "none":
            out += [f"Forward TCP port {port} on your router to this PC, and give this PC a static local IP.",
                    f"Players join at {s['public_url'] or f'http://<your public IP>:{port}'}"]
        if T.has_battery():
            out.append("This is a laptop: set 'When I close the lid' to 'Do nothing' while plugged in, "
                       "or closing it will take the server offline.")
        return out

    def on_enter(self):
        s = self.s
        if has_admin_password(s["data_path"]):
            self.admin_callout.pack_forget()
        self.summary.config(text=f"{s['server_name']}  •  Foundry {s.get('foundry_label') or ''}  •  "
                                 f"Data: {s['data_path']}")
        self.checklist.config(state="normal")
        self.checklist.delete("1.0", "end")
        for i, item in enumerate(self.items(), 1):
            self.checklist.insert("end", f"{i}.  {item}\n\n")
        self.checklist.config(state="disabled")

    def copy(self):
        self.wiz.root.clipboard_clear()
        self.wiz.root.clipboard_append("\n".join(f"{i}. {x}" for i, x in enumerate(self.items(), 1)))

    def launch(self):
        target = self.s.get("manager_target")
        if target:
            T.launch_unelevated(target, self.s.get("manager_args", ""))

    def test(self):
        s = dict(self.s)
        if T.port_in_use(s["port"]):
            messagebox.showerror("Port in use", f"Port {s['port']} is busy. Stop any running Foundry "
                                                "server (including the manager) and try again.")
            return

        def work(emit, progress):
            args = [s["node_path"], "main.js", f"--dataPath={s['data_path']}", f"--port={s['port']}"]
            proc = subprocess.Popen(args, cwd=s["foundry_dir"], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            lines = queue.Queue()
            threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout], daemon=True).start()
            ready = False
            deadline = time.time() + 90
            try:
                while time.time() < deadline and proc.poll() is None:
                    try:
                        line = lines.get(timeout=1).rstrip()
                    except queue.Empty:
                        continue
                    emit(line)
                    if "listening on port" in line.lower():
                        ready = True
                        break
                if not ready:
                    raise T.SetupError("Foundry didn't start. Check the log above.")
                text, ok = "Foundry started locally.", True
                if s.get("cf_mode") in CF_MODES and s.get("hostname"):
                    emit(f"Checking https://{s['hostname']} ...")
                    code = self.probe(f"https://{s['hostname']}")
                    if code and code < 500:
                        text += f" {s['hostname']} is reachable (HTTP {code})."
                    elif code:
                        text += (f" {s['hostname']} returned HTTP {code}, so the tunnel route or DNS "
                                 "isn't finished yet (see checklist).")
                        ok = None
                    else:
                        text += f" {s['hostname']} isn't reachable yet (DNS may still be updating)."
                        ok = None
                return text, ok
            finally:
                T.kill_tree(proc.pid)

        def done(result, err):
            if err:
                self.fail(err, "Test launch failed")
                set_status(self.status, "Test launch failed.", False)
            else:
                set_status(self.status, *result)
        self.task(work, done)

    @staticmethod
    def probe(url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": T.USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except (urllib.error.URLError, OSError):
            return None


class UninstallView(tk.Frame):
    """Uninstall screen, used from the welcome page and from Windows Installed apps."""

    def __init__(self, parent, on_cancel, on_done):
        super().__init__(parent, bg=C["bg"])
        self.on_cancel, self.on_done = on_cancel, on_done
        self.cfg = load_config()
        self.info = self.cfg.get("install_info") or {}
        self.q = queue.Queue()
        self.busy = False
        self.finished = False

        cfg = self.cfg
        name = cfg.get("server_name") or "Foundry Server"
        self.safe = safe_name(name)
        self.data = cfg.get("data_path", "")
        self.foundry = cfg.get("foundry_path", "")
        self.backups = cfg.get("backup_dir", "")
        has_data = bool(self.data) and os.path.isdir(self.data)

        h1(self, f"Uninstall {name} Server Manager")
        para(self, "Always removed: the server manager and its settings, the startup task, shortcuts, "
                   "the private Node.js copy, the app icon, the firewall rule, and the Installed apps "
                   "entry.")
        callout(self, "Your backups are kept",
                "Uninstall never deletes backups. They stay in:\n"
                + (self.backups or "(no backup folder set)"), "ok")

        section(self, "Before uninstalling")
        self.do_backup = tk.BooleanVar(value=has_data and bool(self.backups))
        cb = checkbox(self, "Take a backup of my data first (recommended)", self.do_backup)
        if not (has_data and self.backups):
            cb.config(state="disabled")

        section(self, "Also remove")
        self.rm_tunnel = tk.BooleanVar(value=False)
        self.rm_foundry = tk.BooleanVar(value=False)
        self.rm_data = tk.BooleanVar(value=False)
        shown = 0
        if T.tunnel_service_exists():
            self.rm_tunnel.set(bool(self.info.get("tunnel_service")))
            checkbox(self, "Cloudflare Tunnel service on this PC", self.rm_tunnel)
            shown += 1
        if self.foundry and os.path.isfile(os.path.join(self.foundry, "main.js")):
            checkbox(self, f"Foundry program  ({self.foundry})", self.rm_foundry)
            shown += 1
        if has_data:
            cb = checkbox(self, f"My worlds and data  ({self.data})", self.rm_data)
            cb.config(fg=C["red"], activeforeground=C["red"])
            shown += 1
        if not shown:
            para(self, "Nothing else to remove.", fg=C["muted"])
        self.warn_holder = tk.Frame(self, bg=C["bg"])
        self.warn_holder.pack(fill="x", pady=(6, 0))
        self.data_warning = callout(self.warn_holder, "This can't be undone",
                                    "Deleting your data removes every world, module, system, and "
                                    "uploaded asset in the data folder. Only your backups will remain.",
                                    "danger", pack=False)
        self.rm_data.trace_add("write", lambda *_: self.toggle_warning())

        buttons = tk.Frame(self, bg=C["bg"])
        buttons.pack(fill="x", pady=(10, 0))
        self.cancel_btn = styled_button(buttons, "Cancel", self.cancel, "grey")
        self.cancel_btn.pack(side="left")
        self.go_btn = styled_button(buttons, "Uninstall", self.start, "red", 12)
        self.go_btn.pack(side="right")
        self.status = status_label(self)
        self.status.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(self, mode="indeterminate", style="Wizard.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(6, 0))
        self.log = log_box(self, 6)
        self.after(100, self.poll)

    def toggle_warning(self):
        if self.rm_data.get():
            self.data_warning.pack(fill="x")
        else:
            self.data_warning.pack_forget()

    def log_line(self, msg, tag="default"):
        self.log.config(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def cancel(self):
        if self.busy:
            return
        if self.finished:
            self.close_done()
        else:
            self.on_cancel()

    def close_done(self):
        T.schedule_app_home_removal()
        self.on_done()

    def start(self):
        if self.finished:
            self.close_done()
            return
        if T.manager_running():
            messagebox.showerror("Server manager open", "Stop the server and close the server manager "
                                                        "first.", parent=self)
            return
        if T.port_in_use(self.cfg.get("port", 30000)):
            messagebox.showerror("Foundry running", "Foundry still seems to be running. Stop it first.",
                                 parent=self)
            return
        items = ["Server manager, settings, startup task, shortcuts, app icon, and firewall rule"]
        if self.rm_tunnel.get():
            items.append("Cloudflare Tunnel service")
        if self.rm_foundry.get():
            items.append(f"Foundry program ({self.foundry})")
        if self.rm_data.get():
            items.append(f"ALL worlds and data ({self.data})")
        msg = ""
        if self.do_backup.get():
            msg += "A backup of your data will be taken first.\n\n"
        msg += "This will remove:\n\n" + "\n".join(f"•  {i}" for i in items)
        msg += f"\n\nBackups are not deleted. They stay in:\n{self.backups or '(no backup folder set)'}"
        if not messagebox.askyesno("Confirm uninstall", msg, icon="warning", default="no", parent=self):
            return
        if self.rm_data.get():
            extra = ("You're taking a backup first." if self.do_backup.get()
                     else "You haven't chosen to take a backup first.")
            if not messagebox.askyesno("Delete all worlds?",
                                       f"Permanently delete all worlds and data in:\n{self.data}\n\n"
                                       f"{extra}\n\nThis can't be undone.",
                                       icon="warning", default="no", parent=self):
                return
        opts = dict(backup=self.do_backup.get(), tunnel=self.rm_tunnel.get(),
                    foundry=self.rm_foundry.get(), data=self.rm_data.get())
        self.busy = True
        self.go_btn.config(state="disabled")
        self.cancel_btn.config(state="disabled")
        self.progress.start(12)
        threading.Thread(target=self.work, args=(opts,), daemon=True).start()

    def work(self, opts):
        def emit(msg, tag="default"):
            self.q.put(("log", msg, tag))
        try:
            cfg, info = self.cfg, self.info
            if opts["backup"]:
                make_backup(self.data, self.backups, cfg.get("compressor", "auto"), keep=None, emit=emit)
            task = info.get("task_name") or f"{self.safe} Server Manager"
            T.remove_logon_task(task)
            emit("Removed the startup task.")
            for kind in ("Desktop", "Programs"):
                T.remove_shortcut(kind, info.get("shortcut_name") or task)
            emit("Removed shortcuts.")
            T.remove_firewall_rule(info.get("firewall_rule") or f"Foundry VTT ({self.safe})")
            emit("Removed the firewall rule.")
            if opts["tunnel"]:
                T.uninstall_tunnel_service(emit)
            if opts["foundry"]:
                T.delete_folder(self.foundry, emit)
            if opts["data"]:
                T.delete_folder(self.data, emit)
            T.unregister_uninstall()
            emit("Removed from Installed apps.")
            T.clear_app_home(emit)
            self.q.put(("done", None))
        except Exception as e:  # shown to the user in poll()
            self.q.put(("done", e))

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "log":
                    self.log_line(item[1], item[2])
                else:
                    self.finish(item[1])
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def finish(self, err):
        self.busy = False
        self.progress.stop()
        if err:
            msg = str(err) if isinstance(err, (T.SetupError, RuntimeError)) else f"{type(err).__name__}: {err}"
            self.log_line(msg, "error")
            set_status(self.status, "Uninstall stopped. Nothing after the error was removed.", False)
            self.go_btn.config(state="normal")
            self.cancel_btn.config(state="normal")
            messagebox.showerror("Uninstall failed", msg, parent=self)
            return
        self.finished = True
        set_status(self.status, "Uninstall complete. Your backups are still in "
                                f"{self.backups or 'your backup folder'}.", True)
        self.go_btn.config(text="Close", state="normal")


PAGES = [WelcomePage, NamePage, FoundryPage, NodePage, DataPage, LicencePage, CloudflarePage, DomainPage,
         AccessPage, TunnelManualPage, TunnelApiPage, BackupPage, ManagerPage, FinishPage]


# ================================================================ wizard shell

class Wizard:
    def __init__(self, root):
        self.root = root
        root.title("Foundry Server Setup")
        root.geometry("920x720")
        root.minsize(860, 640)
        root.configure(bg=C["bg"])
        root.protocol("WM_DELETE_WINDOW", self.cancel)
        apply_style(root)

        self.state = {"server_name": "Foundry Server", "safe_name": "Foundry Server", "port": 30000,
                      "symbol": "⚔", "icon_mode": "symbol"}
        if os.path.isfile(CONFIG_PATH):
            self.prefill(load_config())
        self.busy = False
        self.q = queue.Queue()

        self.sidebar = tk.Frame(root, bg=C["panel"], width=210)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Label(self.sidebar, text="⚔ Setup", font=(FONT, 16, "bold"), bg=C["panel"],
                 fg=C["accent"]).pack(anchor="w", padx=20, pady=(24, 16))
        self.steps_frame = tk.Frame(self.sidebar, bg=C["panel"])
        self.steps_frame.pack(fill="x")

        main = tk.Frame(root, bg=C["bg"])
        main.pack(side="left", fill="both", expand=True)
        nav = tk.Frame(main, bg=C["bg"])
        nav.pack(side="bottom", fill="x", padx=30, pady=(0, 20))
        self.cancel_btn = styled_button(nav, "Cancel", self.cancel, "grey")
        self.cancel_btn.pack(side="left")
        self.next_btn = styled_button(nav, "Next  ›", self.next, "accent", 10)
        self.next_btn.pack(side="right")
        self.back_btn = styled_button(nav, "‹  Back", self.back, "grey", 10)
        self.back_btn.pack(side="right", padx=8)
        self.content = tk.Frame(main, bg=C["bg"])
        self.content.pack(fill="both", expand=True, padx=30, pady=(24, 12))

        self.pages = [cls(self) for cls in PAGES]
        self.index = 0
        self.show(0)
        root.after(100, self.poll)

    def prefill(self, prev):
        """Re-running setup: start from the current settings."""
        from urllib.parse import urlparse
        name = prev.get("server_name") or "Foundry Server"
        self.state.update(
            prev_config=prev, server_name=name, safe_name=safe_name(name),
            symbol=prev.get("symbol") or "⚔", port=prev.get("port", 30000),
            data_path=prev.get("data_path") or None, backup_dir=prev.get("backup_dir") or None,
            backup_keep=prev.get("backup_keep", 3),
            icon_mode=prev.get("icon_mode") or "symbol", icon_source=prev.get("icon_source") or "",
            hostname=urlparse(prev.get("public_url") or "").hostname or "")

    # ----- navigation

    def visible(self):
        return [p for p in self.pages if p.applies()]

    def show(self, index):
        for p in self.pages:
            p.pack_forget()
        self.index = index
        page = self.pages[index]
        page.pack(fill="both", expand=True)
        page.on_enter()
        self.refresh_chrome()

    def refresh_chrome(self):
        for w in self.steps_frame.winfo_children():
            w.destroy()
        current = self.pages[self.index]
        for i, p in enumerate(self.visible(), 1):
            active = p is current
            tk.Label(self.steps_frame, text=f"{i}.  {p.title}", font=(FONT, 10, "bold" if active else "normal"),
                     bg=C["accent"] if active else C["panel"], fg="white" if active else C["muted"],
                     anchor="w", padx=20, pady=6).pack(fill="x")
        last = current is self.visible()[-1]
        first = self.index == 0
        state = "disabled" if self.busy else "normal"
        self.next_btn.config(text="Finish" if last else "Next  ›", state=state)
        self.back_btn.config(state="disabled" if (self.busy or first) else "normal")
        self.cancel_btn.config(state=state)

    def next(self):
        if self.busy:
            return
        page = self.pages[self.index]
        if page is self.visible()[-1]:
            self.root.destroy()
            return
        if page.validate():
            self.advance()

    def advance(self):
        for j in range(self.index + 1, len(self.pages)):
            if self.pages[j].applies():
                self.show(j)
                return

    def back(self):
        for j in range(self.index - 1, -1, -1):
            if self.pages[j].applies():
                self.show(j)
                return

    def cancel(self):
        if self.busy:
            messagebox.showinfo("Please wait", "Setup is busy. Wait for the current step to finish.")
            return
        if messagebox.askyesno("Cancel setup", "Exit setup? Anything already installed stays installed."):
            self.root.destroy()

    # ----- background tasks

    def run_task(self, page, func, on_done):
        self.busy = True
        self.refresh_chrome()

        def emit(msg, tag="default"):
            self.q.put(("log", page, msg, tag))

        def progress(done, total):
            self.q.put(("progress", page, done, total))

        def worker():
            try:
                self.q.put(("done", page, on_done, func(emit, progress), None))
            except Exception as e:  # reported to the user by on_done
                self.q.put(("done", page, on_done, None, e))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "log":
                    item[1].log_line(item[2], item[3])
                elif item[0] == "progress":
                    item[1].set_progress(item[2], item[3])
                elif item[0] == "done":
                    _, page, on_done, result, err = item
                    self.busy = False
                    if page.progress:
                        page.progress.config(mode="determinate", value=0 if err else 100)
                    self.refresh_chrome()
                    if on_done:
                        on_done(result, err)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def run_uninstaller():
    root = tk.Tk()
    root.title("Uninstall Foundry Server Manager")
    root.geometry("720x760")
    root.configure(bg=C["bg"])
    apply_style(root)
    if not os.path.isfile(CONFIG_PATH):
        root.withdraw()
        if messagebox.askyesno("Not installed", "The server manager doesn't appear to be installed. "
                                                "Remove it from the Installed apps list?"):
            T.unregister_uninstall()
            T.schedule_app_home_removal()
        root.destroy()
        return
    if not T.is_admin():
        root.withdraw()
        if T.relaunch_as_admin():
            root.destroy()
            return
        messagebox.showerror("Administrator needed", "Uninstalling needs administrator rights.")
        root.destroy()
        return
    view = UninstallView(root, on_cancel=root.destroy, on_done=root.destroy)
    view.pack(fill="both", expand=True, padx=30, pady=24)
    root.protocol("WM_DELETE_WINDOW", view.cancel)
    root.mainloop()


def main():
    if os.name != "nt":
        print("Foundry Server Setup only runs on Windows.")
        return
    if "--uninstall" in sys.argv:
        run_uninstaller()
        return
    root = tk.Tk()
    Wizard(root)
    root.mainloop()


if __name__ == "__main__":
    main()
