#!/usr/bin/env bash
# Compile Alfred for Linux into a single executable: dist/alfred
#   sudo apt install python3 python3-venv libportaudio2     (once)
#   ./build.sh
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .build-venv
. .build-venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet . pyinstaller
# PortAudio (for the microphone/speaker) is bundled when it's installed on the build machine
pyinstaller --onefile --name alfred --clean --noconfirm --log-level WARN run_alfred.py
./dist/alfred --version
echo "Built: $(pwd)/dist/alfred   (install it with ./install.sh)"
