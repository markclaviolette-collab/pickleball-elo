#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Move the app somewhere permanent before installing agents.
#
#   ./relocate.sh                       # -> ~/Applications/PickleballElo
#   ./relocate.sh /path/you/prefer
#
# Why this exists: this folder currently lives inside an Enchanté conversation
# directory. launchd agents hard-code absolute paths, so if that folder is ever
# moved, renamed, or cleaned up, the agents keep pointing at nothing and the app
# silently stops coming back after a reboot. Copy it somewhere stable first.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-$HOME/Applications/PickleballElo}"

if [[ "$SRC" == "$DEST" ]]; then
  echo "Already living at $DEST — nothing to do."
  exit 0
fi

echo "  from  $SRC"
echo "  to    $DEST"
echo

if [[ -e "$DEST" ]]; then
  echo "⚠ $DEST already exists."
  read -r -p "  Overwrite the app files there? Your pickleball.db will be kept. [y/N] " a
  [[ "$a" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 1; }
fi

mkdir -p "$DEST"

# Copy every app file. Globbing rather than a hardcoded list, because a fixed
# list silently drops files added later (publish.sh went missing exactly that way).
shopt -s nullglob
for f in "$SRC"/*.py "$SRC"/*.html "$SRC"/*.sh "$SRC"/*.md "$SRC"/*.json \
         "$SRC"/.gitignore "$SRC"/.python-version; do
  base="$(basename "$f")"
  case "$base" in
    _*) continue ;;                       # skip test scratch files
  esac
  cp "$f" "$DEST/$base"
done
shopt -u nullglob
chmod +x "$DEST"/*.sh 2>/dev/null || true

if [[ -f "$SRC/pickleball.db" && ! -f "$DEST/pickleball.db" ]]; then
  # Use SQLite's own backup so we get a consistent copy even mid-write,
  # and so the -wal/-shm sidecars are folded in correctly.
  if sqlite3 "$SRC/pickleball.db" ".backup '$DEST/pickleball.db'" 2>/dev/null; then
    echo "✓ database copied (consistent SQLite backup)"
  else
    cp "$SRC/pickleball.db" "$DEST/pickleball.db"
    echo "✓ database copied"
  fi
elif [[ -f "$DEST/pickleball.db" ]]; then
  echo "✓ kept the existing database at the destination"
fi

# Carry over tunnel config if present
for f in .tunnel-name .tunnel-hostname; do
  [[ -f "$SRC/$f" ]] && cp "$SRC/$f" "$DEST/$f"
done

mkdir -p "$DEST/logs"

cat <<DONE

✓ Relocated to $DEST

Next:
    cd "$DEST"
    ./install-agents.sh            # or: ./install-agents.sh named

The original folder is untouched — delete it once you've confirmed the new
location works. If agents were already installed from the old path, re-run
install-agents.sh from the new one; it replaces them.
DONE
