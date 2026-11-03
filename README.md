# Alfred · windows

Alfred on your Windows PC, connected to your Alfred server:

- **hands-free**: say “Alfred, …” to the PC's microphone, hear him through its speakers
- **app window**: Alfred's interface in its own window, from the Start menu
- **terminal**: type to him from Command Prompt or PowerShell

> This is the `windows` branch of [parsabe/Alfred](https://github.com/parsabe/Alfred). The server
> is on [`kernel`](https://github.com/parsabe/Alfred/tree/kernel); the same client for Linux is
> on [`linux`](https://github.com/parsabe/Alfred/tree/linux).

It compiles to a single program, `Alfred.exe`, plus an installer. Nothing heavy runs on the PC:
Alfred's mind, hearing and voice are on your server.

## Install

**Ready-made:** repository → **Actions** → **Windows** → latest run → download
**alfred-windows** → unzip → run **Alfred-Setup.exe** (no administrator rights needed).
Windows SmartScreen may warn because the installer isn't code-signed: *More info* → *Run anyway*.

At the end it opens **Alfred setup**: enter your **server address** and **access token** (both
printed by the server's `deploy/install.sh`). You then find in the Start menu:

| | |
|---|---|
| **Alfred** | his interface in an app window (Edge or Chrome) |
| **Alfred (hands-free)** | listens for “Alfred, …” with this PC's microphone |
| **Alfred setup** | change server or token |

Optional during install: a desktop shortcut, **listen when I sign in**, and the `alfred` command
in PATH.

## Build it yourself

Install [Python 3.10+](https://www.python.org/downloads/windows/) (tick *Add python.exe to PATH*)
and, for the installer, [Inno Setup 6](https://jrsoftware.org/isinfo.php). Then in PowerShell:

```powershell
git clone --branch windows https://github.com/parsabe/Alfred.git alfred-windows
cd alfred-windows
.\build.ps1          # → dist\Alfred.exe and dist\Alfred-Setup.exe
```

(If scripts are blocked: `Set-ExecutionPolicy -Scope Process Bypass` first.)

## Use from a terminal

```powershell
alfred                 # listen hands-free: "Alfred, what's on today?"
alfred open            # app window
alfred chat            # typed conversation; or: alfred chat "Is the car ready?"
alfred status          # server reachable? which model? voice ready?
alfred devices         # list microphones and speakers
alfred listen --input-device 3 --output-device 5 --save
alfred listen --push-to-talk      # press Enter, then speak
alfred listen --barge-in          # interrupt him by talking (needs a headset or an
                                  # echo-cancelling speakerphone)
alfred setup
```

Settings live in `%APPDATA%\Alfred\config.json`. `ALFRED_SERVER` / `ALFRED_TOKEN` environment
variables override them.

## Source

| | |
|---|---|
| `alfred_desktop/` | the client (same code as the `linux` branch): setup, listen, chat, open, status, devices |
| `build.ps1` | compile `Alfred.exe` (PyInstaller) and the installer |
| `installer/alfred.iss` | Inno Setup script: per-user install, Start menu, optional autostart and PATH |
| `packaging/alfred.ico` | icon |
| `tests/` | `pytest` |
