#!/bin/bash
# Health check for the Pickleball Elo launchd agents.
set -uo pipefail

PORT="${PORT:-8777}"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGS="$APP_DIR/logs"
U="$(id -u)"
SRV="com.allan.pickleball.server"
TUN="com.allan.pickleball.tunnel"

ok(){ printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad(){ printf '  \033[31m✗\033[0m %s\n' "$1"; }
info(){ printf '    %s\n' "$1"; }

echo
echo "── Pickleball Elo status ──"
echo

for L in "$SRV" "$TUN"; do
  OUT=$(launchctl print "gui/$U/$L" 2>/dev/null)
  if [[ -z "$OUT" ]]; then
    bad "$L — not loaded"
    info "install with ./install-agents.sh"
    continue
  fi
  PID=$(echo "$OUT"   | awk '/^\tpid = /{print $3}')
  RUNS=$(echo "$OUT"  | awk '/^\truns = /{print $3}')
  LAST=$(echo "$OUT"  | awk '/last exit code = /{print $NF}')
  if [[ -n "$PID" ]]; then
    ok "$L — running (pid $PID)"
  else
    bad "$L — loaded but not running"
    [[ -n "$LAST" ]] && info "last exit code: $LAST"
  fi
  # A high restart count means it's crash-looping, which "running" alone hides.
  [[ -n "$RUNS" && "$RUNS" -gt 5 ]] && info "⚠ started $RUNS times — check logs for a crash loop"
done

echo
if curl -fsS -m 5 "http://localhost:$PORT/api/auth" >/dev/null 2>&1; then
  AUTH=$(curl -fsS -m 5 "http://localhost:$PORT/api/auth")
  ok "app responding on localhost:$PORT"
  PS=$(echo "$AUTH" | grep -o '"pinSet": *[a-z]*' | awk '{print $2}')
  AS=$(echo "$AUTH" | grep -o '"adminSet": *[a-z]*' | awk '{print $2}')
  info "friend PIN: ${PS:-?}   admin PIN: ${AS:-?}"
else
  bad "app NOT responding on localhost:$PORT"
  [[ -f "$LOGS/server.err.log" ]] && info "last error: $(tail -2 "$LOGS/server.err.log" | tr '\n' ' ')"
fi

# ── public URL ──
echo
if [[ -f "$APP_DIR/.tunnel-hostname" ]]; then
  H="https://$(cat "$APP_DIR/.tunnel-hostname")"
  if curl -fsS -m 10 -o /dev/null "$H/api/auth" 2>/dev/null; then
    ok "permanent URL live: $H"
  else
    bad "permanent URL not reachable yet: $H"
    info "DNS can take a minute; also check $LOGS/tunnel.err.log"
  fi
else
  URL=$(grep -ohE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGS"/tunnel*.log 2>/dev/null | tail -1)
  if [[ -n "$URL" ]]; then
    ok "quick-tunnel URL: $URL"
    info "changes whenever the tunnel restarts"
  else
    bad "no tunnel URL found in logs"
  fi
fi

echo
DB="$APP_DIR/pickleball.db"
if [[ -f "$DB" ]]; then
  SZ=$(du -h "$DB" | cut -f1)
  CNT=$(sqlite3 "$DB" "SELECT (SELECT COUNT(*) FROM players)||' players, '||(SELECT COUNT(*) FROM matches)||' matches'" 2>/dev/null)
  ok "database $SZ — ${CNT:-unreadable}"
else
  info "no database yet (created on first use)"
fi
echo
