#!/usr/bin/env bash
# Check an Alfred server and say what's wrong, in plain words.
#   sudo /opt/alfred/deploy/doctor.sh                       # the server itself
#   sudo /opt/alfred/deploy/doctor.sh alfred.example.com    # + the website in front of it
# Safe to run any time; changes nothing; never prints your token.
set -uo pipefail

DOMAIN="${1:-}"
DIR="${ALFRED_DIR:-/opt/alfred}"
ENVF="$DIR/.env"
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; PROBLEMS=$((PROBLEMS + 1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fix()  { printf '      → %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }
PROBLEMS=0
envval() { grep -E "^$1=" "$ENVF" 2>/dev/null | tail -1 | cut -d= -f2-; }

head_ "Alfred service"
if [ ! -f "$ENVF" ]; then
  bad "no $ENVF: Alfred isn't installed in $DIR"; fix "run deploy/install.sh (or set ALFRED_DIR=…)"
fi
PORT="$(envval ALFRED_PORT)"; PORT="${PORT:-8000}"
TOKEN="$(envval ALFRED_API_TOKEN)"
if systemctl is-active --quiet alfred; then
  ok "running (systemd: active)"
else
  bad "not running (systemd: $(systemctl is-active alfred 2>/dev/null))"
  echo "      last log lines:"
  journalctl -u alfred -n 25 --no-pager 2>/dev/null | sed 's/^/        /' \
    | sed -E 's/(ALFRED_API_TOKEN=)[^ ]+/\1[hidden]/g'
  if journalctl -u alfred -n 50 --no-pager 2>/dev/null | grep -qiE "226/NAMESPACE|namespac"; then
    fix "this VPS can't sandbox services: run deploy/install.sh again (it now handles this)"
  fi
  if journalctl -u alfred -n 50 --no-pager 2>/dev/null | grep -qi "address already in use"; then
    fix "port $PORT is taken: run deploy/install.sh again (it now picks a free port)"
  fi
fi
LISTENER=$(ss -ltnpH "sport = :$PORT" 2>/dev/null | sed -nE 's/.*users:\(\("([^"]+)".*/\1/p' | head -1)
if [ -n "$LISTENER" ]; then
  case "$LISTENER" in uvicorn|python*) ok "listening on port $PORT ($LISTENER)";;
    *) bad "port $PORT belongs to '$LISTENER', not Alfred"; fix "run deploy/install.sh again to move Alfred to a free port";;
  esac
else
  bad "nothing is listening on port $PORT"
fi
if curl -fs --max-time 5 "http://127.0.0.1:$PORT/health" >/dev/null; then
  ok "answers on http://127.0.0.1:$PORT/health"
  H=$(curl -fs --max-time 5 -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT/health" || true)
  if echo "$H" | grep -q '"llm"'; then
    echo "$H" | python3 -c '
import json, sys
h = json.load(sys.stdin); v = h.get("voice", {}); l = h.get("llm", {})
print(f"      model: {l.get(\"model\")} · hearing: {v.get(\"stt\")} ({v.get(\"stt_status\")}) · voice: {v.get(\"tts\")} ({v.get(\"tts_status\")})")' 2>/dev/null
  else
    warn "the token in $ENVF wasn't accepted (was .env edited without restarting?)"
  fi
fi

head_ "Gemma (Ollama)"
MODEL="$(envval LLM_MODEL)"; MODEL="${MODEL:-gemma3:12b}"
if curl -fs --max-time 5 http://127.0.0.1:11434/api/tags >/tmp/.alfred-tags 2>/dev/null; then
  ok "Ollama is running"
  if grep -q "\"$MODEL\"" /tmp/.alfred-tags || grep -q "\"name\":\"$MODEL" /tmp/.alfred-tags; then
    ok "$MODEL is downloaded"
  else
    bad "$MODEL isn't downloaded"; fix "ollama pull $MODEL"
  fi
else
  bad "Ollama isn't answering on 127.0.0.1:11434"; fix "sudo systemctl restart ollama; journalctl -u ollama -n 30"
fi
rm -f /tmp/.alfred-tags
FREE_GB=$(awk '/MemAvailable/ {printf "%.1f", $2/1024/1024}' /proc/meminfo)
echo "      memory available now: ${FREE_GB} GB"

if [ -n "$DOMAIN" ]; then
  head_ "Website $DOMAIN"
  PUBLIC_IP=$(curl -fs --max-time 5 https://api.ipify.org || true)
  DNS_IPS=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')
  if [ -z "$DNS_IPS" ]; then
    bad "$DOMAIN doesn't resolve"; fix "create an A record for $DOMAIN pointing to ${PUBLIC_IP:-the IP of this server}"
  elif [ -n "$PUBLIC_IP" ] && echo " $DNS_IPS" | grep -q " $PUBLIC_IP "; then
    ok "DNS points to this server ($PUBLIC_IP)"
  else
    warn "DNS gives $DNS_IPS, this server is ${PUBLIC_IP:-unknown}"
    echo "      (normal if Cloudflare's orange cloud is on; otherwise fix the A record)"
  fi

  NGX=$(command -v nginx || ls /www/server/nginx/sbin/nginx 2>/dev/null || true)
  if [ -n "$NGX" ]; then
    if "$NGX" -t >/dev/null 2>&1; then ok "nginx configuration is valid"
    else bad "nginx configuration has an error"; "$NGX" -t 2>&1 | tail -3 | sed 's/^/        /'; fi
  else
    warn "nginx not found"
  fi
  for p in 80 443; do
    if [ -n "$(ss -ltnH "sport = :$p" 2>/dev/null)" ]; then ok "something listens on port $p"
    else bad "nothing listens on port $p"; fix "start nginx / enable the site; open port $p in the firewall"; fi
  done
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw status | grep -qE "^(443|443/tcp)\s+ALLOW" && ok "ufw allows 443" \
      || { bad "ufw is active and doesn't allow 443"; fix "sudo ufw allow 80,443/tcp"; }
  fi

  CONF=$(grep -rlsE "server_name[^;]*\b$DOMAIN\b" /www/server/panel/vhost/nginx /etc/nginx 2>/dev/null | head -1)
  if [ -n "$CONF" ]; then
    ok "site config: $CONF"
    PROXY_FILES="$CONF $(grep -oE 'include [^;]*proxy[^;]*' "$CONF" 2>/dev/null | awk '{print $2}' | tr '\n' ' ')"
    ALL=$(for f in $PROXY_FILES; do for g in $f; do cat "$g" 2>/dev/null; done; done; \
          cat /www/server/panel/vhost/nginx/proxy/"$DOMAIN"/*.conf 2>/dev/null)
    echo "$ALL" | grep -qE "proxy_pass\s+http://(127\.0\.0\.1|localhost):$PORT" \
      && ok "reverse proxy → 127.0.0.1:$PORT" \
      || { bad "no reverse proxy to http://127.0.0.1:$PORT for this site"
           fix "panel: Website → $DOMAIN → Reverse proxy → target http://127.0.0.1:$PORT"; }
    echo "$ALL" | grep -q "proxy_pass\s*https\?://$DOMAIN" \
      && { bad "the reverse proxy points at $DOMAIN itself (a loop)"; fix "target must be http://127.0.0.1:$PORT"; }
    echo "$ALL" | grep -qi "Upgrade" && ok "WebSocket upgrade headers present" \
      || { bad "no WebSocket Upgrade headers: hands-free voice will fail"; fix "add the lines from $DIR/deploy/nginx-$DOMAIN.conf"; }
    echo "$ALL" | grep -q "proxy_buffering off" && ok "streaming (proxy_buffering off)" \
      || { warn "proxy_buffering isn't off: replies arrive all at once"; fix "add: proxy_buffering off;"; }
  else
    warn "no nginx site with server_name $DOMAIN found"
  fi

  # straight to this server, bypassing DNS/Cloudflare
  CODE=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/health" || true)
  case "$CODE" in
    200) ok "this server's nginx serves https://$DOMAIN/health (HTTP 200)";;
    000) bad "this server's nginx doesn't answer HTTPS for $DOMAIN"; fix "panel: Website → $DOMAIN → SSL → Let's Encrypt";;
    301|302|307|308) bad "this server's nginx redirects https://$DOMAIN/health ($CODE) instead of passing it to Alfred"
         fix "the reverse proxy must handle location / ; check the site config";;
    *) bad "this server's nginx answered HTTP $CODE for /health"; fix "502/504 = proxy target wrong or Alfred down";;
  esac

  # the way your phone sees it
  HDRS=$(curl -sSIL --max-redirs 10 --max-time 15 "https://$DOMAIN/health" 2>&1); RC=$?
  if [ $RC -eq 47 ] || echo "$HDRS" | grep -q "Maximum (10) redirects"; then
    bad "https://$DOMAIN redirects in a loop (Firefox: “The page isn't redirecting properly”)"
    if echo "$HDRS" | grep -qi "server: cloudflare"; then
      fix "Cloudflare → SSL/TLS → set mode to “Full (strict)” (not “Flexible”); this is the usual cause"
    else
      fix "turn off one of the HTTPS redirects (panel “Force HTTPS” vs. another redirect rule)"
    fi
  elif [ $RC -ne 0 ]; then
    WHY=$(echo "$HDRS" | tail -1); [ -n "$WHY" ] || WHY="curl exit code $RC"
    bad "https://$DOMAIN can't be reached from the internet side ($WHY)"
    fix "open port 443 in the panel's firewall AND your VPS provider's firewall/security group"
  elif echo "$HDRS" | grep -qE "^HTTP/[0-9.]+ 200"; then
    ok "https://$DOMAIN/health works from outside"
    echo "$HDRS" | grep -qi "server: cloudflare" && warn "behind Cloudflare: WebSockets must be on (Network → WebSockets)"
  else
    bad "https://$DOMAIN/health answered: $(echo "$HDRS" | grep -E '^HTTP/' | tail -1)"
  fi
fi

echo
if [ "$PROBLEMS" -eq 0 ]; then echo "Everything looks right."; else echo "$PROBLEMS problem(s) found; fixes are listed above."; fi
