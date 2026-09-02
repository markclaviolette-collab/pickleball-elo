#!/bin/bash
# Remove the launchd agents. Your data (pickleball.db) is NOT touched.
set -uo pipefail
U="$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"

for L in com.allan.pickleball.server com.allan.pickleball.tunnel; do
  if launchctl print "gui/$U/$L" >/dev/null 2>&1; then
    launchctl bootout "gui/$U/$L" 2>/dev/null && echo "✓ stopped $L"
  else
    echo "  $L wasn't running"
  fi
  if [[ -f "$AGENTS/$L.plist" ]]; then
    rm -f "$AGENTS/$L.plist" && echo "✓ removed $L.plist"
  fi
done

echo
echo "Agents removed. pickleball.db is untouched — your matches are safe."
echo "Run it manually any time with:  python3 server.py"
