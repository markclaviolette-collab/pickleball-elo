# Deploying to Railway via GitHub

No credentials needed from you. You click through both UIs; I never see a token.

---

## What I need from you: nothing secret

| Thing | Do I need it? | Why |
|---|---|---|
| GitHub token / password | **No** | you push, or use the web uploader |
| Railway API token | **No** | Railway deploys by reading your repo |
| Railway project ID | **No** | created in their dashboard |
| Your generated PINs | **No** | never tell anyone, including me |
| The final public URL | Optional | only if you want me to sanity-check it |
| Error text from a failed deploy | **Yes, if stuck** | paste the log and I'll debug |

**Never paste an API token into a chat** — with me or anyone. If you already
have, revoke it: GitHub → Settings → Developer settings → Tokens; Railway →
Account → Tokens.

The one thing worth sharing afterward is a **deploy log**, if something breaks.
Those are safe (no secrets) and are what I'd need to help.

---

## Step 1 — put the code on GitHub

Run the helper. It prepares a local commit and prints the exact commands to
paste — it makes **no network calls and touches no account**:

```bash
./relocate.sh                        # must be a standalone folder first
cd ~/Applications/PickleballElo
./publish.sh
```

Then follow its two printed steps: create an empty **private** repo at
github.com/new, and paste the `git remote add` / `git push` lines with your
username.

### Why relocate first

The Enchanté conversation folder **is itself a git repo**, and `pickleball` sits
inside it. Committing from there would tangle this app into unrelated history.
`publish.sh` detects that and refuses to run, telling you to relocate.

### What publish.sh protects you from

- **Refuses to run inside another repo** (verified)
- **Refuses to commit if `.gitignore` is missing**
- **Blocks the commit if a `.db`, log, backup, or tunnel URL is staged** — the
  push stops rather than leaking
- **Scans the diff for hard-coded credentials** and asks before continuing

Verified with a database containing a real PIN hash present in the folder:
17 code files committed, and the hash was **absent from all git history**.
`pickleball.db`, `logs/`, `backups/`, `current-url.txt`, and `.tunnel-name` were
correctly ignored.

### Publishing later changes

```bash
./publish.sh "fixed the serve chart"
git push
```

## Step 2 — create the Railway service

1. railway.app → **New Project** → **Deploy from GitHub repo**
2. Authorize Railway, pick the repo
3. It auto-detects Python and starts building — `railway.json` supplies the
   start command and healthcheck

---

## Step 3 — add a volume (do not skip)

**Railway wipes the container filesystem on every deploy.** Without a volume,
every match vanishes the next time you push a change. This is the single most
important step.

In your service: **Settings → Volumes → New Volume**

- Mount path: `/data`

---

## Step 4 — environment variables

Service → **Variables**:

| Variable | Value | Purpose |
|---|---|---|
| `DB_PATH` | `/data/pickleball.db` | put the database on the volume |
| `SETUP_TOKEN` | a long random string | stops a stranger claiming your app |
| `SESSION_SECRET` | another long random string | lets you sign everyone out at will |

Generate the two secrets locally:

```bash
python3 -c "import secrets; print('SETUP_TOKEN   ', secrets.token_urlsafe(24)); print('SESSION_SECRET', secrets.token_urlsafe(32))"
```

Paste those into Railway. Keep `SETUP_TOKEN` handy for step 5, then you can
forget it.

### Why SETUP_TOKEN matters

The first visitor creates the PINs. On a public URL that's a **land-grab risk**:
anyone who finds the link before you could claim your app and lock you out.

With `SETUP_TOKEN` set, the first-run form asks for a setup code. Verified:
attempting setup without it, or with a wrong value, returns **403**.

`PORT` is injected by Railway automatically — don't set it.

---

## Step 5 — claim it

1. Settings → Networking → **Generate Domain** → gives
   `something.up.railway.app`
2. Open it. You'll see the first-run form with a **Setup code** field.
3. Enter your friend PIN, admin PIN, and the `SETUP_TOKEN` value.
4. Done — you're admin. Share the URL and the *friend* PIN only.

Add to Home Screen on iPhone for an app icon.

---

## What changes vs. running on your Mac

| | Your Mac | Railway |
|---|---|---|
| Works when your Mac is asleep | ❌ | ✅ |
| Needs cloudflared / tunnel | ✅ | ❌ |
| Needs launchd agents | ✅ | ❌ |
| Permanent URL | needs a domain | ✅ free subdomain |
| Cost | free | free tier, then ~$5/mo |
| Data location | your disk | Railway's volume |

The launchd agents, `tunnel.sh`, and `relocate.sh` all become irrelevant — they
only exist to solve problems Railway solves for you. If you go this route, run
`./uninstall-agents.sh` first so you don't have two copies running.

---

## Protecting data across deploys

Four layers, because the volume alone isn't enough.

### 1. The volume (necessary, not sufficient)

Railway wipes the container filesystem on every deploy. `DB_PATH=/data/pickleball.db`
pointing at a mounted volume is what survives that.

### 2. The app refuses to start unsafely

If it detects a cloud host (`RAILWAY_ENVIRONMENT` et al.) with **no `DB_PATH`**,
it exits with a fatal error instead of booting. Verified — it prints the fix and
stops. Railway's healthcheck then fails the deploy, so the previous working
version keeps serving and **you get an error rather than an empty app**.

That inverts the dangerous default. Without this check, a missing volume looks
identical to a fresh install: everything green, zero matches.

It also *warns* (not fatal) if `DB_PATH`'s directory shares a filesystem with the
app code, which usually means the mount path doesn't match. That one's a
heuristic — I couldn't verify the positive case locally, and a false positive
shouldn't block a good deploy.

Override with `ALLOW_EPHEMERAL_DB=1` only for a throwaway demo.

### 3. Automatic snapshots

Every startup takes a SQLite snapshot into `/data/backups/`, keeping ~10. Uses
SQLite's backup API, so it's consistent even mid-write and folds in the `-wal`
contents that a plain file copy can miss.

**Retention protects the biggest snapshots, not just the newest.** Testing found
a nasty interaction: a snapshot taken around a destructive restore captures an
*empty* database, and naive newest-first pruning would then evict every good copy
— the backup system becoming a liability exactly when you need it. Verified after
a full wipe plus five restarts: **three snapshots still held all 8 matches.**

Snapshots live next to the database, so they cover a bad deploy or a fat-fingered
delete — **not** losing the volume itself. Which is why:

### 4. Off-site exports (the one that actually saves you)

**Backups** tab → *Download JSON backup*. Works from your phone. Do it after any
session you'd hate to lose, and keep the file somewhere else — iCloud, email to
yourself, anywhere not Railway.

Restoring: same tab, pick the file, enter your admin PIN. Merging is the default,
duplicate matches are skipped, and a snapshot is taken first. "Replace
everything" is opt-in, confirmed twice, and logged.

Verified end-to-end: seeded 5 matches, exported, wiped everything, restored from
the file → **4 players and 5 matches recovered with scores, serve side, and
recorder names intact**. Re-importing the same file twice left 5 matches, not 10.

Export and import are admin-only (friends get 403), and import needs the admin
PIN re-entered even with an admin session.

### The routine

| When | Do |
|---|---|
| Before pushing a code change | nothing — the volume handles it |
| After a session worth keeping | Backups → Download JSON |
| Before a risky change | Backups → Snapshot now, plus a JSON download |
| Monthly | check a JSON export actually opens |

### If data does vanish

1. **Don't push anything else** — more deploys mean more startup snapshots, and
   while retention protects the richest copies, don't spend that margin.
2. Backups tab → check server snapshots. A non-zero match count means recovery
   is on the volume.
3. Restore your most recent JSON export.
4. To recover a `.db` snapshot directly:
   ```bash
   railway run bash -c 'cp /data/backups/pickleball-YYYYMMDD-HHMMSS.db /data/pickleball.db'
   ```
   Then restart the service.

## Environment variables reference

| Variable | Required | Purpose |
|---|:---:|---|
| `DB_PATH` | **yes** | `/data/pickleball.db` — puts data on the volume |
| `SETUP_TOKEN` | **yes** | stops a stranger claiming the app first |
| `SESSION_SECRET` | recommended | change it to sign every device out |
| `BACKUP_KEEP` | no | snapshots to retain (default 10) |
| `ALLOW_EPHEMERAL_DB` | no | `1` disables the volume safety check — don't |
| `PORT` | no | injected by Railway |

## Backups (CLI)

Railway's volume is not a backup. Pull a copy occasionally:

- **In-app**: History tab → *Export JSON* — simplest, works from your phone
- **CLI**: `railway run cat /data/pickleball.db > backup.db`

Do this before any big change. It's one file.

---

## Migrating existing matches up

If you've already recorded games locally, upload `pickleball.db` to the volume
once with the Railway CLI:

```bash
npm i -g @railway/cli
railway login
railway link                       # pick the project
railway run bash -c 'cat > /data/pickleball.db' < pickleball.db
```

Then restart the service. Your PINs come along, so `SETUP_TOKEN` won't be asked
for — the app is already claimed.

---

## Verified before shipping

Simulated Railway's environment locally (`$PORT`, `DB_PATH` on a volume,
`SETUP_TOKEN`, `X-Forwarded-Proto: https`, `RAILWAY_ENVIRONMENT`):

- healthcheck at `/api/health` returns 200 and touches the DB, so a broken
  volume fails the deploy rather than silently going live
- setup without / with a wrong token → **403**; with the right one → **200**
- session cookie gets the `Secure` flag behind the HTTPS proxy
- database written to the volume path, not the container
- **killed and restarted the process — 2 players and 1 match survived**, which
  is the redeploy case that eats data when the volume is missing

Untested: the real Railway build. Nixpacks should detect Python from
`.python-version` and there are no dependencies to install, but if the build
fails, paste me the log.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Deploy fails healthcheck | volume not mounted, or `DB_PATH` doesn't point into it |
| Data gone after a deploy | no volume — add one, restore from a JSON export |
| "Wrong setup code" | `SETUP_TOKEN` mismatch; check for trailing spaces |
| Asked to set PINs again | new empty volume, or `DB_PATH` changed |
| Everyone signed out unexpectedly | `SESSION_SECRET` changed |
