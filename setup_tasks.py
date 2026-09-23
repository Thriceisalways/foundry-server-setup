"""Setup operations used by the wizard. No UI code in here.

Worker functions take emit(message, tag) and progress(done, total) callbacks
so the wizard can run them on a background thread.
"""
import ctypes
import json
import os
import platform
import re
import shutil
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from fsm_common import (APP_HOME, APP_NAME, C, MANAGER_EXE_NAME, NO_WINDOW, RUNTIME_DIR,
                        SYMBOL_FONTS, is_within, resource_path, run)

USER_AGENT = "FoundryServerSetup/1.0"
NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
CLOUDFLARED_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
                   "cloudflared-windows-amd64.exe")
CLOUDFLARED_DIR = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "cloudflared")
CF_API = "https://api.cloudflare.com/client/v4"
SETUP_EXE_NAME = "FoundryServerSetup.exe"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\FoundryServerManager"

HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
TUNNEL_TOKEN_RE = re.compile(r"(eyJ[A-Za-z0-9_\-]+=*)")


class SetupError(Exception):
    """An error with a message that's safe to show the user as-is."""


# ---------------------------------------------------------------- system

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin():
    args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
    params = " ".join(f'"{a}"' for a in args)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return rc > 32


def windows_ok():
    try:
        return sys.getwindowsversion().major >= 10
    except AttributeError:
        return False


def has_battery():
    class PowerStatus(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    try:
        status = PowerStatus()
        ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
        return (status.BatteryFlag & 0xFF) not in (128, 255)
    except (AttributeError, OSError):
        return False


def internet_ok():
    try:
        req = urllib.request.Request(NODE_INDEX_URL, method="HEAD", headers={"User-Agent": USER_AGENT})
        urllib.request.urlopen(req, timeout=6).close()
        return True
    except (urllib.error.URLError, OSError):
        return False


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", int(port))) == 0


def kill_tree(pid):
    run(["taskkill", "/PID", str(pid), "/T", "/F"])


# ---------------------------------------------------------------- downloads

def download(url, dest, progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    part = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except urllib.error.HTTPError as e:
        if os.path.exists(part):
            os.remove(part)
        if e.code in (401, 403):
            raise SetupError("The download link was refused. Timed URLs expire after a few "
                             "minutes, so generate a fresh one and try again.")
        raise SetupError(f"Download failed (HTTP {e.code}).")
    except (urllib.error.URLError, OSError) as e:
        if os.path.exists(part):
            os.remove(part)
        raise SetupError(f"Download failed: {getattr(e, 'reason', e)}")
    os.replace(part, dest)


def safe_extract(zf, members, dest, strip_prefix="", progress=None):
    dest = os.path.abspath(dest)
    for i, info in enumerate(members, 1):
        rel = info.filename[len(strip_prefix):]
        if rel:
            target = os.path.abspath(os.path.join(dest, rel))
            if not is_within(target, dest):
                raise SetupError(f"Refusing unsafe path in zip: {info.filename}")
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
        if progress:
            progress(i, len(members))


# ---------------------------------------------------------------- Foundry

def find_main_js(folder):
    """Return the folder containing Foundry's main.js, or None."""
    for candidate in (folder, os.path.join(folder, "resources", "app")):
        if os.path.isfile(os.path.join(candidate, "main.js")):
            return candidate
    return None


def foundry_version(folder):
    """Return (generation, label) from Foundry's package.json, e.g. (14, 'V14 build 360')."""
    try:
        with open(os.path.join(folder, "package.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, None
    release = data.get("release") or {}
    gen, build = release.get("generation"), release.get("build")
    if gen is None:
        m = re.match(r"(\d+)(?:\.(\d+))?", str(data.get("version", "")))
        if m:
            gen, build = int(m.group(1)), build or m.group(2)
    if not gen:
        return None, None
    label = f"V{gen}" + (f" build {build}" if build else "")
    return int(gen), label


def extract_foundry_zip(zip_path, dest, emit, progress):
    if not zipfile.is_zipfile(zip_path):
        raise SetupError("That file isn't a valid zip. If it came from a timed URL, "
                         "the link may have expired.")
    with zipfile.ZipFile(zip_path) as zf:
        names = [n.replace("\\", "/") for n in zf.namelist()]
        if any(n.lower().endswith("foundry virtual tabletop.exe") for n in names):
            raise SetupError("This is the Windows desktop build. On foundryvtt.com, set "
                             "Operating System to 'Node.js' and download that instead.")
        mains = [n for n in names if n.split("/")[-1] == "main.js"]
        if not mains:
            raise SetupError("Couldn't find main.js in the zip. Make sure it's the "
                             "Node.js build of Foundry.")
        main = min(mains, key=lambda n: n.count("/"))
        prefix = main[: -len("main.js")]
        members = [i for i in zf.infolist() if i.filename.replace("\\", "/").startswith(prefix)]
        emit(f"Extracting {len(members)} files to {dest}")
        os.makedirs(dest, exist_ok=True)
        safe_extract(zf, members, dest, prefix, progress)


def install_foundry(source, dest, emit, progress):
    """source is a timed URL or a local zip path. Returns (folder, generation, label)."""
    tmp = None
    if source.lower().startswith(("http://", "https://")):
        tmp = os.path.join(tempfile.gettempdir(), "foundryvtt-node-build.zip")
        emit("Downloading Foundry VTT...")
        download(source, tmp, progress)
        zip_path = tmp
    else:
        zip_path = source
    try:
        extract_foundry_zip(zip_path, dest, emit, progress)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    folder = find_main_js(dest)
    if not folder:
        raise SetupError("Extraction finished but main.js is missing.")
    gen, label = foundry_version(folder)
    emit(f"Foundry installed: {label or 'version unknown'}", "info")
    return folder, gen, label


# ---------------------------------------------------------------- Node.js

def node_requirement(gen):
    """(minimum major, exclusive maximum major or None) for a Foundry generation."""
    if gen is None or gen >= 14:
        return 24, None
    return 22, 24


def node_compatible(major, gen):
    lo, hi = node_requirement(gen)
    return major is not None and major >= lo and (hi is None or major < hi)


def node_major(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        r = run([path, "--version"], timeout=15)
    except OSError:
        return None
    m = re.match(r"v(\d+)", r.stdout.strip())
    return int(m.group(1)) if m else None


def find_system_node():
    for p in (shutil.which("node"), r"C:\Program Files\nodejs\node.exe"):
        if p and os.path.isfile(p):
            return p
    return None


def find_private_node(major):
    if not os.path.isdir(RUNTIME_DIR):
        return None
    for name in sorted(os.listdir(RUNTIME_DIR), reverse=True):
        if name.startswith(f"node-v{major}."):
            exe = os.path.join(RUNTIME_DIR, name, "node.exe")
            if os.path.isfile(exe):
                return exe
    return None


def install_private_node(major, emit, progress):
    arch = "arm64" if platform.machine().upper() == "ARM64" else "x64"
    emit(f"Looking up the latest Node.js {major} release...")
    req = urllib.request.Request(NODE_INDEX_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            releases = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SetupError(f"Couldn't reach nodejs.org: {e}")
    release = next((r for r in releases if r["version"].startswith(f"v{major}.")
                    and f"win-{arch}-zip" in r.get("files", [])), None)
    if not release:
        raise SetupError(f"No Windows {arch} build of Node.js {major} found on nodejs.org.")

    version = release["version"]
    name = f"node-{version}-win-{arch}"
    exe = os.path.join(RUNTIME_DIR, name, "node.exe")
    if os.path.isfile(exe):
        emit(f"Node.js {version} is already downloaded.", "info")
        return exe

    tmp = os.path.join(tempfile.gettempdir(), f"{name}.zip")
    emit(f"Downloading Node.js {version}...")
    download(f"https://nodejs.org/dist/{version}/{name}.zip", tmp, progress)
    emit("Extracting...")
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    try:
        with zipfile.ZipFile(tmp) as zf:
            safe_extract(zf, zf.infolist(), RUNTIME_DIR, "", progress)
    finally:
        os.remove(tmp)
    if not os.path.isfile(exe):
        raise SetupError("Node.js extracted but node.exe is missing.")
    emit(f"Node.js {version} ready.", "info")
    return exe


# ---------------------------------------------------------------- Foundry options

def write_options(data_path, hostname, port, proxy_ssl):
    config_dir = os.path.join(data_path, "Config")
    os.makedirs(config_dir, exist_ok=True)
    path = os.path.join(config_dir, "options.json")
    options = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                options = json.load(f)
        except (OSError, ValueError):
            shutil.copy2(path, path + ".bak")
    options.update({
        "port": int(port),
        "hostname": hostname or None,
        "proxySSL": bool(proxy_ssl),
        "proxyPort": 443 if proxy_ssl else None,
        "upnp": False,
    })
    if proxy_ssl:
        # The tunnel talks plain HTTP to Foundry; its own certs would break that.
        options["sslCert"] = None
        options["sslKey"] = None
    with open(path, "w", encoding="utf-8") as f:
        json.dump(options, f, indent=2)
    return path


def add_firewall_rule(name, port):
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"])
    r = run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={name}", "dir=in",
             "action=allow", "protocol=TCP", f"localport={port}"])
    return r.returncode == 0


# ---------------------------------------------------------------- cloudflared

def extract_tunnel_token(text):
    matches = TUNNEL_TOKEN_RE.findall(text or "")
    return max(matches, key=len) if matches else None


def find_cloudflared():
    for p in (shutil.which("cloudflared"),
              os.path.join(CLOUDFLARED_DIR, "cloudflared.exe"),
              r"C:\Program Files (x86)\cloudflared\cloudflared.exe"):
        if p and os.path.isfile(p):
            return p
    return None


def ensure_cloudflared(emit, progress):
    exe = find_cloudflared()
    if exe:
        emit(f"Using existing cloudflared: {exe}")
        return exe
    os.makedirs(CLOUDFLARED_DIR, exist_ok=True)
    exe = os.path.join(CLOUDFLARED_DIR, "cloudflared.exe")
    emit("Downloading cloudflared...")
    download(CLOUDFLARED_URL, exe, progress)
    return exe


def tunnel_service_exists():
    return run(["sc", "query", "cloudflared"]).returncode == 0


def tunnel_service_running():
    return "RUNNING" in run(["sc", "query", "cloudflared"]).stdout


def install_tunnel_service(exe, token, emit):
    if tunnel_service_exists():
        emit("Removing the existing cloudflared service...")
        run([exe, "service", "uninstall"])
        time.sleep(3)
    emit("Installing the tunnel as a Windows service...")
    r = run([exe, "service", "install", token])
    if r.returncode != 0:
        detail = (r.stderr or r.stdout).strip().splitlines()
        raise SetupError("cloudflared service install failed: "
                         + (detail[-1] if detail else f"exit code {r.returncode}"))
    for _ in range(20):
        if tunnel_service_running():
            emit("Tunnel service is running.", "info")
            return
        time.sleep(1)
    emit("The service installed but isn't running yet. Check services.msc if the "
         "tunnel doesn't show as Healthy in Cloudflare.", "warn")


# ---------------------------------------------------------------- Cloudflare API

def cf_call(token, method, path, body=None, params=None):
    url = CF_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            payload = json.load(e)
        except ValueError:
            raise SetupError(f"Cloudflare returned HTTP {e.code}.")
    except (urllib.error.URLError, OSError) as e:
        raise SetupError(f"Couldn't reach Cloudflare: {getattr(e, 'reason', e)}")
    if not payload.get("success"):
        errors = payload.get("errors") or []
        msg = "; ".join(f"{x.get('message')} (code {x.get('code')})" for x in errors)
        raise SetupError(f"Cloudflare said: {msg or 'unknown error'}")
    return payload.get("result")


def cloudflare_auto_setup(token, hostname, port, tunnel_name, emit):
    """Create or reuse a tunnel, route hostname to Foundry, and set DNS. Returns the run token."""
    service = f"http://localhost:{port}"

    emit("Finding your domain in Cloudflare...")
    labels = hostname.split(".")
    zone = None
    for i in range(len(labels) - 1):
        result = cf_call(token, "GET", "/zones", params={"name": ".".join(labels[i:])})
        if result:
            zone = result[0]
            break
    if not zone:
        raise SetupError(f"No Cloudflare domain found for {hostname}. Check the domain is on "
                         "this account and the token has Zone Read access to it.")
    if zone.get("status") != "active":
        emit(f"{zone['name']} isn't Active in Cloudflare yet (status: {zone.get('status')}). "
             "The address won't work until it is.", "warn")
    zone_id, account_id = zone["id"], zone["account"]["id"]
    emit(f"Found {zone['name']}.", "info")

    tunnels_path = f"/accounts/{account_id}/cfd_tunnel"
    existing = cf_call(token, "GET", tunnels_path, params={"name": tunnel_name, "is_deleted": "false"})
    if existing:
        tunnel = existing[0]
        if tunnel.get("remote_config") is False:
            raise SetupError(f"A tunnel called '{tunnel_name}' already exists but is managed from a "
                             "config file, not the dashboard. Choose a different tunnel name.")
        emit(f"Reusing existing tunnel '{tunnel_name}'.")
    else:
        emit(f"Creating tunnel '{tunnel_name}'...")
        tunnel = cf_call(token, "POST", tunnels_path, {"name": tunnel_name, "config_src": "cloudflare"})
    tunnel_id = tunnel["id"]

    run_token = cf_call(token, "GET", f"{tunnels_path}/{tunnel_id}/token")

    emit(f"Routing {hostname} to {service}...")
    config_path = f"{tunnels_path}/{tunnel_id}/configurations"
    ingress = []
    if existing:
        try:
            current = cf_call(token, "GET", config_path) or {}
            ingress = (current.get("config") or {}).get("ingress") or []
        except SetupError:
            ingress = []
    # Keep other hostnames on a reused tunnel, replace ours, and keep the catch-all last.
    ingress = [r for r in ingress if r.get("hostname") and r.get("hostname") != hostname]
    ingress.append({"hostname": hostname, "service": service})
    ingress.append({"service": "http_status:404"})
    cf_call(token, "PUT", config_path, {"config": {"ingress": ingress}})

    emit("Setting up the DNS record...")
    record = {"type": "CNAME", "name": hostname, "content": f"{tunnel_id}.cfargotunnel.com",
              "proxied": True, "comment": "Foundry VTT tunnel"}
    dns_path = f"/zones/{zone_id}/dns_records"
    records = cf_call(token, "GET", dns_path, params={"name": hostname}) or []
    if records:
        rec = records[0]
        if rec["type"] != "CNAME":
            raise SetupError(f"{hostname} already has a {rec['type']} record. Delete it in "
                             "Cloudflare's DNS tab (or pick another address) and try again.")
        cf_call(token, "PUT", f"{dns_path}/{rec['id']}", record)
        emit("Updated the existing DNS record.", "info")
    else:
        cf_call(token, "POST", dns_path, record)
        emit("DNS record created.", "info")
    return run_token


# ---------------------------------------------------------------- icons

ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def pillow_available():
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _symbol_font(size):
    from PIL import ImageFont
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in SYMBOL_FONTS:
        try:
            return ImageFont.truetype(os.path.join(fonts_dir, name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_symbol(symbol, size=256):
    """Draw the symbol in the accent colour on a rounded dark tile, like the manager header."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5, fill=C["panel"])
    draw.text((size / 2, size / 2), symbol, font=_symbol_font(int(size * 0.64)),
              fill=C["accent"], anchor="mm")
    return img


def load_icon_image(path, size=256):
    """Open a user image, crop it to a centred square, and resize it."""
    from PIL import Image
    try:
        img = Image.open(path)
        img.load()
    except (OSError, ValueError) as e:
        raise SetupError(f"Couldn't open that image: {e}")
    if getattr(img, "n_frames", 1) > 1 or img.format == "ICO":
        # .ico files hold several sizes; pick the largest.
        sizes = getattr(img, "info", {}).get("sizes")
        if sizes:
            img.size = max(sizes)
            img.load()
    img = img.convert("RGBA")
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    return img.resize((size, size), Image.LANCZOS)


def save_icon(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, format="ICO", sizes=ICON_SIZES)
    return path


# ---------------------------------------------------------------- manager install

def ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"


def powershell(script):
    return run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", script])


def install_manager_files():
    """Copy the manager into APP_HOME. Returns (target, arguments) for launching it."""
    os.makedirs(APP_HOME, exist_ok=True)
    if getattr(sys, "frozen", False):
        dst = os.path.join(APP_HOME, MANAGER_EXE_NAME)
        try:
            shutil.copy2(resource_path(MANAGER_EXE_NAME), dst)
        except PermissionError:
            raise SetupError("Couldn't replace the server manager. Close it if it's open "
                             "(check the system tray and Task Manager) and try again.")
        return dst, ""
    # Running from source: copy the scripts and launch with pythonw.
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("server_manager.py", "fsm_common.py"):
        shutil.copy2(os.path.join(here, name), os.path.join(APP_HOME, name))
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    return pythonw, f'"{os.path.join(APP_HOME, "server_manager.py")}"'


def current_user():
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def remove_logon_task(task_name):
    powershell(f"Unregister-ScheduledTask -TaskName {ps_quote(task_name)} "
               "-Confirm:$false -ErrorAction SilentlyContinue")


def create_logon_task(task_name, target, arguments, workdir):
    user = current_user()
    arg_part = f" -Argument {ps_quote(arguments)}" if arguments else ""
    # ExecutionTimeLimit 0: Task Scheduler otherwise kills tasks after 72 hours.
    script = f"""
$a = New-ScheduledTaskAction -Execute {ps_quote(target)}{arg_part} -WorkingDirectory {ps_quote(workdir)}
$t = New-ScheduledTaskTrigger -AtLogOn -User {ps_quote(user)}
$p = New-ScheduledTaskPrincipal -UserId {ps_quote(user)} -LogonType Interactive -RunLevel Limited
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName {ps_quote(task_name)} -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
"""
    r = powershell(script)
    if r.returncode != 0:
        raise SetupError("Couldn't create the startup task: " + (r.stderr.strip() or "unknown error"))


def create_shortcut(folder_kind, name, target, arguments, workdir, icon=None):
    """folder_kind is 'Desktop' or 'Programs' (Start menu)."""
    icon_line = f"$lnk.IconLocation = {ps_quote(icon + ',0')}" if icon else ""
    script = f"""
$dir = [Environment]::GetFolderPath({ps_quote(folder_kind)})
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $dir {ps_quote(name + '.lnk')}))
$lnk.TargetPath = {ps_quote(target)}
$lnk.Arguments = {ps_quote(arguments)}
$lnk.WorkingDirectory = {ps_quote(workdir)}
{icon_line}
$lnk.Save()
"""
    r = powershell(script)
    if r.returncode != 0:
        raise SetupError(f"Couldn't create the {folder_kind} shortcut: {r.stderr.strip()}")


def launch_unelevated(target, arguments=""):
    """Launch the manager as the normal user even though setup runs as admin."""
    import subprocess
    if arguments:
        subprocess.Popen(f'"{target}" {arguments}')
    else:
        # explorer.exe starts programs with the desktop user's normal token.
        subprocess.Popen(["explorer.exe", target])


# ---------------------------------------------------------------- uninstall support

def install_setup_copy():
    """Keep a copy of setup in APP_HOME so Installed apps can run the uninstaller.

    Returns the UninstallString command.
    """
    if getattr(sys, "frozen", False):
        dst = os.path.join(APP_HOME, SETUP_EXE_NAME)
        if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(dst):
            shutil.copy2(sys.executable, dst)
        return f'"{dst}" --uninstall'
    return f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}" --uninstall'


def _folder_size_kb(path):
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total // 1024


def register_uninstall(display_name, uninstall_cmd, icon=""):
    import winreg
    values = {
        "DisplayName": display_name,
        "UninstallString": uninstall_cmd,
        "InstallLocation": APP_HOME,
        "Publisher": "Foundry Server Setup",
        "DisplayVersion": "1.0",
    }
    if icon:
        values["DisplayIcon"] = icon
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name in ("NoModify", "NoRepair"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, _folder_size_kb(APP_HOME))


def unregister_uninstall():
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass


def manager_running():
    """True if a server manager window is open (it holds a named mutex)."""
    try:
        handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, f"Local\\{APP_NAME}")
    except (AttributeError, OSError):
        return False
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def remove_shortcut(folder_kind, name):
    powershell(f"$p = Join-Path ([Environment]::GetFolderPath({ps_quote(folder_kind)})) "
               f"{ps_quote(name + '.lnk')}; if (Test-Path -LiteralPath $p) "
               "{ Remove-Item -LiteralPath $p -Force }")


def remove_firewall_rule(name):
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"])


def uninstall_tunnel_service(emit):
    exe = find_cloudflared()
    if not tunnel_service_exists():
        emit("No tunnel service found.")
        return
    if not exe:
        raise SetupError("The tunnel service exists but cloudflared.exe wasn't found. Remove it "
                         "from services.msc instead.")
    run([exe, "service", "uninstall"])
    for _ in range(10):
        if not tunnel_service_exists():
            break
        time.sleep(1)
    emit("Tunnel service removed.", "info")
    if is_within(exe, CLOUDFLARED_DIR):
        shutil.rmtree(CLOUDFLARED_DIR, ignore_errors=True)


def _protected_paths():
    home = os.path.expanduser("~")
    paths = [home, os.path.join(home, "Desktop"), os.path.join(home, "Documents"),
             os.path.join(home, "Downloads"), os.path.join(home, "OneDrive")]
    for var in ("SystemDrive", "SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)",
                "ProgramData", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "OneDrive"):
        value = os.environ.get(var)
        if value:
            paths.append(value + ("\\" if value.endswith(":") else ""))
    return {os.path.normcase(os.path.abspath(p)) for p in paths}


def safe_to_delete(path):
    if not path:
        return False
    p = os.path.normcase(os.path.abspath(path))
    if p in _protected_paths():
        return False
    drive, rest = os.path.splitdrive(p)
    return rest.strip("\\/") != ""  # never a drive root


def delete_folder(path, emit):
    if not os.path.isdir(path):
        emit(f"Already gone: {path}")
        return
    if not safe_to_delete(path):
        raise SetupError(f"Refusing to delete {path}: it's a system or user folder.")

    def on_error(func, target, _exc):
        try:
            os.chmod(target, 0o700)
            func(target)
        except OSError as e:
            emit(f"Couldn't delete {target}: {e}", "warn")
    shutil.rmtree(path, onerror=on_error)
    emit(f"Deleted {path}", "info")


def clear_app_home(emit):
    """Delete APP_HOME's contents, except the setup exe if it's the one running."""
    if not os.path.isdir(APP_HOME):
        return
    running = os.path.normcase(os.path.abspath(sys.executable))
    for name in os.listdir(APP_HOME):
        path = os.path.join(APP_HOME, name)
        if os.path.normcase(os.path.abspath(path)) == running:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError as e:
            emit(f"Couldn't delete {path}: {e}", "warn")
    emit("Removed the server manager and its settings.", "info")


def schedule_app_home_removal():
    """Delete APP_HOME a few seconds after setup exits (a running exe can't delete itself)."""
    import subprocess
    if not os.path.isdir(APP_HOME):
        return
    cmd = f'ping 127.0.0.1 -n 4 > nul & rmdir /s /q "{APP_HOME}"'
    subprocess.Popen(["cmd", "/c", cmd], cwd=tempfile.gettempdir(), creationflags=NO_WINDOW)
