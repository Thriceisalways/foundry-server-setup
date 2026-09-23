# Foundry Server Setup

A Windows setup wizard for self-hosting [Foundry Virtual Tabletop](https://foundryvtt.com), plus the server manager it installs. Point a GM at one .exe and they're walked through installing Foundry, Node.js, an optional Cloudflare Tunnel, backups, and a manager that opens when they log in.

## What the wizard does

1. **Welcome**: checks Windows version, admin rights, disk space, internet, and that the server manager isn't running. Offers **Uninstall** if already installed.
2. **Server name**: name, header symbol, and app icon (match the symbol, or use your own image).
3. **Foundry VTT**: installs the Node.js build from a timed URL or a downloaded zip, or uses an existing install. Detects the Foundry version.
4. **Node.js**: downloads a private copy of the Node.js version that Foundry needs (V14 needs 24, V13 needs 22), or uses an existing compatible install.
5. **Data folder**: detects existing Foundry data (including a User Data Path redirect) and warns against synced folders like OneDrive.
6. **Licence**: optionally saves the licence key, or skips it for first launch.
7. **Cloudflare**: guided tunnel (paste a token), automatic tunnel (paste an API token), existing tunnel, or no Cloudflare.
8. **Domain**: checks the domain is on Cloudflare, with a guide for adding one.
9. **Web address**: hostname and port, then writes Foundry's `options.json`. Adds a firewall rule when not using Cloudflare.
10. **Tunnel**: installs `cloudflared` as a Windows service and connects it.
11. **Backups**: folder, compression tool (WinRAR, 7-Zip, or ZIP), and how many to keep.
12. **Server manager**: installs it, creates the login task and shortcuts, and registers it in Installed apps.
13. **Finish**: test launch, an Administrator Password reminder, and a checklist of anything left to do.

Re-running setup pre-fills everything from the current install.

## Server manager

- Start and stop Foundry, with a colour-coded log
- Choose which world launches, or none
- Keeps the PC awake while the server runs, and restores normal sleep afterwards
- Clears stale lock files, and detects crashes and Foundry's own restarts
- Backups with automatic clean-up of old ones
- Opens Foundry on localhost, with a link to test the public address
- Warns while no Administrator Password is set

## Building

On Windows with Python 3.10 or newer, double-click `build.bat`. It installs the requirements and produces:

```
dist\FoundryServerSetup.exe
```

That single file is all you need to share. It contains the server manager inside it.

## Running from source

From an **elevated** terminal:

```
pip install -r requirements.txt
python installer.py
```

From source, the manager is installed as scripts and launched with `pythonw.exe` rather than as an .exe.

## Project layout

| File | Purpose |
|---|---|
| `installer.py` | The setup wizard and uninstaller UI |
| `setup_tasks.py` | What the wizard actually does: downloads, Cloudflare, tasks, shortcuts, uninstall |
| `server_manager.py` | The server manager, driven by `config.json` |
| `fsm_common.py` | Shared theme, config, backups, and Foundry file helpers |
| `build.bat` | Builds both .exe files with PyInstaller |

## Where things are installed

| Item | Location |
|---|---|
| Foundry (Node.js build) | Chosen during setup, default `C:\FoundryVTT` |
| Foundry data | Chosen during setup, default `%LOCALAPPDATA%\FoundryVTT` |
| Server manager, `config.json`, icon | `%LOCALAPPDATA%\FoundryServerManager\` |
| Private Node.js | `%LOCALAPPDATA%\FoundryServerManager\runtime\` |
| cloudflared | `C:\Program Files\cloudflared\` (the `cloudflared` Windows service) |
| Backups | Chosen during setup, default `%USERPROFILE%\FoundryVTTBackups` |

## Uninstalling

From **Settings > Apps > Installed apps**, or the **Uninstall** button on the setup welcome page. It can take a backup first, and optionally removes the tunnel service, the Foundry program, and the data folder. Backups are never deleted.

## Notes for users

- Windows SmartScreen will warn about the unsigned .exe. Click **More info**, then **Run anyway**.
- Set an Administrator Password in Foundry (Setup screen > Configuration) before sharing your address.
- Backups include your licence key, so don't share them publicly.
- Cloudflare's free plan limits uploads through the tunnel to 100 MB per file. Upload large files on the host PC via `http://localhost:30000` instead.
