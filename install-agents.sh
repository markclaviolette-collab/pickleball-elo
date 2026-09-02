#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Pickleball Elo — install launchd agents so the app + tunnel start on login
# and restart themselves if they crash.
#
#   ./install-agents.sh            # quick tunnel (random URL, no account)
#   ./install-agents.sh named      # named tunnel (permanent URL, needs setup)
#
# Uninstall with ./uninstall-agents.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

MODE="${1:-quick}"
DRY=0
[[ "$MODE" == "--dry-run" || "${2:-}" == "--dry-run" ]] && DRY=1
[[ "$MODE" == "--dry-run" ]] && MODE="quick"

PORT="${PORT:-8777}"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL_SRV="com.pickleball.elo.server"
LABEL_TUN="com.pickleball.elo.tunnel"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$APP_DIR/logs"
UID_NUM="$(id -u)"
[[ $DRY -eq 1 ]] && AGENTS="$(mktemp -d)" && echo "DRY RUN — writing plists to $AGENTS, not loading them"

# launchd runs with a minimal environment and NO shell PATH, so every binary
# must be referenced by absolute path. Resolve them now and fail loudly if
# they're missing rather than producing an agent that silently never starts.
PYTHON="$(command -v python3 || true)"
[[ -z "$PYTHON" ]] && { echo "✗ python3 not found"; exit 1; }
# sys.executable resolves symlinks: /usr/local/bin/python3 often points into a
# framework build that a future installer could replace.
PYTHON="$(python3 -c 'import sys; print(sys.executable)')"

CLOUDFLARED="$(command -v cloudflared || true)"
if [[ -z "$CLOUDFLARED" ]]; then
  if [[ $DRY -eq 1 ]]; then
    CLOUDFLARED="/opt/homebrew/bin/cloudflared"   # placeholder for validation
    echo "  (cloudflared missing — using placeholder path for the dry run)"
  else
    echo "✗ cloudflared not found. Install it first:"
    echo "    brew install cloudflared"
    exit 1
  fi
fi

mkdir -p "$AGENTS" "$LOGS"

# launchd hard-codes this path. If the app lives somewhere transient (like an
# Enchanté conversation folder), the agents break silently the moment it moves.
case "$APP_DIR" in
  *"/Enchanté/"*|*"/Conversations/"*|/tmp/*|/var/folders/*|/private/var/folders/*|*/Downloads/*)
    echo "⚠  WARNING: this folder looks temporary:"
    echo "     $APP_DIR"
    echo "   launchd stores absolute paths, so if it's ever moved or cleaned up,"
    echo "   the app will stop restarting and you'll get no error."
    echo "   Strongly recommended: ./relocate.sh   then install from there."
    echo
    if [[ $DRY -eq 0 ]]; then
      read -r -p "   Install anyway? [y/N] " ans
      [[ "$ans" =~ ^[Yy]$ ]] || { echo "Cancelled. Run ./relocate.sh first."; exit 1; }
      echo
    fi
    ;;
esac

echo "  app dir     $APP_DIR"
echo "  python      $PYTHON"
echo "  cloudflared $CLOUDFLARED"
echo "  mode        $MODE"
echo

# ── server agent ────────────────────────────────────────────────────────────
cat > "$AGENTS/$LABEL_SRV.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL_SRV</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$APP_DIR/server.py</string>
    <string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <!-- Without a throttle, a crash-on-start loops ~forever and floods the log.
       10s is launchd's minimum-sane retry window. -->
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/server.log</string>
  <key>StandardErrorPath</key><string>$LOGS/server.err.log</string>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
</dict>
</plist>
PLIST
echo "✓ wrote $LABEL_SRV.plist"

# ── tunnel agent ────────────────────────────────────────────────────────────
if [[ "$MODE" == "named" ]]; then
  if [[ ! -f "$APP_DIR/.tunnel-name" ]]; then
    echo
    echo "✗ No named tunnel configured yet. Run this first:"
    echo "    ./setup-named-tunnel.sh yourdomain.com"
    echo
    echo "  (or re-run without 'named' to use a random quick-tunnel URL)"
    exit 1
  fi
  TUNNEL_NAME="$(cat "$APP_DIR/.tunnel-name")"
  TUN_ARGS="<string>tunnel</string>
    <string>run</string>
    <string>$TUNNEL_NAME</string>"
  echo "✓ named tunnel: $TUNNEL_NAME"
else
  # Quick tunnel: cloudflared prints the URL to stderr. publish-url.sh scrapes
  # it into a file so you can find it without digging through logs.
  TUN_ARGS="<string>tunnel</string>
    <string>--url</string>
    <string>http://localhost:$PORT</string>"
  echo "✓ quick tunnel (URL changes on each restart)"
fi

cat > "$AGENTS/$LABEL_TUN.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL_TUN</string>
  <key>ProgramArguments</key>
  <array>
    <string>$CLOUDFLARED</string>
    $TUN_ARGS
  </array>
  <key>WorkingDirectory</key><string>$APP_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/tunnel.log</string>
  <key>StandardErrorPath</key><string>$LOGS/tunnel.err.log</string>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
</dict>
</plist>
PLIST
echo "✓ wrote $LABEL_TUN.plist"

# ── load them ───────────────────────────────────────────────────────────────
if [[ $DRY -eq 1 ]]; then
  echo
  echo "── dry run: validating generated plists ──"
  for L in "$LABEL_SRV" "$LABEL_TUN"; do
    if plutil -lint "$AGENTS/$L.plist" >/dev/null 2>&1; then
      echo "  ✓ $L.plist is valid XML"
      /usr/libexec/PlistBuddy -c "Print :ProgramArguments" "$AGENTS/$L.plist" 2>/dev/null \
        | tr -d '\n' | sed 's/  */ /g;s/^/      cmd:/' ; echo
    else
      echo "  ✗ $L.plist is INVALID"
      plutil -lint "$AGENTS/$L.plist"
    fi
  done
  echo
  echo "Dry run complete. Nothing was installed."
  echo "Run without --dry-run (in a normal Terminal) to install for real."
  exit 0
fi

# bootout first so re-running this script is idempotent; ignore "not loaded".
for L in "$LABEL_SRV" "$LABEL_TUN"; do
  launchctl bootout "gui/$UID_NUM/$L" 2>/dev/null || true
done
sleep 1
FAILED=0
for L in "$LABEL_SRV" "$LABEL_TUN"; do
  if ! launchctl bootstrap "gui/$UID_NUM" "$AGENTS/$L.plist" 2>/tmp/lcerr.txt; then
    echo "✗ failed to load $L: $(cat /tmp/lcerr.txt)"
    echo "  If this says 'Input/output error', you're likely running inside a"
    echo "  sandboxed shell — run this script from Terminal.app directly."
    FAILED=1
  else
    launchctl enable "gui/$UID_NUM/$L" 2>/dev/null || true
  fi
done
[[ $FAILED -eq 1 ]] && exit 1
echo "✓ agents loaded"

sleep 4
echo
echo "── status ──"
for L in "$LABEL_SRV" "$LABEL_TUN"; do
  if launchctl print "gui/$UID_NUM/$L" >/dev/null 2>&1; then
    PID=$(launchctl print "gui/$UID_NUM/$L" 2>/dev/null | awk '/^\tpid = /{print $3}')
    echo "  $L  pid=${PID:-—}"
  else
    echo "  $L  NOT RUNNING"
  fi
done

echo
if curl -fsS -m 5 "http://localhost:$PORT/api/auth" >/dev/null 2>&1; then
  echo "✓ app responding on http://localhost:$PORT"
else
  echo "✗ app not responding yet — check $LOGS/server.err.log"
fi

if [[ "$MODE" == "named" ]]; then
  echo "✓ your permanent URL: https://$(cat "$APP_DIR/.tunnel-hostname" 2>/dev/null || echo '?')"
else
  echo
  echo "  Finding the quick-tunnel URL (takes a few seconds)..."
  sleep 6
  URL=$(grep -ohE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGS"/tunnel*.log 2>/dev/null | tail -1)
  if [[ -n "$URL" ]]; then
    echo "✓ public URL: $URL"
    echo "$URL" > "$APP_DIR/current-url.txt"
    echo "  (also saved to current-url.txt — it changes on every restart)"
  else
    echo "  Not printed yet. Run ./whats-my-url.sh in a moment."
  fi
fi

echo
echo "Both survive reboots and restart on crash."
echo "  status:  ./agent-status.sh"
echo "  logs:    tail -f $LOGS/server.log"
echo "  remove:  ./uninstall-agents.sh"
