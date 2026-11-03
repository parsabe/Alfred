#!/usr/bin/env bash
# Install Alfred for the current user: the `alfred` command, app-menu entries, and optionally an
# always-listening background service.
#   ./install.sh               # needs dist/alfred (./build.sh) or a downloaded `alfred` next to it
#   ./install.sh --service     # also start hands-free listening at login
set -euo pipefail
cd "$(dirname "$0")"
BIN="dist/alfred"; [ -x "$BIN" ] || BIN="./alfred"
[ -x "$BIN" ] || { echo "Build first (./build.sh) or put the downloaded 'alfred' here." >&2; exit 1; }

mkdir -p ~/.local/bin ~/.local/share/applications ~/.local/share/icons/hicolor/512x512/apps
install -m 755 "$BIN" ~/.local/bin/alfred
install -m 644 packaging/alfred.png ~/.local/share/icons/hicolor/512x512/apps/alfred.png
install -m 644 packaging/alfred.desktop ~/.local/share/applications/alfred.desktop
install -m 644 packaging/alfred-listen.desktop ~/.local/share/applications/alfred-listen.desktop
command -v update-desktop-database >/dev/null && update-desktop-database ~/.local/share/applications || true

if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
  echo "Note: for the microphone and speaker install PortAudio:  sudo apt install libportaudio2"
fi

case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Add ~/.local/bin to your PATH to run 'alfred' from a terminal.";; esac

~/.local/bin/alfred status >/dev/null 2>&1 || ~/.local/bin/alfred setup

if [ "${1:-}" = "--service" ]; then
  mkdir -p ~/.config/systemd/user
  install -m 644 packaging/alfred-listen.service ~/.config/systemd/user/alfred-listen.service
  systemctl --user daemon-reload
  systemctl --user enable --now alfred-listen.service
  echo "Alfred is listening in the background (systemctl --user status alfred-listen)."
fi
echo "Installed. Find 'Alfred' in your applications, or run: alfred"
