#!/usr/bin/env python3
"""
Pickleball Elo — shared backend with PIN auth.

Single-file server on the Python standard library. No pip install.
State lives in one portable SQLite file (pickleball.db).

Security model (designed for a public HTTPS tunnel, not just LAN):
  * PIN is never stored — only a per-install scrypt hash + random salt.
  * Sessions are HMAC-signed tokens in HttpOnly cookies; no PIN is
    re-sent after login, and cookies are Secure when served over HTTPS.
  * Failed attempts are rate-limited per IP with exponential backoff,
    which is what actually makes a 6-digit PIN safe on a public URL.
  * PIN comparison is constant-time (hmac.compare_digest).

Run:  python3 server.py
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import socket
import sys
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).parent.resolve()
# Railway (and most hosts) mount a persistent volume and inject $PORT.
# DB_PATH lets the database live on that volume instead of inside the
# container filesystem — containers are wiped on every deploy, so a DB
# written next to the code would silently lose every match on redeploy.
DB = Path(os.environ.get("DB_PATH") or (ROOT / "pickleball.db"))
DB.parent.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8777))

SESSION_DAYS = 30
MIN_PIN, MAX_PIN = 6, 12          # 6+ digits: 1M combos, and lockout does the rest
LOCK_AFTER = 5                    # free attempts before backoff begins
LOCK_BASE = 5                     # first lockout, seconds
MAX_LOCK = 3600                   # cap backoff at 1 hour

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  format TEXT NOT NULL CHECK (format IN ('singles','doubles')),
  a1 TEXT NOT NULL, a2 TEXT, b1 TEXT NOT NULL, b2 TEXT,
  score_a INTEGER NOT NULL, score_b INTEGER NOT NULL,
  serve TEXT NOT NULL CHECK (serve IN ('a','b')),
  recorder TEXT, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (
  ip TEXT PRIMARY KEY, fails INTEGER NOT NULL DEFAULT 0, last REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  action TEXT NOT NULL,
  detail TEXT,
  ip TEXT
);
CREATE INDEX IF NOT EXISTS idx_matches_order ON matches(date, id);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit(at DESC);
"""


def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def cfg(key, default=None):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_cfg(key, value):
    with db() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def init():
    with db() as c:
        c.executescript(SCHEMA)
    if cfg("use_mov") is None:
        set_cfg("use_mov", "true")
    if cfg("secret") is None:                 # HMAC key for signing sessions
        set_cfg("secret", secrets.token_hex(32))


def signing_secret():
    """Env var wins so you can rotate every session by changing a Railway
    variable — useful if a device is lost. Falls back to the DB-stored value."""
    return os.environ.get("SESSION_SECRET") or cfg("secret")


def volume_check():
    """Refuse to start on a host if the DB isn't on a mounted volume.

    This is the single most destructive misconfiguration: without a volume the
    container filesystem is wiped on every deploy, the app comes up looking
    perfectly healthy with an empty database, and every match is gone. Better to
    fail the deploy loudly than to serve a blank slate.

    Set ALLOW_EPHEMERAL_DB=1 to override (only sensible for a throwaway demo).
    """
    on_host = any(os.environ.get(k) for k in
                  ("RAILWAY_ENVIRONMENT", "RENDER", "DYNO", "FLY_APP_NAME"))
    if not on_host or os.environ.get("ALLOW_EPHEMERAL_DB") == "1":
        return
    if not os.environ.get("DB_PATH"):
        sys.exit(
            "\nFATAL: running on a cloud host with no DB_PATH set.\n"
            "  Your database would live in the container and be DELETED on the\n"
            "  next deploy. Fix:\n"
            "    1. Add a volume mounted at /data\n"
            "    2. Set DB_PATH=/data/pickleball.db\n"
            "  Override (loses data on deploy): ALLOW_EPHEMERAL_DB=1\n")
    # Heuristic: a mounted volume is normally a separate device from the
    # container root. This is a WARNING, not fatal — I couldn't verify the
    # positive case (a real mount) during development, and a false positive
    # here would block a perfectly good deploy. The DB_PATH check above is the
    # one that actually catches the common mistake.
    try:
        if os.stat(DB.parent).st_dev == os.stat(ROOT).st_dev:
            print(f"\n  ⚠  WARNING: {DB.parent} looks like it's on the same"
                  f" filesystem as\n     the app code, which would mean the volume"
                  f" isn't mounted there and\n     data is lost on the next deploy."
                  f"\n     Verify: Railway → Volumes → mount path matches"
                  f" {DB.parent}\n     If matches survive a redeploy, ignore this.\n")
    except OSError:
        pass


def auto_backup(reason="startup"):
    """Snapshot the DB, keeping the last BACKUP_KEEP copies.

    Guards against the failure a volume can't: a bad deploy, a fat-fingered
    admin delete, or corruption.

    Retention deliberately protects the largest snapshots rather than simply the
    newest. Testing found that snapshotting around a destructive restore stored
    an EMPTY database, and pure newest-first pruning would then evict the good
    copies — turning the backup system into a liability at the exact moment you
    need it. So we always keep the biggest few (most matches) alongside the
    most recent few.
    """
    if not DB.exists() or DB.stat().st_size == 0:
        return
    keep = max(2, int(os.environ.get("BACKUP_KEEP") or 10))
    bdir = DB.parent / "backups"
    try:
        bdir.mkdir(exist_ok=True)
        dest = bdir / f"pickleball-{time.strftime('%Y%m%d-%H%M%S')}.db"
        if dest.exists():                      # same-second double call
            dest = bdir / f"pickleball-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}.db"
        # SQLite's own backup API — consistent even mid-write, and it folds in
        # the -wal contents (a plain file copy can miss them).
        src = sqlite3.connect(DB)
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()

        # Count rows in each snapshot so pruning is content-aware.
        snaps = []
        for f in bdir.glob("pickleball-*.db"):
            try:
                c = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
                n = c.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
                c.close()
            except sqlite3.Error:
                n = -1                          # unreadable → prune first
            snaps.append((f, n, f.stat().st_mtime))

        newest = {s[0] for s in sorted(snaps, key=lambda s: s[2], reverse=True)[:keep // 2]}
        richest = {s[0] for s in sorted(snaps, key=lambda s: s[1], reverse=True)[:keep // 2]}
        protect = newest | richest
        for f, n, _ in snaps:
            if f not in protect:
                f.unlink(missing_ok=True)

        rows = next((n for f, n, _ in snaps if f == dest), 0)
        print(f"  Backup     {dest.name}  ({rows} matches, {len(protect)} kept, {reason})")
    except (OSError, sqlite3.Error) as e:
        # Never let a backup problem stop the app from serving.
        print(f"  Backup     failed ({e}) — continuing")


# ---------------- PIN ----------------
# Two roles:
#   'friend' — enter matches, add players. What everyone gets.
#   'admin'  — everything above, plus deleting matches/players and settings.
# Stored as separate scrypt hashes so knowing one tells you nothing about the other.
def hash_pin(pin, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt.hex() + "$" + h.hex()


def _verify(pin, stored):
    if not stored or not pin:
        return False
    salt_hex, want = stored.split("$")
    got = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt_hex),
                         n=2**14, r=8, p=1, dklen=32).hex()
    return hmac.compare_digest(got, want)


def check_pin(pin):
    """Returns the role this PIN unlocks, or None.

    Admin is checked first, and both hashes are always evaluated so the
    response time doesn't leak which PIN was entered."""
    admin_ok = _verify(pin, cfg("admin_pin_hash"))
    friend_ok = _verify(pin, cfg("pin_hash"))
    if admin_ok:
        return "admin"
    if friend_ok:
        return "friend"
    return None


def pin_is_set():
    return cfg("pin_hash") is not None


def admin_is_set():
    return cfg("admin_pin_hash") is not None


def audit(action, detail=None, ip=None):
    with db() as c:
        c.execute("INSERT INTO audit(at,action,detail,ip) VALUES(?,?,?,?)",
                  (time.time(), action, detail, ip))


# ---------------- sessions ----------------
def make_token(role="friend"):
    exp = int(time.time()) + SESSION_DAYS * 86400
    body = f"{exp}:{role}"
    sig = hmac.new(signing_secret().encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def token_role(tok):
    """Returns 'admin' / 'friend' / None. The role is inside the signed
    payload, so a friend can't self-promote by editing the cookie."""
    if not tok or "." not in tok:
        return None
    body, sig = tok.rsplit(".", 1)
    want = hmac.new(signing_secret().encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, want):
        return None
    try:
        exp, _, role = body.partition(":")
        if int(exp) <= time.time():
            return None
        return role if role in ("admin", "friend") else None
    except ValueError:
        return None


# ---------------- rate limiting ----------------
def lock_left(ip):
    """Exponential backoff. This is the real defense — it turns a 1M-combo
    PIN space into something no attacker can walk through.

    Note: attempts made *during* a lockout still increment the counter (see
    note_fail call in the login handler). Without that, the delay would freeze
    at its first value and an attacker could simply retry forever on a fixed
    interval — which is exactly the bug this comment exists to prevent
    regressing."""
    with db() as c:
        r = c.execute("SELECT fails,last FROM attempts WHERE ip=?", (ip,)).fetchone()
    if not r or r["fails"] < LOCK_AFTER:
        return 0
    delay = min(MAX_LOCK, LOCK_BASE * 2 ** (r["fails"] - LOCK_AFTER))
    return max(0, int(r["last"] + delay - time.time()))


def note_fail(ip):
    with db() as c:
        c.execute("INSERT INTO attempts(ip,fails,last) VALUES(?,1,?) "
                  "ON CONFLICT(ip) DO UPDATE SET fails=fails+1, last=excluded.last",
                  (ip, time.time()))


def clear_fails(ip):
    with db() as c:
        c.execute("DELETE FROM attempts WHERE ip=?", (ip,))


# ---------------- state ----------------
def state():
    with db() as c:
        players = [r["name"] for r in c.execute(
            "SELECT name FROM players ORDER BY name COLLATE NOCASE")]
        matches = []
        for r in c.execute("SELECT * FROM matches ORDER BY date, id"):
            matches.append({
                "id": r["id"], "date": r["date"], "format": r["format"],
                "a": [x for x in (r["a1"], r["a2"]) if x],
                "b": [x for x in (r["b1"], r["b2"]) if x],
                "sa": r["score_a"], "sb": r["score_b"],
                "serve": r["serve"], "recorder": r["recorder"]})
        log = [{"at": r["at"], "action": r["action"], "detail": r["detail"]}
               for r in c.execute("SELECT at,action,detail FROM audit "
                                  "ORDER BY at DESC LIMIT 50")]
        rev = c.execute("SELECT COALESCE(MAX(created),0) FROM ("
                        "SELECT created FROM matches UNION ALL "
                        "SELECT created FROM players)").fetchone()[0]
    return {"players": players, "matches": matches,
            "useMov": cfg("use_mov", "true") == "true",
            "audit": log,
            "rev": f"{rev}:{len(matches)}:{len(players)}:{len(log)}"}


def validate(d):
    if d.get("format") not in ("singles", "doubles"):
        return "Format must be singles or doubles."
    need = 2 if d["format"] == "doubles" else 1
    a, b = d.get("a") or [], d.get("b") or []
    if len(a) != need or len(b) != need or not all(a + b):
        return f"{d['format'].title()} needs {need} player(s) per side."
    if len(set(a + b)) != len(a + b):
        return "A player can only appear once per match."
    try:
        sa, sb = int(d["sa"]), int(d["sb"])
    except (KeyError, TypeError, ValueError):
        return "Scores must be whole numbers."
    if sa < 0 or sb < 0 or max(sa, sb) > 99:
        return "Scores out of range."
    if sa == sb:
        return "Matches can't end tied."
    if d.get("serve") not in ("a", "b"):
        return "Record which side served first."
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", (d.get("date") or "").strip()):
        return "Date must be YYYY-MM-DD."
    # Recorder is mandatory: deletions are admin-only, so this is the only
    # attribution the log has for who entered a result.
    rec = (d.get("recorder") or "").strip()
    if len(rec) < 2:
        return "Put down who recorded the game."
    if len(rec) > 40:
        return "Recorder name too long."
    return None


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):
        if "/api/state" not in self.path:
            sys.stderr.write("  %s\n" % (fmt % args))

    # ---- helpers ----
    def client_ip(self):
        # Behind cloudflared/ngrok the socket peer is localhost, so trust the
        # forwarded header for rate-limit bucketing.
        fwd = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For")
        return fwd.split(",")[0].strip() if fwd else self.client_address[0]

    def is_https(self):
        # Railway/Heroku/Render terminate TLS at their edge and forward plain
        # HTTP internally, so the socket itself is never encrypted — this
        # header is the only signal. Also treat a known-cloud env as HTTPS so
        # the Secure cookie flag is set even if the header is absent.
        proto = (self.headers.get("X-Forwarded-Proto") or "").lower()
        if proto:
            return proto.split(",")[0].strip() == "https"
        return bool(os.environ.get("RAILWAY_ENVIRONMENT")
                    or os.environ.get("RENDER")
                    or os.environ.get("DYNO"))

    def send_json(self, obj, code=200, cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64_000:
            raise ValueError("payload too large")
        return json.loads(self.rfile.read(n) or "{}")

    def role(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        c = SimpleCookie(raw).get("pbsess")
        return token_role(c.value) if c else None

    def authed(self):
        return self.role() is not None

    def is_admin(self):
        return self.role() == "admin"

    def cookie_for(self, tok):
        bits = [f"pbsess={tok}", "HttpOnly", "Path=/", "SameSite=Lax",
                f"Max-Age={SESSION_DAYS*86400}"]
        if self.is_https():
            bits.append("Secure")
        return "; ".join(bits)

    def deny(self):
        self.send_json({"error": "Not authorized.", "auth": False}, 401)

    def deny_admin(self):
        self.send_json({"error": "That needs the admin PIN.", "needAdmin": True}, 403)

    # ---- routes ----
    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/health":
            # Railway pings this to decide whether a deploy succeeded. Touch the
            # DB so a broken/unwritable volume fails the healthcheck instead of
            # going live and losing matches.
            try:
                with db() as c:
                    c.execute("SELECT 1 FROM settings LIMIT 1")
                return self.send_json({"ok": True, "db": str(DB)})
            except sqlite3.Error as e:
                return self.send_json({"ok": False, "error": str(e)}, 503)
        if p == "/api/backups":
            # Admin-only: lists on-disk snapshots so you can confirm backups
            # are actually happening rather than assuming.
            if not self.is_admin():
                return self.deny_admin() if self.authed() else self.deny()
            bdir = DB.parent / "backups"
            out = []
            if bdir.is_dir():
                for f in sorted(bdir.glob("pickleball-*.db"), reverse=True):
                    st = f.stat()
                    out.append({"name": f.name, "size": st.st_size, "at": st.st_mtime})
            return self.send_json({"backups": out, "dir": str(bdir)})
        if p == "/api/export":
            # Full JSON dump for off-site backup. Admin-only: it's the entire
            # dataset in one request.
            if not self.is_admin():
                return self.deny_admin() if self.authed() else self.deny()
            body = json.dumps(state(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition",
                             f'attachment; filename="pickleball-{time.strftime("%Y%m%d")}.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if p == "/api/auth":
            return self.send_json({"pinSet": pin_is_set(),
                                   "adminSet": admin_is_set(),
                                   "authed": self.authed(),
                                   "role": self.role(),
                                   "needSetupToken": bool(os.environ.get("SETUP_TOKEN")),
                                   "lock": lock_left(self.client_ip()),
                                   "https": self.is_https()})
        if p == "/api/state":
            return self.deny() if not self.authed() else self.send_json(state())
        if p in ("/", "/index.html"):
            self.path = "/index.html"
            return super().do_GET()
        if p.startswith("/api/"):
            return self.send_json({"error": "no such endpoint"}, 404)
        return super().do_GET()

    def do_POST(self):
        p = urlparse(self.path).path
        ip = self.client_ip()
        try:
            data = self.body()
        except (json.JSONDecodeError, ValueError):
            return self.send_json({"error": "malformed request"}, 400)

        # --- first-run PIN creation (only possible while no PIN exists) ---
        if p == "/api/setup":
            if pin_is_set():
                return self.send_json({"error": "A PIN is already set."}, 409)
            # On a public URL, whoever loads the page first would otherwise get
            # to claim the app. If SETUP_TOKEN is set in the environment, the
            # first-run form must supply it. Locally (no token set) setup stays
            # open, which is fine on your own LAN.
            want = os.environ.get("SETUP_TOKEN")
            if want:
                got = str(data.get("setupToken") or "")
                if not hmac.compare_digest(got, want):
                    note_fail(ip)
                    time.sleep(0.4)
                    return self.send_json(
                        {"error": "Wrong setup code."}, 403)
            pin = str(data.get("pin") or "")
            apin = str(data.get("adminPin") or "")
            for label, v in (("Friend PIN", pin), ("Admin PIN", apin)):
                if not re.fullmatch(rf"\d{{{MIN_PIN},{MAX_PIN}}}", v):
                    return self.send_json(
                        {"error": f"{label} must be {MIN_PIN}–{MAX_PIN} digits."}, 400)
                if len(set(v)) == 1 or v in ("123456", "654321", "000000",
                                             "12345678", "123456789"):
                    return self.send_json(
                        {"error": f"{label} is too easy to guess."}, 400)
            if pin == apin:
                return self.send_json(
                    {"error": "The admin PIN must be different from the friend PIN."}, 400)
            set_cfg("pin_hash", hash_pin(pin))
            set_cfg("admin_pin_hash", hash_pin(apin))
            clear_fails(ip)
            audit("setup", "friend + admin PINs created", ip)
            return self.send_json({"ok": True, "role": "admin"}, 200,
                                  self.cookie_for(make_token("admin")))

        # --- login ---
        if p == "/api/login":
            wait = lock_left(ip)
            if wait:
                # Count hammering-while-locked too, so the delay keeps growing
                # instead of plateauing at its first value.
                note_fail(ip)
                return self.send_json(
                    {"error": f"Too many wrong PINs. Try again in {wait}s.",
                     "lock": wait}, 429)
            role = check_pin(str(data.get("pin") or ""))
            if role:
                clear_fails(ip)
                return self.send_json({"ok": True, "role": role}, 200,
                                      self.cookie_for(make_token(role)))
            note_fail(ip)
            time.sleep(0.4)                     # blunt the request rate too
            return self.send_json({"error": "Wrong PIN."}, 401)

        if p == "/api/logout":
            return self.send_json({"ok": True}, 200,
                                  "pbsess=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")

        # --- everything below requires a session ---
        if not self.authed():
            return self.deny()

        try:
            if p == "/api/players":
                name = (data.get("name") or "").strip()
                if not name:
                    return self.send_json({"error": "Name required."}, 400)
                if len(name) > 40:
                    return self.send_json({"error": "Name too long."}, 400)
                with db() as c:
                    if c.execute("SELECT 1 FROM players WHERE name=? COLLATE NOCASE",
                                 (name,)).fetchone():
                        return self.send_json({"error": f"{name} already exists."}, 409)
                    c.execute("INSERT INTO players(name,created) VALUES(?,?)",
                              (name, time.time()))
                return self.send_json(state())

            if p == "/api/matches":
                err = validate(data)
                if err:
                    return self.send_json({"error": err}, 400)
                a, b = data["a"], data["b"]
                rec = (data.get("recorder") or "").strip()[:40]
                with db() as c:
                    known = {r["name"] for r in c.execute("SELECT name FROM players")}
                    missing = [x for x in a + b if x not in known]
                    if missing:
                        return self.send_json(
                            {"error": "Unknown player(s): " + ", ".join(missing)}, 400)
                    c.execute("INSERT INTO matches(date,format,a1,a2,b1,b2,score_a,"
                              "score_b,serve,recorder,created) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (data["date"], data["format"], a[0],
                               a[1] if len(a) > 1 else None, b[0],
                               b[1] if len(b) > 1 else None, int(data["sa"]),
                               int(data["sb"]), data["serve"], rec, time.time()))
                audit("match added",
                      f"{'/'.join(a)} {data['sa']}-{data['sb']} {'/'.join(b)} "
                      f"(by {rec})", ip)
                return self.send_json(state())

            if p == "/api/settings":
                if not self.is_admin():
                    return self.deny_admin()
                set_cfg("use_mov", "true" if data.get("useMov") else "false")
                audit("setting changed",
                      f"margin-of-victory {'on' if data.get('useMov') else 'off'}", ip)
                return self.send_json(state())

            if p == "/api/pin":
                # Only an admin can rotate either PIN, and must confirm with
                # the admin PIN — not merely hold an admin cookie.
                if not self.is_admin():
                    return self.deny_admin()
                if check_pin(str(data.get("old") or "")) != "admin":
                    note_fail(ip)
                    return self.send_json({"error": "Admin PIN is wrong."}, 401)
                which = data.get("which")
                if which not in ("friend", "admin"):
                    return self.send_json({"error": "Say which PIN to change."}, 400)
                new = str(data.get("new") or "")
                if not re.fullmatch(rf"\d{{{MIN_PIN},{MAX_PIN}}}", new):
                    return self.send_json(
                        {"error": f"New PIN must be {MIN_PIN}–{MAX_PIN} digits."}, 400)
                other = cfg("admin_pin_hash" if which == "friend" else "pin_hash")
                if _verify(new, other):
                    return self.send_json(
                        {"error": "The two PINs must be different."}, 400)
                set_cfg("admin_pin_hash" if which == "admin" else "pin_hash",
                        hash_pin(new))
                audit("pin changed", f"{which} PIN rotated", ip)
                if which == "admin":
                    # Rotating the admin PIN invalidates every session, so a
                    # leaked admin cookie can't outlive the PIN it came from.
                    set_cfg("secret", secrets.token_hex(32))
                    return self.send_json({"ok": True, "role": "admin"}, 200,
                                          self.cookie_for(make_token("admin")))
                return self.send_json({"ok": True})

            if p == "/api/backup":
                # Take a snapshot on demand — worth doing before you push a
                # risky change or delete a batch of matches.
                if not self.is_admin():
                    return self.deny_admin()
                auto_backup()
                audit("backup taken", "manual snapshot", ip)
                return self.send_json({"ok": True})

            if p == "/api/import":
                # Restore from a JSON export. Additive by default so a partial
                # file can't wipe good data; replace=true is opt-in and logged.
                if not self.is_admin():
                    return self.deny_admin()
                if check_pin(str(data.get("adminPin") or "")) != "admin":
                    note_fail(ip)
                    return self.send_json({"error": "Admin PIN required to import."}, 401)
                players = data.get("players")
                matches = data.get("matches")
                if not isinstance(players, list) or not isinstance(matches, list):
                    return self.send_json(
                        {"error": "That doesn't look like a Pickleball Elo export."}, 400)
                # Snapshot first — an import is exactly when you want an undo.
                auto_backup()
                replace = bool(data.get("replace"))
                added_p = added_m = 0
                with db() as c:
                    if replace:
                        c.execute("DELETE FROM matches")
                        c.execute("DELETE FROM players")
                    for nm in players:
                        if isinstance(nm, str) and nm.strip():
                            cur = c.execute(
                                "INSERT OR IGNORE INTO players(name,created) VALUES(?,?)",
                                (nm.strip()[:40], time.time()))
                            added_p += cur.rowcount
                    known = {r["name"] for r in c.execute("SELECT name FROM players")}
                    for m in matches:
                        if not isinstance(m, dict):
                            continue
                        a = m.get("a") or []
                        b = m.get("b") or []
                        if not a or not b or any(x not in known for x in a + b):
                            continue
                        try:
                            sa, sb = int(m["sa"]), int(m["sb"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        # Skip rows already present so re-importing the same file
                        # twice doesn't double every match.
                        if c.execute(
                            "SELECT 1 FROM matches WHERE date=? AND a1=? AND b1=? "
                            "AND score_a=? AND score_b=?",
                            (m.get("date"), a[0], b[0], sa, sb)).fetchone():
                            continue
                        c.execute(
                            "INSERT INTO matches(date,format,a1,a2,b1,b2,score_a,"
                            "score_b,serve,recorder,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (m.get("date") or "1970-01-01",
                             m.get("format") if m.get("format") in ("singles", "doubles")
                             else ("doubles" if len(a) > 1 else "singles"),
                             a[0], a[1] if len(a) > 1 else None,
                             b[0], b[1] if len(b) > 1 else None, sa, sb,
                             m.get("serve") if m.get("serve") in ("a", "b") else "a",
                             (m.get("recorder") or "imported")[:40], time.time()))
                        added_m += 1
                audit("data imported",
                      f"{'replaced all, ' if replace else ''}"
                      f"+{added_p} players, +{added_m} matches", ip)
                return self.send_json(state())

            return self.send_json({"error": "no such endpoint"}, 404)
        except sqlite3.Error as e:
            return self.send_json({"error": f"database error: {e}"}, 500)

    def do_DELETE(self):
        if not self.authed():
            return self.deny()
        if not self.is_admin():
            return self.deny_admin()
        p = urlparse(self.path).path
        ip = self.client_ip()
        try:
            # Build the audit line inside the transaction but WRITE it after
            # committing: audit() opens its own connection, and calling it while
            # this write txn still holds the lock deadlocks SQLite
            # ("database is locked"). Learned the hard way.
            entry = None
            with db() as c:
                if p.startswith("/api/matches/"):
                    mid = p.rsplit("/", 1)[1]
                    r = c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
                    if not r:
                        return self.send_json({"error": "No such match."}, 404)
                    side_a = "/".join(x for x in (r["a1"], r["a2"]) if x)
                    side_b = "/".join(x for x in (r["b1"], r["b2"]) if x)
                    c.execute("DELETE FROM matches WHERE id=?", (mid,))
                    entry = ("match DELETED",
                             f"{r['date']} {side_a} {r['score_a']}-{r['score_b']} "
                             f"{side_b} (was entered by {r['recorder'] or 'unknown'})")
                elif p.startswith("/api/players/"):
                    name = unquote(p.rsplit("/", 1)[1])
                    if not c.execute("SELECT 1 FROM players WHERE name=?",
                                     (name,)).fetchone():
                        return self.send_json({"error": "No such player."}, 404)
                    n = c.execute("SELECT COUNT(*) FROM matches WHERE ? IN (a1,a2,b1,b2)",
                                  (name,)).fetchone()[0]
                    c.execute("DELETE FROM matches WHERE ? IN (a1,a2,b1,b2)", (name,))
                    c.execute("DELETE FROM players WHERE name=?", (name,))
                    entry = ("player DELETED", f"{name} (and {n} of their matches)")
                else:
                    return self.send_json({"error": "no such endpoint"}, 404)
            if entry:
                audit(entry[0], entry[1], ip)
            return self.send_json(state())
        except sqlite3.Error as e:
            return self.send_json({"error": f"database error: {e}"}, 500)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except OSError:
        return "127.0.0.1"


if __name__ == "__main__":
    volume_check()          # refuse to start if data would be ephemeral
    init()
    auto_backup()           # snapshot last-known-good state before serving
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        p = c.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    print("\n  \033[1mPickleball Elo\033[0m — shared server running")
    print(f"  Database   {DB}  ({p} players, {n} matches)")
    if pin_is_set():
        print(f"  PINs       friend: set   admin: {'set' if admin_is_set() else 'MISSING'}")
    else:
        print("  PINs       NOT SET — first visitor creates both")
    print(f"  This Mac   http://localhost:{PORT}")
    print(f"  Same Wi-Fi http://{lan_ip()}:{PORT}")
    print("  Public     run ./tunnel.sh in another terminal for an HTTPS link")
    print("  Ctrl-C to stop.  Your data stays in pickleball.db either way.\n")
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("  Stopped. Data is saved in pickleball.db\n")
