"""Foundry Server Manager: start, stop, and back up a self-hosted Foundry VTT server."""
import ctypes
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from fsm_common import (APP_NAME, C, CONFIG_PATH, FONT, MONO, NO_WINDOW, apply_style,
                        has_admin_password, list_worlds, load_config, make_backup, open_folder,
                        read_options, run, save_config, set_auto_world, styled_button,
                        valid_symbol)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
READY_PATTERN = re.compile(r"listening on port", re.I)
NO_WORLD = "None (choose in Foundry)"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
MAX_LOG_LINES = 5000

STATES = {
    #  state:      (label,              colour,       start, stop,  backup)
    "stopped":    ("Status: Stopped",     C["red"],   True,  False, True),
    "starting":   ("Status: Starting...", C["amber"], False, True,  False),
    "running":    ("Status: Running ✓",   C["green"], False, True,  False),
    "stopping":   ("Status: Stopping...", C["amber"], False, False, False),
    "backing_up": ("Status: Backing up...", C["blue"], False, False, False),
}


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


class FoundryServerManager:
    def __init__(self, root, cfg, config_path=CONFIG_PATH):
        self.root = root
        self.cfg = cfg
        self.config_path = config_path
        self.proc = None
        self.state = "stopped"
        self.stop_requested = False
        self.close_after_stop = False
        self.q = queue.Queue()

        name = cfg.get("server_name") or "Foundry Server"
        symbol = cfg.get("symbol") or "⚔"
        if not valid_symbol(symbol):
            symbol = "⚔"
        root.title(f"{name} Server Manager")
        icon = cfg.get("icon_path")
        if icon and os.path.isfile(icon):
            try:
                root.iconbitmap(default=icon)  # also applies to popups
            except tk.TclError:
                pass
        root.geometry("820x720")
        root.minsize(700, 450)
        root.configure(bg=C["bg"])
        root.protocol("WM_DELETE_WINDOW", self.on_closing)

        header = tk.Frame(root, bg=C["panel"], height=90)
        header.pack(fill="x")
        tk.Label(header, text=f"{symbol} {name} Server Manager {symbol}",
                 font=(FONT, 22, "bold"), bg=C["panel"], fg=C["accent"]).pack(pady=20)

        self.status_var = tk.StringVar()
        self.status_label = tk.Label(root, textvariable=self.status_var,
                                     font=(FONT, 12, "bold"), bg=C["bg"])
        self.status_label.pack(pady=(12, 4))

        # Shown while no Administrator Password exists.
        self.banner_slot = tk.Frame(root, bg=C["bg"])
        self.banner_slot.pack(fill="x", padx=20)
        self.admin_banner = tk.Label(
            self.banner_slot, text="⚠  No Administrator Password is set. Anyone who reaches your server can open "
                       "Foundry's Setup screen.\nSet one in Foundry: Setup screen > Configuration > "
                       "Administrator Password.",
            font=(FONT, 9, "bold"), bg="#2c1517", fg=C["log_error"], justify="center", pady=8)

        world_row = tk.Frame(root, bg=C["bg"])
        world_row.pack(pady=(8, 0))
        tk.Label(world_row, text="World to launch:", font=(FONT, 10), bg=C["bg"],
                 fg=C["muted"]).pack(side="left", padx=(0, 8))
        self.world_var = tk.StringVar()
        self.world_box = ttk.Combobox(world_row, textvariable=self.world_var, state="readonly",
                                      width=38, font=(FONT, 10))
        self.world_box.pack(side="left")
        self.refresh_btn = styled_button(world_row, "⟳", self.load_worlds, "grey")
        self.refresh_btn.pack(side="left", padx=(6, 0))
        self.worlds = []

        bar = tk.Frame(root, bg=C["bg"])
        bar.pack(pady=10)
        self.start_btn = styled_button(bar, "▶  Start Server", self.start_server, "green", 16, big=True)
        self.stop_btn = styled_button(bar, "⏹  Stop Server", self.stop_server, "red", 16, big=True)
        self.backup_btn = styled_button(bar, "💾  Backups", self.backup_popup, "accent", 16, big=True)
        self.open_btn = styled_button(bar, "🌐  Open", self.open_local, "blue", 10, big=True)
        for i, b in enumerate((self.start_btn, self.stop_btn, self.backup_btn, self.open_btn)):
            b.grid(row=0, column=i, padx=6)
        if cfg.get("public_url"):
            public = tk.Button(root, text=f"Test public address  ({cfg['public_url']})",
                               command=self.open_public, font=(FONT, 9, "underline"),
                               bg=C["bg"], fg=C["blue"], activebackground=C["bg"],
                               activeforeground=C["text"], relief="flat", bd=0, cursor="hand2",
                               highlightthickness=0)
            public.pack()

        tk.Label(root, text="📋 Server Log", font=(FONT, 11, "bold"),
                 bg=C["bg"], fg=C["muted"]).pack(anchor="w", padx=25, pady=(12, 8))

        self.log_display = scrolledtext.ScrolledText(
            root, height=19, bg=C["panel"], fg=C["text"], font=(MONO, 9),
            insertbackground=C["accent"], relief="flat", bd=0,
            selectbackground=C["accent"], selectforeground="white", state="disabled")
        self.log_display.pack(padx=20, pady=(0, 20), fill="both", expand=True)
        for tag in ("error", "warn", "info", "backup", "default"):
            self.log_display.tag_config(tag, foreground=C[f"log_{tag}"])

        self.set_state("stopped")
        self.root.after(100, self.poll)

        if self.check_config():
            self.load_worlds()
            self.update_admin_banner()
            self.log(f"[info] Ready. Foundry: {cfg['foundry_path']}")
            self.log(f"[info] Data: {cfg['data_path']}")
            if cfg.get("auto_start_server"):
                self.root.after(800, self.start_server)

    # ---------- logging and state ----------

    def log(self, message):
        """Write to the log. UI thread only; worker threads use self.emit()."""
        message = ANSI.sub("", message)
        if not message.startswith("FoundryVTT |"):
            message = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {message}"
        low = message.lower()
        tag = "default"
        for key in ("error", "warn", "info", "backup"):
            if f"[{key}]" in low:
                tag = key
                break
        self.log_display.config(state="normal")
        self.log_display.insert("end", message + "\n", tag)
        lines = int(self.log_display.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_display.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log_display.see("end")
        self.log_display.config(state="disabled")

    def emit(self, message):
        self.q.put(("log", message))

    def poll(self):
        try:
            while True:
                kind, value = self.q.get_nowait()
                if kind == "log":
                    self.log(value)
                elif kind == "ready":
                    self.on_ready()
                elif kind == "exited":
                    self.on_exited(value)
                elif kind == "backup_done":
                    self.set_state("stopped")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def set_state(self, state):
        self.state = state
        label, colour, start, stop, backup = STATES[state]
        self.status_var.set(label)
        self.status_label.config(fg=colour)
        self.start_btn.config(state="normal" if start else "disabled")
        self.stop_btn.config(state="normal" if stop else "disabled")
        self.backup_btn.config(state="normal" if backup else "disabled")
        self.world_box.config(state="readonly" if state == "stopped" else "disabled")
        self.refresh_btn.config(state="normal" if state == "stopped" else "disabled")

    def check_config(self):
        problems = []
        node = self.cfg.get("node_path", "")
        main_js = os.path.join(self.cfg.get("foundry_path", ""), "main.js")
        data = self.cfg.get("data_path", "")
        if not os.path.isfile(node):
            problems.append(f"Node.js not found: {node or '(not set)'}")
        if not os.path.isfile(main_js):
            problems.append(f"Foundry main.js not found: {main_js}")
        if not data or not os.path.isdir(data):
            problems.append(f"Data folder not found: {data or '(not set)'}")
        for p in problems:
            self.log(f"[error] {p}")
        if problems:
            self.log("[error] Run Foundry Server Setup again to fix the configuration.")
        return not problems

    # ---------- worlds and admin password ----------

    def load_worlds(self):
        """Rescan the data folder and restore the last choice where possible."""
        data = self.cfg.get("data_path", "")
        self.worlds = list_worlds(data) if data else []
        titles = [NO_WORLD] + [title for _id, title in self.worlds]
        self.world_box.config(values=titles)
        preferred = self.cfg.get("last_world")
        if preferred is None:
            preferred = read_options(data).get("world") if data else None
        match = next((title for wid, title in self.worlds if wid == preferred), None)
        self.world_var.set(match or NO_WORLD)

    def selected_world(self):
        title = self.world_var.get()
        return next((wid for wid, t in self.worlds if t == title), None)

    def update_admin_banner(self):
        data = self.cfg.get("data_path", "")
        if data and os.path.isdir(data) and not has_admin_password(data):
            self.admin_banner.pack(fill="x")
        else:
            self.admin_banner.pack_forget()

    # ---------- power ----------

    def keep_awake(self, on):
        try:
            flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            self.log("[info] Sleep blocked while the server runs." if on
                     else "[info] Normal sleep settings restored.")
        except (AttributeError, OSError) as e:
            self.log(f"[warn] Couldn't change sleep behaviour: {e}")

    # ---------- lock files ----------

    def clear_lock_files(self):
        data = self.cfg["data_path"]
        targets = [os.path.join(data, "Config"), os.path.join(data, "Data", "worlds")]
        for target in targets:
            if not os.path.isdir(target):
                continue
            for folder, _dirs, files in os.walk(target):
                for f in files:
                    if f.endswith(".lock"):
                        path = os.path.join(folder, f)
                        try:
                            os.remove(path)
                            self.log(f"[info] Cleared lock file: {path}")
                        except OSError as e:
                            self.log(f"[warn] Couldn't delete {path}: {e}")

    # ---------- server ----------

    def start_server(self):
        if self.state != "stopped":
            return
        if not self.check_config():
            messagebox.showerror("Configuration problem",
                                 "Some paths are missing. See the log for details.")
            return
        port = int(self.cfg.get("port", 30000))
        if port_in_use(port):
            self.log(f"[error] Port {port} is already in use.")
            messagebox.showerror(
                "Port in use",
                f"Something is already using port {port}.\n\n"
                "Another Foundry server may already be running. Check Task Manager "
                "for node.exe or Foundry Virtual Tabletop.")
            return

        world = self.selected_world()
        try:
            set_auto_world(self.cfg["data_path"], world)
            self.log(f"[info] World to launch: {self.world_var.get()}")
        except OSError as e:
            self.log(f"[warn] Couldn't set the launch world in options.json: {e}")
        if self.cfg.get("last_world") != world:
            self.cfg["last_world"] = world
            try:
                save_config(self.cfg, self.config_path)
            except OSError:
                pass

        self.clear_lock_files()
        self.keep_awake(True)
        args = [self.cfg["node_path"], "main.js",
                f"--dataPath={self.cfg['data_path']}", f"--port={port}"]
        self.log("[info] Starting Foundry...")
        try:
            self.proc = subprocess.Popen(
                args, cwd=self.cfg["foundry_path"], stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW)
        except OSError as e:
            self.log(f"[error] Couldn't start Foundry: {e}")
            self.keep_awake(False)
            return
        self.stop_requested = False
        self.set_state("starting")
        threading.Thread(target=self._read_output, args=(self.proc,), daemon=True).start()

    def _read_output(self, proc):
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.q.put(("log", line))
                if READY_PATTERN.search(line):
                    self.q.put(("ready", None))
        self.q.put(("exited", proc.wait()))

    def on_ready(self):
        if self.state == "starting":
            self.set_state("running")
            self.log(f"[info] Server ready at {self.local_address()}")
            if self.cfg.get("public_url"):
                self.log(f"[info] Players join at {self.cfg['public_url']}")

    def on_exited(self, code):
        self.proc = None
        self.keep_awake(False)
        self.clear_lock_files()
        if self.stop_requested:
            self.log("[info] Server stopped.")
        elif code == 0:
            self.log("[warn] Foundry shut itself down. This is normal after saving changes in its "
                     "Configuration tab. Click Start to bring it back.")
        else:
            self.log(f"[error] Foundry exited unexpectedly (exit code {code}). "
                     "Check the log above for the cause.")
        self.set_state("stopped")
        self.update_admin_banner()
        self.load_worlds()
        if self.close_after_stop:
            self.root.destroy()

    def stop_server(self, confirm=True):
        if self.state not in ("starting", "running") or not self.proc:
            return
        if confirm and not messagebox.askyesno("Confirm stop", "Stop the Foundry server?"):
            return
        self.stop_requested = True
        self.set_state("stopping")
        self.log("[info] Stopping server...")
        pid = self.proc.pid
        # Kill only Foundry's process tree, never every node.exe on the machine.
        threading.Thread(target=lambda: run(["taskkill", "/PID", str(pid), "/T", "/F"]),
                         daemon=True).start()

    def local_address(self):
        return f"http://localhost:{self.cfg.get('port', 30000)}"

    def open_local(self):
        webbrowser.open(self.local_address())

    def open_public(self):
        webbrowser.open(self.cfg["public_url"])

    # ---------- backups ----------

    def backup_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Backups and Data")
        popup.geometry("420x320")
        popup.configure(bg=C["panel"])
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()

        tk.Label(popup, text="Backup Management", font=(FONT, 14, "bold"),
                 bg=C["panel"], fg=C["accent"]).pack(pady=(20, 4))
        keep = self.cfg.get("backup_keep", 3)
        tk.Label(popup, text=f"Keeps the {keep} most recent backups", font=(FONT, 9),
                 bg=C["panel"], fg=C["muted"]).pack()
        frame = tk.Frame(popup, bg=C["panel"])
        frame.pack(pady=12)
        styled_button(frame, "📁  Open Backups Folder",
                      lambda: self.open_backup_folder(popup), "blue", 22).pack(pady=6)
        styled_button(frame, "💾  Create Backup Now",
                      lambda: self.create_backup(popup), "accent", 22).pack(pady=6)
        styled_button(frame, "📂  Open Data Folder",
                      lambda: self.open_data_folder(popup), "grey", 22).pack(pady=6)
        tk.Label(popup, text="⚠  Backups include your Foundry licence key.\nDon't share them publicly.",
                 font=(FONT, 9), bg=C["panel"], fg=C["amber"], justify="center").pack(pady=(0, 10))

    def open_backup_folder(self, popup):
        open_folder(self.cfg.get("backup_dir"))
        popup.destroy()

    def open_data_folder(self, popup):
        open_folder(self.cfg.get("data_path"))
        popup.destroy()

    def create_backup(self, popup):
        popup.destroy()
        if self.state != "stopped":
            messagebox.showwarning("Backup", "Stop the server before backing up.")
            return
        self.set_state("backing_up")
        threading.Thread(target=self._do_backup, daemon=True).start()

    def _do_backup(self):
        ok = False
        try:
            make_backup(self.cfg["data_path"], self.cfg["backup_dir"],
                        self.cfg.get("compressor", "auto"), self.cfg.get("backup_keep", 3), self.emit)
            ok = True
        except Exception as e:  # keep the UI alive whatever happens
            self.emit(f"[error] Backup failed: {e}")
        self.q.put(("backup_done", ok))

    # ---------- closing ----------

    def on_closing(self):
        if self.state in ("starting", "running"):
            if messagebox.askokcancel("Server running",
                                      "The server is still running.\n\nStop it and close the manager?"):
                self.close_after_stop = True
                self.stop_server(confirm=False)
        elif self.state == "stopping":
            self.close_after_stop = True
        elif self.state == "backing_up":
            messagebox.showinfo("Backup running", "Wait for the backup to finish before closing.")
        else:
            self.root.destroy()


def main():
    if os.name != "nt":
        print("Foundry Server Manager only runs on Windows.")
        return
    # One manager at a time, otherwise two could fight over the same server.
    kernel32 = ctypes.windll.kernel32
    _mutex = kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        tk.Tk().withdraw()
        messagebox.showinfo("Already open", "Foundry Server Manager is already running.")
        return

    config_path = sys.argv[1] if len(sys.argv) > 1 else CONFIG_PATH
    if not os.path.isfile(config_path):
        tk.Tk().withdraw()
        messagebox.showerror("Not set up",
                             f"No configuration found at:\n{config_path}\n\n"
                             "Run Foundry Server Setup first.")
        return

    root = tk.Tk()
    apply_style(root)
    FoundryServerManager(root, load_config(config_path), config_path)
    root.mainloop()


if __name__ == "__main__":
    main()
