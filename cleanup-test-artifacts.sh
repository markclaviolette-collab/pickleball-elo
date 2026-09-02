#!/bin/bash
# Remove leftovers from Claude's sandboxed testing. Safe to run; touches
# nothing belonging to the real app.
#
# Two artifacts couldn't be deleted from inside the sandbox:
#   1. ~/Library/LaunchAgents/com.pickleball.elo.TESTONLY.plist
#      A throwaway agent from a load test. It never started (bootstrap was
#      blocked), but it WOULD try to run at your next login. Remove it.
#   2. ~/Applications/_pbtest/
#      A scratch copy of two files used to verify a path warning.
set -uo pipefail
U="$(id -u)"

# Both names are checked: the plist was created before the labels were
# generalized from com.allan.* to com.pickleball.*, so the file actually on
# disk may use either.
for LABEL in com.allan.pickleball.TESTONLY com.pickleball.elo.TESTONLY; do
  P="$HOME/Library/LaunchAgents/$LABEL.plist"
  if [[ -f "$P" ]]; then
    launchctl bootout "gui/$U/$LABEL" 2>/dev/null || true
    rm -f "$P" && echo "✓ removed stray test agent ($LABEL)"
  fi
done
if ! ls "$HOME/Library/LaunchAgents"/*TESTONLY.plist >/dev/null 2>&1; then
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
