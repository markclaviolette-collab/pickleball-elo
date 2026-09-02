#!/bin/bash
# Remove leftovers from Claude's sandboxed testing. Safe to run; touches
# nothing belonging to the real app.
#
# Two artifacts couldn't be deleted from inside the sandbox:
#   1. ~/Library/LaunchAgents/com.allan.pickleball.TESTONLY.plist
#      A throwaway agent from a load test. It never started (bootstrap was
#      blocked), but it WOULD try to run at your next login. Remove it.
#   2. ~/Applications/_pbtest/
#      A scratch copy of two files used to verify a path warning.
set -uo pipefail
U="$(id -u)"

P="$HOME/Library/LaunchAgents/com.allan.pickleball.TESTONLY.plist"
if [[ -f "$P" ]]; then
  launchctl bootout "gui/$U/com.allan.pickleball.TESTONLY" 2>/dev/null || true
  rm -f "$P" && echo "✓ removed stray test agent"
else
  echo "  no stray test agent (already clean)"
fi

if [[ -d "$HOME/Applications/_pbtest" ]]; then
  rm -rf "$HOME/Applications/_pbtest" && echo "✓ removed ~/Applications/_pbtest"
else
  echo "  no _pbtest scratch dir"
fi

echo
echo "Clean. Remaining pickleball agents (should be none until you install):"
ls "$HOME/Library/LaunchAgents" 2>/dev/null | grep -i pickle || echo "  none"
