# Pickleball Elo

Shared Elo ratings for your pickleball group. Singles and doubles, tennis-style
rating math, and a serve-first correlation analysis.

## Running it

```bash
cd pickleball
python3 server.py
```

Then open the URL it prints. No `pip install`, no dependencies — Python 3
standard library only.

**First visitor creates both PINs.** Whoever loads the page first sets a
*friend PIN* and an *admin PIN*; after that everyone else just enters one.
Share the friend PIN separately from the link — text it, don't put it in the
same message. Keep the admin PIN to yourself.

Run on a different port: `python3 server.py 9000`

## Two PINs, two roles

| | Friend PIN | Admin PIN |
|---|:---:|:---:|
| Record matches | ✅ | ✅ |
| Add players | ✅ | ✅ |
| View rankings, serve analysis, activity log | ✅ | ✅ |
| **Delete a match** | ❌ | ✅ |
| **Delete a player** | ❌ | ✅ |
| Change margin-of-victory setting | ❌ | ✅ |
| Change either PIN | ❌ | ✅ |

Friends see no delete buttons at all — but the enforcement is server-side, not
just hidden UI. Verified: a friend session gets **403** on every delete,
settings, and PIN endpoint, and seven different cookie-forgery attempts (role
swapped in the payload, admin signature reattached, `role=root`, trailing
whitespace, legacy tokens) all returned **401**. The role lives inside the
HMAC-signed payload, so editing the cookie invalidates the signature.

To promote a device, use *How it works* → **Sign in as admin**.

### Recorded by is mandatory

Since deletion is admin-only, the `recorded by` field is the only attribution on
a score entry — so the server now rejects a match without one (minimum 2
characters, whitespace-only refused). It's self-reported and unverified, so treat
it as a label, not proof.

### Activity log

A new **Activity** tab shows the last 50 events — every match added, every
deletion (with the full score and who had entered it), and every PIN change.
Deletions are logged *before* the row is removed, so the record survives the
thing it describes.

## Reachable from anywhere (phones, cell data)

One-time install:

```bash
brew install cloudflared
```

Then, in a second terminal with the server already running:

```bash
./tunnel.sh
```

Cloudflare prints a public URL like `https://random-words.trycloudflare.com`.
Share it and your friends can record matches from their phones on cell data,
anywhere. On iPhone, Safari → Share → **Add to Home Screen** gives it an app
icon and a full-screen, chrome-free view.

Free, no account, no port forwarding, no router config, and **HTTPS is
terminated by Cloudflare** — which is the reason to tunnel rather than forward a
port. The PIN is encrypted in transit; over plain HTTP it wouldn't be. The app
detects this and shows a warning banner if you ever load it over a
non-HTTPS public address.

Two caveats worth knowing up front:

- **The URL changes every restart.** Free quick-tunnels are ephemeral. For a
  permanent address you need a (free) Cloudflare account plus a domain — say the
  word and I'll set up a named tunnel.
- **The tunnel lives only as long as that terminal.** Close it and the public
  link dies. The app keeps working on your LAN.

## Security

Designed on the assumption the URL *will* leak — a public link shared in a group
chat always does eventually.

| Measure | Detail |
|---|---|
| PIN storage | scrypt (n=2¹⁴, r=8, p=1), separate random 16-byte salt per PIN. Never stored in plaintext — verified. |
| PIN comparison | `hmac.compare_digest`, constant-time. Both hashes always evaluated so timing doesn't leak which PIN matched. |
| Sessions | HMAC-SHA256 signed tokens carrying the role, HttpOnly cookie, 30-day expiry. The PIN is never re-sent after login. |
| Privilege separation | Role lives inside the signed payload — 7 forgery/escalation attempts all rejected 401 |
| Cookie flags | `Secure` added automatically when served over HTTPS; omitted on LAN so local use still works |
| Brute-force | Per-IP exponential backoff: 5 free attempts, then 5s doubling to a 1-hour cap |
| Weak PINs | Repeated digits, common sequences, and admin==friend all rejected at setup |
| Proxy awareness | Rate limits bucket on `CF-Connecting-IP`, not the localhost socket peer |
| Admin PIN change | Rotates the signing secret, invalidating every existing session |
| Audit trail | Every add, deletion, and PIN change logged with a timestamp |

### Why 8 digits, not 4

Measured against the actual implementation:

| PIN length | Single attacker | 1,000-IP botnet |
|---|---:|---:|
| 6 digits | 57 years | **~5 weeks** |
| 8 digits | 5,708 years | 5.7 years |

Backoff is per-IP, so a distributed attack divides those numbers by the number
of addresses. That's why length still matters even with lockout — the app
enforces a 6-digit minimum but the setup screen asks for **8+**, and that's the
number I'd use for a public URL.

For reference, with no lockout at all a 6-digit PIN falls in **2.8 hours**. The
rate limiting is doing most of the work here, not the PIN itself.

### What this does *not* protect against

- **Friends share one PIN among themselves.** Any friend can enter a match under
  any name, so `recorded by` is a label rather than proof. Deletions are now
  admin-gated, which is the part that actually mattered.
- **A leaked admin PIN is a full compromise.** Rotate it in *How it works* →
  Change a PIN, which signs every device out.
- **An admin can still delete anything** — but the activity log records what was
  removed, including who had entered it.

## Always-on: launchd agents + permanent URL

Two agents keep everything running: one for the app, one for the tunnel. Both
start at login and restart themselves if they crash.

### Step 0 — move somewhere permanent (important)

```bash
./relocate.sh                 # → ~/Applications/PickleballElo
```

launchd stores **absolute paths**. This folder currently lives inside an
Enchanté conversation directory — if that ever gets moved, renamed, or cleaned
up, the agents keep pointing at nothing and the app silently stops coming back
after a reboot, with no error anywhere. `relocate.sh` copies the code and
SQLite-backups the database to a stable location. The installer refuses to run
from a temp-looking path without an explicit override.

### Step 1 — install cloudflared

```bash
brew install cloudflared
```

### Step 2a — quick tunnel (no account, random URL)

```bash
cd ~/Applications/PickleballElo
./install-agents.sh
./whats-my-url.sh            # prints + copies the current URL
```

Works immediately, but the URL changes every time the tunnel restarts — so
after a reboot you have to send your friends a new link. Fine for a trial, poor
for a standing group.

### Step 2b — named tunnel (permanent URL)

```bash
./setup-named-tunnel.sh yourdomain.com
./install-agents.sh named
```

Gives you `https://pickleball.yourdomain.com`, permanently. Bookmark it, share
it once, done.

**This requires a domain on Cloudflare's nameservers**, and there's no way
around that: a stable hostname has to live in a DNS zone you control.
Cloudflare's `trycloudflare.com` names are deliberately ephemeral. A `.com` from
Cloudflare Registrar is ~$10/yr at cost, then *Add site* in the dashboard and
switch your nameservers.

`setup-named-tunnel.sh` handles the rest — browser auth, tunnel creation, the
`config.yml`, and the DNS CNAME. It's idempotent, so re-running is safe.

### Managing them

```bash
./agent-status.sh          # pids, crash-loop detection, URL reachability, DB stats
./whats-my-url.sh          # current public URL
./uninstall-agents.sh      # remove agents (never touches pickleball.db)
tail -f logs/server.log    # live log
```

`agent-status.sh` flags a restart count above 5, which is how you catch a
crash-loop that `launchctl` otherwise reports as simply "running".

### What's in the plists, and why

| Key | Value | Reason |
|---|---|---|
| `RunAtLoad` | true | starts at login |
| `KeepAlive` | server: `SuccessfulExit=false`<br>tunnel: `true` | restart on crash; the tunnel should always be up, while a clean server exit (you stopping it) is respected |
| `ThrottleInterval` | 10s / 15s | without it, a crash-on-start loops as fast as the CPU allows and floods the log |
| `ProgramArguments` | absolute paths from `sys.executable` | launchd has no `PATH`; a bare `python3` silently never starts |
| `ProcessType` | Background | correct scheduling priority |
| `StandardOut/ErrorPath` | `logs/` | otherwise crashes are invisible |

### If you're on a laptop

Agents can't run while the Mac is asleep, so the app is unreachable with the lid
shut regardless of any of this. Options: keep it plugged in with
`caffeinate -s`, adjust Energy Saver, or move the app to an always-on box — a
Raspberry Pi or spare Mac mini runs this fine, since it's just stdlib Python.

## Where the data lives — and why closing a terminal is safe

Everything is in **`pickleball.db`** — one SQLite file next to the server. Every
match is written to disk the moment someone taps Save.

**Closing the tunnel does not lose data.** The tunnel is only a doorway; it
carries no state. Closing it removes the public *route* — your friends' link
stops working and you'll need to send them the new one — but the database is
untouched. Stopping the server likewise just makes the app unreachable; the file
is still sitting there, and restarting picks up exactly where you left off,
PINs included.

The only way to lose data is deleting `pickleball.db` yourself.

| Action | Data | Public URL |
|---|---|---|
| Close tunnel terminal | ✅ safe | ❌ dies, changes on restart |
| Stop the server (Ctrl-C) | ✅ safe | ❌ unreachable until restarted |
| Reboot your Mac | ✅ safe | ❌ both need restarting |
| Delete `pickleball.db` | ❌ gone | — |

Copy that one file to back it up, or drop it in Dropbox/iCloud to sync between
machines. WAL mode is on, so several people writing at once won't lock each
other out — verified with 16 simultaneous adds and deletes, zero errors.

`Export JSON` in the History tab gives you a plain-text snapshot too.

### Leaving it always-on

See **Always-on: launchd agents + permanent URL** above — that's the automated
route. Manual alternatives:

1. **Leave your Mac awake** with both terminals open — no setup, no persistence.
2. **A small always-on box** (Raspberry Pi, spare Mac mini) — stdlib Python only,
   so it runs anywhere.
3. **A VPS** with a named tunnel, if you want it independent of your home
   network entirely.


## The rating math

Modeled on tennis Elo (the FiveThirtyEight approach). Everyone starts at 1500.

**Expected result** — standard logistic curve:

```
E_A = 1 / (1 + 10^((R_B - R_A)/400))
```

A 100-point rating edge is a 64.0% favorite.

**Dynamic K-factor** — the piece specifically borrowed from tennis Elo:

```
K = 250 / (matches + 5)^0.4
```

| Matches played | K    | Typical swing |
|---------------:|-----:|--------------:|
| 0              | 131  | very fast     |
| 5              | 100  | fast          |
| 30             | 60   | settling      |
| 100            | 39   | stable        |

New players converge on their real level in a few sessions; veterans stop
bouncing around. A fixed K (chess-style) would take your group a season to sort
itself out.

**Doubles** — a team's rating is the *average* of its two players. Both partners
face the same expected result, but each updates with their **own** K. Verified:
a debutant paired with a 60-match veteran moves **2.79×** as much as the veteran
does. Updates are exactly zero-sum, so no rating is created or destroyed.

**Margin of victory** — pickleball scores carry information that a binary
win/loss discards:

```
m = 0.75 + 0.5 × (pointDiff / winnerScore),  clamped to [0.75, 1.25]
```

11–9 → ×0.84 · 11–5 → ×1.02 · 11–0 → ×1.25. Toggleable in *How it works*
(it's a shared setting — changing it affects everyone). This is an addition, not
tennis-Elo canon, which is set-based.

**Verified by simulation:** 400 singles matches among five players with known
true ratings (1800/1650/1500/1350/1200) recovered the correct ranking order,
with final Elos within ~50 points of ground truth.

## Serve-first analysis

Every match records which side served first. That is deliberately **excluded
from the rating formula** — testing whether serving first matters requires the
ratings stay independent of it, otherwise you'd be measuring your own assumption.

The **Serve Analysis** tab reports:

- **Serve-first win rate** — raw share of matches won by the side that served.
- **Expected from Elo alone** — what that rate should be given who was serving.
  This is the important control: if your stronger players happen to serve first
  more often, the raw rate looks like a serve advantage when it's just skill.
- **Serve edge** — the difference. This is the actual signal.
- **Two-proportion z-test** against the 50% null, with a plain-language verdict.
- **Per-player splits** — win rate serving first vs. receiving first.

### How many matches you need

Verified against the z-test implementation:

| Result   | Matches | p-value | Verdict         |
|----------|--------:|--------:|-----------------|
| 60%      | 10      | 0.53    | not significant |
| 60%      | 30      | 0.27    | not significant |
| **60%**  | **100** | **0.046** | **significant** |
| 55%      | 100     | 0.32    | not significant |
| 60%      | 200     | 0.005   | significant     |

Margin of error is ±17.9 points at 30 matches, ±9.8 at 100. Below 40 matches
the app says "too early to call" rather than showing you a number that looks
meaningful but isn't.

**A real caveat found in testing.** I seeded 122 synthetic matches with a
deliberate **+10pp** serve advantage baked in. The raw serve-first rate measured
only **54.1%**, and the Elo-controlled edge just **+1.8pp** — both well short of
the +10pp truth. The cause isn't a bug: when the serving side is already a 90%
favorite, a +10pp boost gets clipped by the 0–100% ceiling, so lopsided
matchups dilute the measured effect.

Practical consequence: **this analysis under-reports the serve advantage when
games are mismatched.** It's most trustworthy on evenly-matched games. If the
edge reads small but consistently positive across a few hundred matches, the
true effect is probably larger than the number shown. If you want, I can add a
version that restricts the test to games between closely-rated sides — fewer
matches counted, but a much cleaner estimate.

## Limitations

- **Two shared PINs, no individual accounts.** `recorded by` is unverified. See
  Security above.
- **Free tunnel URLs are ephemeral** — they change on every restart. Data is
  unaffected.
- **Deletes recalculate everything.** Removing a match or player rewrites all
  ratings from the full log. That's intentional — no phantom points — but it
  means one bad entry silently shifts everyone until an admin removes it.
- **Serve advantage is under-measured on mismatched games**, as above.
- Player names are the identity key, so renaming isn't supported yet.

## Files

| File | Purpose |
|------|---------|
| `server.py` | HTTP server, SQLite storage, two-role PIN auth, rate limiting, audit log |
| `index.html` | Entire frontend — no build step, no framework |
| `tunnel.sh` | Opens the public HTTPS tunnel |
| `pickleball.db` | Your data + PIN hashes (created on first run) |
| `verify_math.py` | Math/statistics test suite — run `python3 verify_math.py` |
