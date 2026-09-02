#!/bin/bash
# Print the current public URL. Useful in quick-tunnel mode, where the
# URL changes on every restart.
set -uo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGS="$APP_DIR/logs"

if [[ -f "$APP_DIR/.tunnel-hostname" ]]; then
  echo "https://$(cat "$APP_DIR/.tunnel-hostname")"
  exit 0
fi

# cloudflared prints the URL to stderr once at startup, so take the newest.
URL=$(grep -ohE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGS"/tunnel*.log 2>/dev/null | tail -1)
if [[ -n "$URL" ]]; then
  echo "$URL"
  echo "$URL" > "$APP_DIR/current-url.txt"
  command -v pbcopy >/dev/null && echo -n "$URL" | pbcopy && echo "(copied to clipboard)" >&2
else
  echo "No URL found. Is the tunnel running?  ./agent-status.sh" >&2
  exit 1
fi
