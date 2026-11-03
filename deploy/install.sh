#!/usr/bin/env bash
# Install Alfred on an Ubuntu server: Gemma 3 12B (Ollama), local hearing and voice, a system
# service that starts on boot, and (optionally) Tailscale for private HTTPS from your devices.
#
#   git clone --branch kernel https://github.com/parsabe/Alfred.git && cd Alfred
#   sudo ./deploy/install.sh                 # server only, reachable on this machine
#   sudo ./deploy/install.sh --tailscale     # + private https:// address for phone/watch/laptop
#   sudo ./deploy/install.sh --lan           # + listen on the local network (http, typing only)
#   sudo ./deploy/install.sh --domain alfred.example.com
#                                            # behind your own nginx / aaPanel site with HTTPS
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
PORT="${ALFRED_PORT:-}"
LISTEN="127.0.0.1"
TAILSCALE=0
DOMAIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tailscale) TAILSCALE=1 ;;
    --lan) LISTEN="0.0.0.0" ;;
    --domain) DOMAIN="${2:-}"; shift; [ -n "$DOMAIN" ] || { echo "--domain needs a name" >&2; exit 2; } ;;
    --domain=*) DOMAIN="${1#--domain=}" ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
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
apt-get install -y -qq curl git ca-certificates rsync python3 >/dev/null

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

say "Fetching the web interface (web branch)"
if [ ! -f "$SRC/web/index.html" ] || [ -d "$SRC/web/.git" ]; then
  "$SRC/scripts/fetch_web.sh" "$SRC/web"
fi

say "Installing Alfred into $DIR"
id alfred >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/alfred --shell /usr/sbin/nologin alfred
mkdir -p "$DIR"
rsync -a --delete --exclude .git --exclude web/.git --exclude .venv --exclude data --exclude .env \
  --exclude '__pycache__' "$SRC"/ "$DIR"/
mkdir -p "$DIR/data"
# uv must live somewhere the `alfred` service user can run it: a copy in root's home
# (/root/.local/bin) is not readable by other users.
UV=/usr/local/bin/uv
if [ ! -x "$UV" ]; then
  if command -v uv >/dev/null 2>&1; then
    install -m 755 "$(command -v uv)" "$UV"
  else
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
  fi
fi
sudo -u alfred -H "$UV" --version >/dev/null || { echo "The alfred user can't run $UV" >&2; exit 1; }
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
set_env() {  # set KEY=VALUE in .env, adding it if missing
  if grep -q "^$1=" "$DIR/.env"; then sed -i "s|^$1=.*|$1=$2|" "$DIR/.env"; else echo "$1=$2" >> "$DIR/.env"; fi
}
set_env ALFRED_LISTEN "$LISTEN"

# Pick the port: keep the one Alfred already uses, else 8000, else the next free one.
[ -n "$PORT" ] || PORT=$(grep -E '^ALFRED_PORT=[0-9]+$' "$DIR/.env" | cut -d= -f2)
[ -n "$PORT" ] || PORT=8000
systemctl stop alfred 2>/dev/null || true   # free our own port before checking
port_user() { ss -ltnpH "sport = :$1" 2>/dev/null | sed -nE 's/.*users:\(\("([^"]+)".*/\1/p' | head -1; }
if [ -n "$(ss -ltnH "sport = :$PORT" 2>/dev/null)" ]; then
  echo "Port $PORT is already used by: $(port_user "$PORT" || echo 'another program')"
  for candidate in $(seq $((PORT + 1)) $((PORT + 100))); do
    if [ -z "$(ss -ltnH "sport = :$candidate" 2>/dev/null)" ]; then PORT=$candidate; break; fi
  done
  echo "Alfred will use port $PORT instead."
fi
set_env ALFRED_PORT "$PORT"
chown alfred:alfred "$DIR/.env"
chmod 600 "$DIR/.env"

say "Downloading Alfred's hearing (Whisper) and voice (Piper)"
sudo -u alfred -H bash -c "cd '$DIR' && .venv/bin/python -m scripts.download_models" \
  || echo "Model download failed; Alfred will retry when he starts."

say "Starting the Alfred service"
sed -e "s|/opt/alfred|$DIR|g" "$DIR/deploy/alfred.service" > /etc/systemd/system/alfred.service
rm -f /etc/systemd/system/alfred.service.d/no-sandbox.conf
systemctl daemon-reload
systemctl enable alfred >/dev/null 2>&1

wait_healthy() {
  for _ in $(seq 1 60); do
    curl -fs "http://127.0.0.1:$PORT/health" >/dev/null && return 0
    systemctl is-failed --quiet alfred && return 1
    sleep 1
  done
  return 1
}
systemctl restart alfred || true
if ! wait_healthy; then
  # Some VPS types (OpenVZ/LXC containers) can't create the service's private sandbox
  # (status 226/NAMESPACE). Retry without it.
  if journalctl -u alfred -n 30 --no-pager 2>/dev/null | grep -qiE "226/NAMESPACE|namespac|Failed to set up mount"; then
    echo "This server can't sandbox services; starting Alfred without the sandbox."
    mkdir -p /etc/systemd/system/alfred.service.d
    printf '[Service]\nProtectSystem=no\nProtectHome=no\nPrivateTmp=no\nReadWritePaths=\n' \
      > /etc/systemd/system/alfred.service.d/no-sandbox.conf
    systemctl daemon-reload
    systemctl reset-failed alfred 2>/dev/null || true
    systemctl restart alfred || true
  fi
fi
if wait_healthy; then
  echo "Alfred is running on 127.0.0.1:$PORT."
else
  echo "Alfred did not start. Here is why (last log lines):" >&2
  journalctl -u alfred -n 40 --no-pager >&2 || true
  echo >&2
  echo "Run  sudo $DIR/deploy/doctor.sh ${DOMAIN:-}  and send its output if this isn't clear." >&2
  exit 1
fi

URL="http://127.0.0.1:$PORT"
if [ -n "$DOMAIN" ]; then
  say "Reverse proxy for https://$DOMAIN"
  NGINX_SNIPPET="$DIR/deploy/nginx-$DOMAIN.conf"
  cat > "$NGINX_SNIPPET" <<NGINX
# Alfred behind nginx at https://$DOMAIN  (paste into the site's server { } block)
location / {
    proxy_pass http://127.0.0.1:$PORT;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    # hands-free voice is a WebSocket
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection "upgrade";
    # stream Alfred's words as he says them, and keep the voice connection open
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    client_max_body_size 12m;
}
NGINX
  URL="https://$DOMAIN"
  if [ -d /www/server/panel ]; then
    cat <<PANEL
  aaPanel / BaoTa detected. In the panel:
    1. Website → $DOMAIN → SSL → Let's Encrypt → Apply, then turn on "Force HTTPS".
    2. Website → $DOMAIN → Reverse proxy → Add: target URL http://127.0.0.1:$PORT,
       send domain \$host. Save.
    3. Open that reverse proxy's configuration file and make sure it contains the lines in
       $NGINX_SNIPPET (especially Upgrade/Connection, proxy_buffering off and the 3600s
       timeouts), then save. The panel reloads nginx.
PANEL
  else
    echo "  Add the contents of $NGINX_SNIPPET to the nginx server block for $DOMAIN"
    echo "  (with HTTPS, e.g. certbot --nginx -d $DOMAIN), then: sudo nginx -t && sudo systemctl reload nginx"
  fi
  echo "  Make sure $DOMAIN's DNS points to this server."
fi
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

  Next: connect your devices: https://github.com/parsabe/Alfred/blob/docs/DEVICES.md

  Useful commands:
    sudo systemctl status alfred        # is it running?
    sudo journalctl -u alfred -f        # live logs
    sudo nano $DIR/.env                 # settings (then: sudo systemctl restart alfred)
    sudo nano $DIR/prompts/alfred_system.md   # Alfred's personality (applies immediately)
DONE
