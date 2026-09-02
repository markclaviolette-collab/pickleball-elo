#!/bin/bash
# Expose the local Pickleball Elo server on a public HTTPS URL via Cloudflare.
#
# Free, no account, no router config, no port forwarding. Cloudflare terminates
# TLS, so the PIN is encrypted in transit — which is the whole reason we tunnel
# instead of forwarding a port.
#
# Usage:  ./tunnel.sh [port]        (default 8777)

set -euo pipefail
PORT="${1:-8777}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared isn't installed. Installing via Homebrew..."
  brew install cloudflared
fi

if ! curl -fsS "http://localhost:${PORT}/api/auth" >/dev/null 2>&1; then
  echo
  echo "  The app doesn't seem to be running on port ${PORT}."
  echo "  Start it first, in another terminal:   python3 server.py ${PORT}"
  echo
  exit 1
fi

cat <<BANNER

  Opening a public HTTPS tunnel to localhost:${PORT}...

  Cloudflare will print a URL like https://something-random.trycloudflare.com
  Share that with your friends — it works from anywhere, on cell data.

  Notes:
    * The URL changes every time you restart this script. For a permanent
      address you need a Cloudflare account + domain (see README).
    * The tunnel only lives as long as this terminal stays open.
    * Anyone with the URL still needs the PIN.

BANNER

exec cloudflared tunnel --url "http://localhost:${PORT}"
