#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Prepare this folder as its own git repo, ready to push to GitHub.
#
#   ./publish.sh
#
# What it does NOT do: touch your GitHub account. No tokens, no passwords, no
# network calls. It only prepares a local commit and prints the exact two
# commands to paste once you've made an empty repo on github.com.
#
# You stay the only one who ever authenticates.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo
echo "── Preparing $(basename "$DIR") for GitHub ──"
echo

# ── 1. refuse to run inside someone else's repo ──────────────────────────────
# The Enchanté conversation folder is itself a git repo. Committing from inside
# it would mix this app into unrelated history, or worse, push files you never
# meant to share.
if [[ ! -d .git ]] && git rev-parse --show-toplevel >/dev/null 2>&1; then
  PARENT="$(git rev-parse --show-toplevel)"
  cat <<WARN
✗ This folder is inside an existing git repo:
    $PARENT

  Publishing from here would tangle the app into that repo's history.

  Fix — copy it somewhere standalone first:
      ./relocate.sh
      cd ~/Applications/PickleballElo
      ./publish.sh

WARN
  exit 1
fi

# ── 2. git identity (local only, so nothing global changes) ─────────────────
if [[ -z "$(git config user.name 2>/dev/null || true)" ]]; then
  echo "Git needs a name and email for commit messages (stored in this repo only)."
  read -r -p "  Your name:  " GN
  read -r -p "  Your email: " GE
  [[ -z "$GN" || -z "$GE" ]] && { echo "✗ Both are required."; exit 1; }
fi

# ── 3. init ─────────────────────────────────────────────────────────────────
if [[ ! -d .git ]]; then
  git init -q
  git branch -M main 2>/dev/null || true
  echo "✓ initialized a new git repo"
else
  echo "✓ already a git repo"
fi
[[ -n "${GN:-}" ]] && git config user.name "$GN"
[[ -n "${GE:-}" ]] && git config user.email "$GE"

# ── 4. safety check: is anything sensitive about to be committed? ────────────
if [[ ! -f .gitignore ]]; then
  echo "✗ .gitignore is missing — refusing to continue, your database could be committed."
  exit 1
fi

git add -A
LEAKS=""
while IFS= read -r f; do
  case "$f" in
    *.db|*.db-wal|*.db-shm|*current-url.txt|*.tunnel-name|*.tunnel-hostname|logs/*|backups/*)
      LEAKS="$LEAKS  $f"$'\n' ;;
  esac
done < <(git diff --cached --name-only)

if [[ -n "$LEAKS" ]]; then
  echo "✗ These would be committed but shouldn't be:"
  printf '%s' "$LEAKS"
  echo "  .gitignore isn't catching them. Stopping so nothing leaks."
  git reset -q
  exit 1
fi
echo "✓ no database or local-state files staged"

# ── 5. show exactly what WILL be published ──────────────────────────────────
echo
echo "── files to publish ──"
git diff --cached --name-only | sed 's/^/  /'
COUNT=$(git diff --cached --name-only | wc -l | tr -d ' ')
echo "  ($COUNT files)"
echo

# Sanity: none of these contain secrets, but scan for obvious mistakes anyway.
if git diff --cached -U0 | grep -inE '(api[_-]?key|secret|password|token)[[:space:]]*[:=][[:space:]]*["'"'"'][a-z0-9]{16,}' >/dev/null 2>&1; then
  echo "⚠  Something looks like a hard-coded credential. Review before pushing:"
  git diff --cached -U0 | grep -inE '(api[_-]?key|secret|password|token)[[:space:]]*[:=]' | head -5 | sed 's/^/    /'
  read -r -p "  Continue anyway? [y/N] " a
  [[ "$a" =~ ^[Yy]$ ]] || { git reset -q; exit 1; }
fi

# ── 6. commit ───────────────────────────────────────────────────────────────
if git rev-parse HEAD >/dev/null 2>&1; then
  MSG="${1:-Update Pickleball Elo}"
else
  MSG="${1:-Pickleball Elo: shared ratings app}"
fi

if git diff --cached --quiet; then
  echo "✓ nothing new to commit — already up to date"
else
  git commit -q -m "$MSG"
  echo "✓ committed: $MSG"
fi

# ── 7. next steps ───────────────────────────────────────────────────────────
if git remote get-url origin >/dev/null 2>&1; then
  REMOTE="$(git remote get-url origin)"
  cat <<NEXT

── Ready to push ──

  Remote already set: $REMOTE

  Push with:
      git push

NEXT
else
  cat <<NEXT

── Almost done: 2 steps left ──

STEP 1 — create an empty repo on GitHub (30 seconds, in your browser)

  1. Go to  https://github.com/new
  2. Repository name:  pickleball-elo
  3. Choose  ● Private
  4. Do NOT tick "Add a README", ".gitignore", or a license —
     leave it completely empty, or the first push will conflict
  5. Click  Create repository

STEP 2 — connect and push (paste these, with YOUR username)

      git remote add origin https://github.com/YOUR-USERNAME/pickleball-elo.git
      git push -u origin main

  GitHub will ask to authenticate in your browser, or prompt for a
  Personal Access Token as the password. Either way, YOU authenticate —
  the credential never leaves your machine and nobody else ever sees it.

  If it asks for a token: github.com → Settings → Developer settings →
  Personal access tokens → Fine-grained → only needs "Contents: Read and write"
  on this one repo.

Then continue with DEPLOY.md for the Railway side.

NEXT
fi

echo "Later, to publish a change:  ./publish.sh \"what changed\"  &&  git push"
echo
