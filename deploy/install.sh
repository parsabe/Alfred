#!/usr/bin/env bash
# Install Alfred on an Ubuntu server: Gemma 3 12B (Ollama), local hearing and voice, a system
# service that starts on boot, and (optionally) Tailscale for private HTTPS from your devices.
#
#   git clone https://github.com/parsabe/Alfred.git && cd Alfred
#   sudo ./deploy/install.sh                 # server only, reachable on this machine
#   sudo ./deploy/install.sh --tailscale     # + private https:// address for phone/watch/laptop
#   sudo ./deploy/install.sh --lan           # + listen on the local network (http, typing only)
#
# Options (environment variables):
#   ALFRED_MODEL=gemma3:12b    the Ollama model (gemma3:4b is ~3x faster on a CPU-only server)
#   ALFRED_DIR=/opt/alfred     where Alfred is installed
#   ALFRED_PORT=8000
#
# Safe to run again: it updates the code and keeps your .env, data and conversations.
set -euo pipefail

MODEL="${ALFRED_MODEL:-gemma3:12b}"
DIR="${ALFRED_DIR:-/opt/alfred}"
PORT="${ALFRED_PORT:-8000}"
LISTEN="127.0.0.1"
TAILSCALE=0
for arg in "$@"; do
  case "$arg" in
    --tailscale) TAILSCALE=1 ;;
    --lan) LISTEN="0.0.0.0" ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"

say "Checking the machine"
. /etc/os-release
echo "OS: ${PRETTY_NAME:-unknown}"
MEM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
echo "RAM: ${MEM_GB} GB"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  echo "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"
else
  echo "GPU: none detected (Gemma will run on the CPU: it works, but answers take a while)"
fi
if [ "$MODEL" = "gemma3:12b" ] && [ "$MEM_GB" -lt 11 ]; then
  echo "WARNING: gemma3:12b needs about 9-10 GB free RAM. Consider ALFRED_MODEL=gemma3:4b." >&2
fi

say "Installing system packages"
apt-get update -qq
apt-get install -y -qq curl git ca-certificates rsync >/dev/null

say "Installing Ollama and $MODEL"
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/alfred.conf <<CONF
[Service]
# Alfred keeps its context inside this window (LOCAL_LLM_CONTEXT_TOKENS in .env).
Environment="OLLAMA_CONTEXT_LENGTH=8192"
# Keep Gemma loaded so replies don't wait for it to load from disk.
Environment="OLLAMA_KEEP_ALIVE=24h"
# Only Alfred talks to Ollama; keep it private to this machine.
Environment="OLLAMA_HOST=127.0.0.1:11434"
CONF
systemctl daemon-reload
systemctl enable --now ollama >/dev/null
systemctl restart ollama
for _ in $(seq 1 30); do curl -fs http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done
ollama pull "$MODEL"

say "Installing Alfred into $DIR"
id alfred >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/alfred --shell /usr/sbin/nologin alfred
mkdir -p "$DIR"
rsync -a --delete --exclude .git --exclude .venv --exclude data --exclude .env \
  --exclude '__pycache__' "$SRC"/ "$DIR"/
mkdir -p "$DIR/data"
if ! command -v uv >/dev/null 2>&1 && [ ! -x /usr/local/bin/uv ]; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi
UV="$(command -v uv || echo /usr/local/bin/uv)"
chown -R alfred:alfred "$DIR"
sudo -u alfred -H bash -c "cd '$DIR' && '$UV' sync --frozen --no-dev --python 3.12"

if [ ! -f "$DIR/.env" ]; then
  TOKEN=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 43)
  sed -e "s|^ALFRED_API_TOKEN=.*|ALFRED_API_TOKEN=$TOKEN|" \
      -e "s|^LLM_MODEL=.*|LLM_MODEL=$MODEL|" \
      "$DIR/.env.example" > "$DIR/.env"
  NEW_TOKEN=1
else
  TOKEN=$(grep '^ALFRED_API_TOKEN=' "$DIR/.env" | cut -d= -f2-)
  NEW_TOKEN=0
fi
grep -q '^ALFRED_LISTEN=' "$DIR/.env" && sed -i "s|^ALFRED_LISTEN=.*|ALFRED_LISTEN=$LISTEN|" "$DIR/.env" \
  || echo "ALFRED_LISTEN=$LISTEN" >> "$DIR/.env"
grep -q '^ALFRED_PORT=' "$DIR/.env" || echo "ALFRED_PORT=$PORT" >> "$DIR/.env"
chown alfred:alfred "$DIR/.env"
chmod 600 "$DIR/.env"

say "Downloading Alfred's hearing (Whisper) and voice (Piper)"
sudo -u alfred -H bash -c "cd '$DIR' && .venv/bin/python -m scripts.download_models" \
  || echo "Model download failed; Alfred will retry when he starts."

say "Starting the Alfred service"
sed -e "s|/opt/alfred|$DIR|g" "$DIR/deploy/alfred.service" > /etc/systemd/system/alfred.service
systemctl daemon-reload
systemctl enable --now alfred >/dev/null
systemctl restart alfred
for _ in $(seq 1 60); do curl -fs "http://127.0.0.1:$PORT/health" >/dev/null && break; sleep 1; done
curl -fs "http://127.0.0.1:$PORT/health" >/dev/null && echo "Alfred is running." \
  || { echo "Alfred did not start; see: journalctl -u alfred -n 50" >&2; exit 1; }

URL="http://127.0.0.1:$PORT"
if [ "$TAILSCALE" -eq 1 ]; then
  say "Setting up Tailscale (private HTTPS for your devices)"
  command -v tailscale >/dev/null 2>&1 || curl -fsSL https://tailscale.com/install.sh | sh
  tailscale status >/dev/null 2>&1 || tailscale up
  tailscale serve --bg "$PORT"
  TS_NAME=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
  URL="https://$TS_NAME"
elif [ "$LISTEN" = "0.0.0.0" ]; then
  URL="http://$(hostname -I | awk '{print $1}'):$PORT"
fi

say "Done"
cat <<DONE
  Open Alfred:   $URL
  Access token:  $( [ "$NEW_TOKEN" -eq 1 ] && echo "$TOKEN" || echo "(unchanged; it's ALFRED_API_TOKEN in $DIR/.env)" )

  Next: connect your devices. See docs/DEVICES.md (phone app, Apple Watch, laptop, speaker).

  Useful commands:
    sudo systemctl status alfred        # is it running?
    sudo journalctl -u alfred -f        # live logs
    sudo nano $DIR/.env                 # settings (then: sudo systemctl restart alfred)
    sudo nano $DIR/prompts/alfred_system.md   # Alfred's personality (applies immediately)
DONE
