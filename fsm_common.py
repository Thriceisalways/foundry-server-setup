"""Shared helpers for Foundry Server Manager and Foundry Server Setup."""
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime
import tkinter as tk
from tkinter import ttk

APP_NAME = "FoundryServerManager"
MANAGER_EXE_NAME = "FoundryServerManager.exe"

LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(
    os.path.expanduser("~"), "AppData", "Local")
APP_HOME = os.path.join(LOCALAPPDATA, APP_NAME)
CONFIG_PATH = os.path.join(APP_HOME, "config.json")
RUNTIME_DIR = os.path.join(APP_HOME, "runtime")
ICON_PATH = os.path.join(APP_HOME, "icon.ico")
# Foundry's own default User Data location on Windows.
FOUNDRY_DEFAULT_DATA = os.path.join(LOCALAPPDATA, "FoundryVTT")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FONT = "Segoe UI"
MONO = "Consolas"

C = {
    "bg": "#0f1419",
    "panel": "#1a1f2e",
    "panel2": "#232a3b",
    "border": "#2d3548",
    "accent": "#7c3aed",
    "accent_dark": "#6d28d9",
    "green": "#10b981",
    "green_dark": "#059669",
    "red": "#ef4444",
    "red_dark": "#dc2626",
    "amber": "#f59e0b",
    "blue": "#3b82f6",
    "blue_dark": "#1d4ed8",
    "text": "#e5e7eb",
    "muted": "#9ca3af",
    "disabled": "#6b7280",
    "log_error": "#fca5a5",
    "log_warn": "#fbbf24",
    "log_info": "#86efac",
    "log_backup": "#a5f3fc",
    "log_default": "#d1d5db",
}

DEFAULT_CONFIG = {
    "server_name": "Foundry Server",
    "symbol": "⚔",
    "icon_path": "",
    "node_path": "",
    "foundry_path": "",
    "data_path": "",
    "port": 30000,
    "public_url": "",
    "backup_dir": "",
    "backup_keep": 3,
    "compressor": "auto",
    "auto_start_server": False,
}

COMPRESSOR_PATHS = {
    "winrar": [r"C:\Program Files\WinRAR\rar.exe",
               r"C:\Program Files (x86)\WinRAR\rar.exe"],
    "7zip": [r"C:\Program Files\7-Zip\7z.exe",
             r"C:\Program Files (x86)\7-Zip\7z.exe"],
}
COMPRESSOR_LABELS = {
    "auto": "Automatic (best available)",
    "winrar": "WinRAR (.rar)",
    "7zip": "7-Zip (.7z)",
    "zip": "ZIP (built in)",
}

SYNC_MARKERS = {
    "onedrive": "OneDrive",
    "dropbox": "Dropbox",
    "google drive": "Google Drive",
    "googledrive": "Google Drive",
    "my drive": "Google Drive",
    "icloud": "iCloud",
    "box sync": "Box",
    "sync.com": "Sync.com",
}


# Symbols from the Basic Multilingual Plane only: Tk can't draw characters above U+FFFF.
SYMBOL_PRESETS = ["⚔", "⚓", "☠", "♛", "☽", "★", "⚜", "♜", "♞", "⚡", "⚗", "✠", "☘", "✦", "⚖", "☀"]
SYMBOL_FONTS = ["seguisym.ttf", "seguiemj.ttf", "segoeui.ttf"]


def valid_symbol(text):
    return 0 < len(text) <= 2 and all(ord(ch) <= 0xFFFF for ch in text)


def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def run(args, **kwargs):
    """subprocess.run with no console window and captured text output."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("errors", "replace")
    kwargs.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(args, **kwargs)


def safe_name(text):
    """Strip characters Windows won't allow in file or task names."""
    cleaned = re.sub(r'[\\/:*?"<>|]', "", text).strip().rstrip(".")
    return cleaned or "Foundry Server"


def is_within(path, parent):
    try:
        path = os.path.normcase(os.path.abspath(path))
        parent = os.path.normcase(os.path.abspath(parent))
        return os.path.commonpath([path, parent]) == parent
    except ValueError:  # different drives
        return False


def synced_folder_name(path):
    """Return the sync service name if path looks like a synced folder."""
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(var)
        if root and is_within(path, root):
            return "OneDrive"
    low = os.path.abspath(path).lower()
    for marker, name in SYNC_MARKERS.items():
        if marker in low:
            return name
    return None


def find_compressors():
    """Return {key: exe_path} for installed compressors. ZIP is always available."""
    found = {}
    for key, paths in COMPRESSOR_PATHS.items():
        for p in paths:
            if os.path.isfile(p):
                found[key] = p
                break
    found["zip"] = None
    return found


def styled_button(parent, text, command, colour="accent", width=None, big=False):
    colours = {
        "accent": (C["accent"], C["accent_dark"]),
        "green": (C["green"], C["green_dark"]),
        "red": (C["red"], C["red_dark"]),
        "blue": (C["blue"], C["blue_dark"]),
        "grey": (C["panel2"], C["border"]),
    }
    bg, hover = colours[colour]
    btn = tk.Button(
        parent, text=text, command=command, bg=bg, fg="white",
        activebackground=hover, activeforeground="white",
        disabledforeground=C["disabled"],
        font=(FONT, 11 if big else 10, "bold"),
        relief="flat", bd=0, cursor="hand2", highlightthickness=0,
        padx=12 if big else 14, pady=11 if big else 7)
    if width:
        btn.config(width=width)
    return btn


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ---------------------------------------------------------------- data folders

def count_worlds(data_path):
    worlds = os.path.join(data_path, "Data", "worlds")
    if not os.path.isdir(worlds):
        return None
    return len([d for d in os.listdir(worlds) if os.path.isdir(os.path.join(worlds, d))])


def detect_foundry_data():
    """Find an existing Foundry User Data folder.

    Checks Foundry's default location, following a dataPath redirect in its
    options.json if there is one. Returns (path, how_found) or (None, None).
    """
    redirect = None
    options = os.path.join(FOUNDRY_DEFAULT_DATA, "Config", "options.json")
    try:
        with open(options, encoding="utf-8") as f:
            redirect = json.load(f).get("dataPath")
    except (OSError, ValueError, AttributeError):
        pass
    if redirect:
        redirect = os.path.normpath(redirect)
        if os.path.isdir(os.path.join(redirect, "Data")):
            return redirect, "redirect"
    if os.path.isdir(os.path.join(FOUNDRY_DEFAULT_DATA, "Data")):
        return FOUNDRY_DEFAULT_DATA, "default"
    return None, None


# ---------------------------------------------------------------- backups

def human_size(num):
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def _pick_compressor(pref, emit):
    found = find_compressors()
    if pref == "auto":
        for key in ("winrar", "7zip", "zip"):
            if key in found:
                return key, found[key]
    if pref in found:
        return pref, found[pref]
    emit(f"[warn] {pref} not found, using built-in ZIP instead.")
    return "zip", None


def _zip_folder(src, archive, emit):
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, _dirs, files in os.walk(src):
            for name in files:
                path = os.path.join(folder, name)
                try:
                    zf.write(path, os.path.relpath(path, src))
                except OSError as e:
                    emit(f"[warn] Skipped {path}: {e}")


def cleanup_old_backups(folder, keep, emit):
    keep = max(1, int(keep))
    backups = [os.path.join(folder, f) for f in os.listdir(folder)
               if f.startswith("backup_") and f.endswith((".rar", ".7z", ".zip"))]
    backups.sort(key=os.path.getmtime, reverse=True)
    for old in backups[keep:]:
        try:
            os.remove(old)
            emit(f"[backup] Deleted old backup: {os.path.basename(old)}")
        except OSError as e:
            emit(f"[warn] Couldn't delete {os.path.basename(old)}: {e}")


def make_backup(data_path, backup_dir, compressor="auto", keep=None, emit=print):
    """Compress the whole data folder. keep=None never deletes older backups.

    Returns the archive path. Raises RuntimeError on failure.
    """
    if not os.path.isdir(data_path):
        raise RuntimeError(f"Data folder not found: {data_path}")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = f"backup_{datetime.now():%Y%m%d_%H%M%S}"
    base, n = os.path.join(backup_dir, stamp), 2
    while any(os.path.exists(base + ext) for ext in (".rar", ".7z", ".zip")):
        base, n = os.path.join(backup_dir, f"{stamp}_{n}"), n + 1
    key, exe = _pick_compressor(compressor, emit)
    emit(f"[backup] Backing up {data_path} ...")
    if key == "winrar":
        archive = base + ".rar"
        r = run([exe, "a", "-r", "-ep1", "-idq", archive, os.path.join(data_path, "*")])
        ok = r.returncode in (0, 1)
    elif key == "7zip":
        archive = base + ".7z"
        r = run([exe, "a", "-bd", archive, os.path.join(data_path, "*")])
        ok = r.returncode in (0, 1)
    else:
        archive, r = base + ".zip", None
        _zip_folder(data_path, archive, emit)
        ok = True
    if not ok or not os.path.isfile(archive):
        detail = ((r.stderr or r.stdout).strip() if r else "") or "unknown error"
        raise RuntimeError(f"Compression failed: {detail}")
    emit(f"[backup] Backup complete: {os.path.basename(archive)} "
         f"({human_size(os.path.getsize(archive))})")
    if keep:
        cleanup_old_backups(backup_dir, keep, emit)
    return archive


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    subprocess.Popen(["explorer", os.path.normpath(path)])


# ---------------------------------------------------------------- UI style

def apply_style(root):
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure("Wizard.Horizontal.TProgressbar", background=C["accent"], troughcolor=C["panel"],
                 bordercolor=C["panel"], lightcolor=C["accent"], darkcolor=C["accent"])
    st.configure("TCombobox", fieldbackground=C["panel"], background=C["panel2"],
                 foreground=C["text"], arrowcolor=C["text"], bordercolor=C["border"],
                 selectbackground=C["panel"], selectforeground=C["text"])
    st.map("TCombobox", fieldbackground=[("readonly", C["panel"]), ("disabled", C["bg"])],
           foreground=[("readonly", C["text"]), ("disabled", C["disabled"])])
    root.option_add("*TCombobox*Listbox.background", C["panel"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])


# ---------------------------------------------------------------- Foundry files

def options_path(data_path):
    return os.path.join(data_path, "Config", "options.json")


def read_options(data_path):
    try:
        with open(options_path(data_path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def list_worlds(data_path):
    """[(world_id, title)] sorted by title, from each world's world.json."""
    root = os.path.join(data_path, "Data", "worlds")
    worlds = []
    if not os.path.isdir(root):
        return worlds
    for world_id in os.listdir(root):
        manifest = os.path.join(root, world_id, "world.json")
        if not os.path.isfile(manifest):
            continue
        try:
            with open(manifest, encoding="utf-8") as f:
                title = json.load(f).get("title") or world_id
        except (OSError, ValueError):
            title = world_id
        worlds.append((world_id, title))
    worlds.sort(key=lambda w: w[1].lower())
    return worlds


def set_auto_world(data_path, world_id):
    """Set (or clear, with None) the world Foundry launches on start."""
    path = options_path(data_path)
    options = read_options(data_path)
    options["world"] = world_id or None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(options, f, indent=2)


def has_admin_password(data_path):
    return os.path.isfile(os.path.join(data_path, "Config", "admin.txt"))


def licence_path(data_path):
    return os.path.join(data_path, "Config", "license.json")
