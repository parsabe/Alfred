# Alfred · linux

Alfred on your Linux computer, connected to your Alfred server:

- **hands-free**: say “Alfred, …” to this computer's microphone, hear him through its speaker
- **app window**: Alfred's interface in its own window, from your app menu
- **terminal**: type to him from any shell, SSH session or script

> This is the `linux` branch of [parsabe/Alfred](https://github.com/parsabe/Alfred). The server
> is on [`kernel`](https://github.com/parsabe/Alfred/tree/kernel); the same client for Windows
> is on [`windows`](https://github.com/parsabe/Alfred/tree/windows).

It compiles to a single executable, `alfred`. Nothing runs on this computer except the
microphone and speaker: Alfred's mind, hearing and voice are on your server.

## Install

**Ready-made:** repository → **Actions** → **Linux** → latest run → download
**alfred-linux-x86_64** → unpack, then:

```bash
sudo apt install libportaudio2      # microphone/speaker library (most desktops have it)
cd alfred-linux && ./install.sh     # add --service to listen in the background at login
```

**Build it yourself** (Ubuntu/Debian; other distributions: install Python 3.10+ and PortAudio):

```bash
sudo apt install python3 python3-venv libportaudio2
git clone --branch linux https://github.com/parsabe/Alfred.git alfred-linux && cd alfred-linux
./build.sh          # → dist/alfred
./install.sh        # → ~/.local/bin/alfred + "Alfred" in your app menu
```

The first run asks for your **server address** and **access token** (both printed by the
server's `deploy/install.sh`) and checks the connection. Settings are kept in
`~/.config/alfred/config.json` (readable only by you).

## Use

```bash
alfred                 # listen hands-free: "Alfred, what's on today?"
alfred open            # Alfred's interface in an app window (Chrome/Chromium/Edge/Brave)
alfred chat            # a typed conversation; or: alfred chat "Is the car ready?"
alfred status          # is the server reachable, which model, is his voice ready
alfred devices         # list microphones and speakers
alfred listen --input-device 3 --output-device 5 --save
alfred listen --push-to-talk      # press Enter, then speak (no wake word)
alfred listen --barge-in          # interrupt him by talking: only with an echo-cancelling
                                  # speakerphone or a headset, or he'd hear himself
alfred setup           # change server or token
```

Environment variables `ALFRED_SERVER` and `ALFRED_TOKEN` override the settings file (handy for
services and scripts).

**Always listening** (an old laptop or a Raspberry Pi as a room speaker):
`./install.sh --service` runs `alfred listen` in the background at login
(`systemctl --user status alfred-listen`). On a headless Pi also run
`sudo loginctl enable-linger $USER` so it starts without logging in.

## Source

| | |
|---|---|
| `alfred_desktop/cli.py` | the `alfred` command: setup, listen, chat, open, status, devices |
| `alfred_desktop/voice.py` | hands-free session over `WS /v1/voice` (mic → server, his voice → speaker, reconnects) |
| `alfred_desktop/chat.py` | typed conversation (`/v1/chat`) and health check |
| `alfred_desktop/window.py` | opens the interface in a browser app window |
| `alfred_desktop/config.py` | settings file, address clean-up |
| `build.sh`, `install.sh`, `packaging/` | compile, install, app-menu entries, user service |
| `tests/` | `pytest` |
