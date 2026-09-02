#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Diagnose a GitHub token and, if it works, store it so `git push` succeeds.
#
#   ./fix-github-auth.sh
#
# The token is read with hidden input, never echoed, never written to a file,
# and never added to your shell history. It goes only to api.github.com and
# (if valid) your macOS keychain.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

USER_NAME="${1:-allanlaviolette-11}"
REPO="${2:-pickleball-elo}"

echo
echo "── GitHub token check ──"
echo "  account: $USER_NAME"
echo "  repo:    $REPO"
echo
echo "Paste your token and press Return. It will NOT appear as you type —"
echo "that's normal, keep going."
echo
printf "  Token: "
read -rs TOKEN
echo
echo

if [[ -z "$TOKEN" ]]; then
  echo "✗ Nothing was received."
  echo
  echo "  The paste didn't land. Try:"
  echo "    • Cmd-V (not right-click) in Terminal"
  echo "    • Re-copy the token from GitHub — it's only shown once,"
  echo "      so if you navigated away you'll need to generate a new one"
  exit 1
fi

# --- shape check: catch the classic mistakes before hitting the network ---
LEN=${#TOKEN}
echo "  received $LEN characters"
case "$TOKEN" in
  github_pat_*) echo "  looks like: fine-grained token ✓" ;;
  ghp_*)        echo "  looks like: classic token ✓" ;;
  gho_*|ghu_*)  echo "  looks like: OAuth token (unusual here, but may work)" ;;
  *)
    echo "  ⚠  This doesn't start with github_pat_ or ghp_."
    echo "     That usually means it's your PASSWORD, not a token —"
    echo "     GitHub rejects passwords for git operations."
    echo
    read -r -p "  Test it anyway? [y/N] " a
    [[ "$a" =~ ^[Yy]$ ]] || exit 1
    ;;
esac
echo

# --- 1. is the token valid at all? ---
CODE=$(curl -s -o /tmp/ghwho.json -w '%{http_code}' -m 15 \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/user)

if [[ "$CODE" == "401" ]]; then
  echo "✗ GitHub says this token is invalid or expired (401)."
  echo
  echo "  Generate a fresh one:"
  echo "    https://github.com/settings/personal-access-tokens/new"
  echo "  Make sure you click 'Generate token' at the bottom and copy the"
  echo "  result — a token you only viewed the settings page for won't exist."
  rm -f /tmp/ghwho.json
  exit 1
elif [[ "$CODE" != "200" ]]; then
  echo "✗ Unexpected response from GitHub: HTTP $CODE"
  head -c 300 /tmp/ghwho.json 2>/dev/null | sed 's/^/    /'
  rm -f /tmp/ghwho.json
  exit 1
fi

WHO=$(python3 -c "import json;print(json.load(open('/tmp/ghwho.json'))['login'])" 2>/dev/null)
rm -f /tmp/ghwho.json
echo "✓ token is valid — belongs to: $WHO"

if [[ "$WHO" != "$USER_NAME" ]]; then
  echo
  echo "⚠  MISMATCH: the token belongs to '$WHO' but the remote uses '$USER_NAME'."
  echo "   Fix the remote to match:"
  echo "     git remote set-url origin https://github.com/$WHO/$REPO.git"
  echo
fi

# --- 2. can it actually WRITE to this repo? ---
# This is the check that catches a token missing "Contents: Read and write",
# which fails identically to a bad password during git push.
CODE=$(curl -s -o /tmp/ghrepo.json -w '%{http_code}' -m 15 \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "https://api.github.com/repos/$WHO/$REPO")

if [[ "$CODE" != "200" ]]; then
  echo "✗ Token can't see $WHO/$REPO (HTTP $CODE)."
  echo "  For a fine-grained token, set Repository access to include this repo."
  rm -f /tmp/ghrepo.json
  exit 1
fi

PUSH=$(python3 -c "
import json;d=json.load(open('/tmp/ghrepo.json'))
print(d.get('permissions',{}).get('push'))" 2>/dev/null)
rm -f /tmp/ghrepo.json

if [[ "$PUSH" != "True" ]]; then
  cat <<PERM
✗ Token can READ the repo but not WRITE to it.

  This is the most common cause of the error you saw. Fix:
    1. https://github.com/settings/tokens  →  open this token
    2. Repository permissions  →  Contents  →  "Read and write"
    3. Save, then run this script again

  (A fine-grained token defaults Contents to "Read-only" or none —
   it must be explicitly set.)
PERM
  exit 1
fi
echo "✓ token has write access to $WHO/$REPO"

# --- 3. store it so git stops asking ---
printf "protocol=https\nhost=github.com\n\n" | git credential-osxkeychain erase 2>/dev/null
printf "protocol=https\nhost=github.com\nusername=%s\npassword=%s\n\n" \
  "$WHO" "$TOKEN" | git credential-osxkeychain store
unset TOKEN
echo "✓ saved to your keychain — git won't ask again"

cat <<DONE

── Now push ──

    cd ~/Applications/PickleballElo
    git push -u origin main

DONE
