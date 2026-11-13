"""SQLite storage: talk events, RSVPs, and the token / safety logs.

Resumes and chat text are never written here. The usage log keeps counts only, keyed by a
hashed browser session id, which is enough for tokenomics without holding personal data.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'Think Tank talk',
    speaker_slugs TEXT NOT NULL DEFAULT '[]',
    starts_at TEXT NOT NULL,           -- local Minneapolis time, ISO 'YYYY-MM-DDTHH:MM'
    ends_at TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    join_url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    prep_note TEXT NOT NULL DEFAULT '', -- the reminder text interns see
    required INTEGER NOT NULL DEFAULT 0,
    is_sample INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rsvps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(event_id, name)
);
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    session TEXT NOT NULL,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    requests INTEGER NOT NULL,
    tool_calls INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    latency_ms INTEGER NOT NULL,
    outcome TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    slack_email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    school TEXT NOT NULL DEFAULT '',
    interests TEXT NOT NULL DEFAULT '',
    hobbies TEXT NOT NULL DEFAULT '',
    goals TEXT NOT NULL DEFAULT '',
    fun_fact TEXT NOT NULL DEFAULT '',
    linkedin_url TEXT NOT NULL DEFAULT '',
    photo BLOB,
    photo_updated TEXT,
    share_with_cohort INTEGER NOT NULL DEFAULT 1,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login TEXT
);
CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,  -- sha256 of the token; the token itself is never stored
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_by TEXT NOT NULL          -- 'admin' or 'email'
);
CREATE TABLE IF NOT EXISTS reset_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS committees (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    needs TEXT NOT NULL DEFAULT '',          -- skills/traits the committee is looking for
    capacity INTEGER NOT NULL DEFAULT 2,
    director_slug TEXT NOT NULL DEFAULT '',  -- a Think Tank member, if the director is one
    director_name TEXT NOT NULL DEFAULT '',  -- otherwise typed in by the admin
    director_email TEXT NOT NULL DEFAULT '',
    meeting_day TEXT NOT NULL DEFAULT '',    -- Mon..Fri, '' = not set
    meeting_time TEXT NOT NULL DEFAULT '',   -- HH:MM local
    meeting_note TEXT NOT NULL DEFAULT '',   -- e.g. "1st & 3rd Tuesday"
    active INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS coaches (
    slug TEXT PRIMARY KEY,                   -- Think Tank slug
    capacity INTEGER NOT NULL DEFAULT 2,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS fit_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    source_hash TEXT NOT NULL,               -- re-extract only when the profile changes
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    matrix_json TEXT NOT NULL,
    usage_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS placements (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    committee_slug TEXT NOT NULL,
    coach_slug TEXT NOT NULL DEFAULT '',
    why TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'proposed', -- proposed | approved
    drafts_json TEXT NOT NULL DEFAULT '',
    run_id INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slack_users (
    slack_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    real_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    is_bot INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS slack_messages (
    channel TEXT NOT NULL,
    ts TEXT NOT NULL,                        -- Slack message timestamp (unique per channel)
    slack_user TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    thread_ts TEXT NOT NULL DEFAULT '',
    reply_count INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'export',   -- export | live | demo
    PRIMARY KEY (channel, ts)
);
CREATE TABLE IF NOT EXISTS meeting_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    committee TEXT NOT NULL DEFAULT '',
    meeting_date TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'upload',
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_of TEXT NOT NULL,
    created_at TEXT NOT NULL,
    brief_json TEXT NOT NULL,                -- pseudonymized, as the model wrote it
    alias_map_json TEXT NOT NULL,            -- alias -> person, for the admin view only
    usage_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS safety_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


# Columns added after v1. CREATE TABLE IF NOT EXISTS won't add them to an existing table.
MIGRATIONS = {
    "users": {
        "fun_fact": "TEXT NOT NULL DEFAULT ''",
        "linkedin_url": "TEXT NOT NULL DEFAULT ''",
        "photo": "BLOB",
        "photo_updated": "TEXT",
        "session_version": "INTEGER NOT NULL DEFAULT 0",
        "temp_password_hash": "TEXT",
        "temp_password_expires": "TEXT",
        "must_change_password": "INTEGER NOT NULL DEFAULT 0",
        "committee_prefs": "TEXT NOT NULL DEFAULT '[]'",
        "availability": "TEXT NOT NULL DEFAULT ''",
        "is_sample": "INTEGER NOT NULL DEFAULT 0",
    },
}


def _migrate(c: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)
        if c.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
            _seed_samples(c)
    from . import placements  # noqa: PLC0415  (seeds committees + coach pool once)
    placements.seed_defaults()


def _seed_samples(c: sqlite3.Connection) -> None:
    """Three clearly-labelled sample talks so a fresh install has a calendar to look at.
    Dated relative to today so they are always upcoming. The admin deletes them."""
    base = date.today()

    def at(days: int, hh: int, mm: int = 0) -> str:
        d = base + timedelta(days=days)
        return f"{d.isoformat()}T{hh:02d}:{mm:02d}"

    samples = [
        dict(title="Fireside chat: a day in the life of a CISO", kind="CISO fireside",
             speakers=["christopher-buse", "tina-meeker"], start=at(3, 12), end=at(3, 13),
             location="Zoom", description="Old Republic's CISO and Deputy CISO on how a security "
             "program is run, what they look for in early-career hires, and open Q&A.",
             prep="Bring one question about how security leaders talk to a board.", required=1),
        dict(title="Military to cyber: career paths panel", kind="Think Tank panel",
             speakers=["major-brian-morgan", "peter-kapelanski", "nicolette-weis"],
             start=at(8, 16), end=at(8, 17), location="Zoom",
             description="Minnesota National Guard cyber leaders on Guard service, Cyber Protection "
             "Teams, and moving between military and private-sector security.",
             prep="Skim the panelists' bios in the Directory first.", required=0),
        dict(title="Identity & access management office hours", kind="Office hours",
             speakers=["michael-nghiem", "mamady-konneh"], start=at(12, 11), end=at(12, 12),
             location="Zoom", description="Small-group office hours on IAM careers, CIAM, and "
             "breaking into product management in security.",
             prep="Have your resume open; we will do quick reviews.", required=0),
    ]
    now = _now()
    for s in samples:
        c.execute(
            "INSERT INTO events (title, kind, speaker_slugs, starts_at, ends_at, location, "
            "join_url, description, prep_note, required, is_sample, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (s["title"], s["kind"], json.dumps(s["speakers"]), s["start"], s["end"],
             s["location"], "", s["description"], s["prep"], s["required"], now, now),
        )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
EVENT_FIELDS = ("title", "kind", "speaker_slugs", "starts_at", "ends_at", "location",
                "join_url", "description", "prep_note", "required")


def _event_row(r: sqlite3.Row, rsvp_counts: dict[int, int]) -> dict[str, Any]:
    d = dict(r)
    d["speaker_slugs"] = json.loads(d["speaker_slugs"])
    d["required"] = bool(d["required"])
    d["is_sample"] = bool(d["is_sample"])
    d["rsvp_count"] = rsvp_counts.get(d["id"], 0)
    return d


def list_events(upcoming_only: bool = False) -> list[dict[str, Any]]:
    with conn() as c:
        counts = {r[0]: r[1] for r in c.execute(
            "SELECT event_id, COUNT(*) FROM rsvps GROUP BY event_id")}
        q = "SELECT * FROM events"
        args: tuple = ()
        if upcoming_only:
            q += " WHERE ends_at >= ?"
            args = (datetime.now().strftime("%Y-%m-%dT%H:%M"),)
        rows = c.execute(q + " ORDER BY starts_at", args).fetchall()
    return [_event_row(r, counts) for r in rows]


def get_event(event_id: int) -> dict[str, Any] | None:
    with conn() as c:
        r = c.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not r:
            return None
        n = c.execute("SELECT COUNT(*) FROM rsvps WHERE event_id = ?", (event_id,)).fetchone()[0]
    return _event_row(r, {event_id: n})


def save_event(data: dict[str, Any], event_id: int | None = None) -> int:
    vals = [json.dumps(data[k]) if k == "speaker_slugs" else (int(data[k]) if k == "required"
            else data[k]) for k in EVENT_FIELDS]
    with conn() as c:
        if event_id is None:
            cur = c.execute(
                f"INSERT INTO events ({', '.join(EVENT_FIELDS)}, is_sample, created_at, updated_at) "
                f"VALUES ({', '.join('?' * len(EVENT_FIELDS))}, 0, ?, ?)",
                (*vals, _now(), _now()),
            )
            return int(cur.lastrowid)
        c.execute(
            f"UPDATE events SET {', '.join(f'{k} = ?' for k in EVENT_FIELDS)}, is_sample = 0, "
            "updated_at = ? WHERE id = ?",
            (*vals, _now(), event_id),
        )
        return event_id


def delete_event(event_id: int) -> bool:
    with conn() as c:
        return c.execute("DELETE FROM events WHERE id = ?", (event_id,)).rowcount > 0


def add_rsvp(event_id: int, name: str) -> int:
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO rsvps (event_id, name, created_at) VALUES (?,?,?)",
                  (event_id, name, _now()))
        return c.execute("SELECT COUNT(*) FROM rsvps WHERE event_id = ?", (event_id,)).fetchone()[0]


def list_rsvps(event_id: int) -> list[str]:
    with conn() as c:
        return [r[0] for r in c.execute(
            "SELECT name FROM rsvps WHERE event_id = ? ORDER BY created_at", (event_id,))]


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------
def list_announcements(active_only: bool = True) -> list[dict[str, Any]]:
    with conn() as c:
        q = "SELECT * FROM announcements" + (" WHERE active = 1" if active_only else "")
        return [dict(r) for r in c.execute(q + " ORDER BY created_at DESC")]


def add_announcement(message: str) -> int:
    with conn() as c:
        return int(c.execute("INSERT INTO announcements (message, created_at) VALUES (?,?)",
                             (message, _now())).lastrowid)


def archive_announcement(ann_id: int) -> bool:
    with conn() as c:
        return c.execute("UPDATE announcements SET active = 0 WHERE id = ?", (ann_id,)).rowcount > 0


# ---------------------------------------------------------------------------
# Intern accounts
# ---------------------------------------------------------------------------
USER_FIELDS = ("first_name", "last_name", "phone", "slack_email", "city", "state", "school",
               "interests", "hobbies", "goals", "fun_fact", "linkedin_url", "share_with_cohort",
               "committee_prefs", "availability")
# Everything except the photo bytes and password hash, for list/detail queries.
_USER_COLS = ("id, first_name, last_name, phone, slack_email, city, state, school, interests, "
              "hobbies, goals, fun_fact, linkedin_url, share_with_cohort, created_at, updated_at, "
              "last_login, photo_updated, password_hash, session_version, temp_password_hash, "
              "temp_password_expires, must_change_password, committee_prefs, availability, "
              "is_sample")


def _user_val(k: str, v: Any) -> Any:
    if k == "share_with_cohort":
        return int(v)
    if k == "committee_prefs":
        return v if isinstance(v, str) else json.dumps(list(v or []))
    return v


def create_user(data: dict[str, Any], password_hash: str, is_sample: bool = False) -> int:
    data = {"committee_prefs": [], "availability": "", **data}
    with conn() as c:
        cur = c.execute(
            f"INSERT INTO users ({', '.join(USER_FIELDS)}, password_hash, is_sample, created_at, "
            f"updated_at) VALUES ({', '.join('?' * len(USER_FIELDS))}, ?, ?, ?, ?)",
            (*(_user_val(k, data[k]) for k in USER_FIELDS), password_hash, int(is_sample),
             _now(), _now()),
        )
        return int(cur.lastrowid)


def update_user(user_id: int, data: dict[str, Any]) -> None:
    keys = [k for k in USER_FIELDS if k in data]
    with conn() as c:
        c.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in keys)}, updated_at = ? WHERE id = ?",
                  (*(_user_val(k, data[k]) for k in keys), _now(), user_id))


def get_user(user_id: int) -> dict[str, Any] | None:
    with conn() as c:
        r = c.execute(f"SELECT {_USER_COLS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(r) if r else None


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with conn() as c:
        r = c.execute(f"SELECT {_USER_COLS} FROM users WHERE slack_email = ?", (email,)).fetchone()
    return dict(r) if r else None


def touch_login(user_id: int) -> None:
    with conn() as c:
        c.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), user_id))


def list_users(cohort_only: bool = False) -> list[dict[str, Any]]:
    with conn() as c:
        q = f"SELECT {_USER_COLS} FROM users" + (" WHERE share_with_cohort = 1" if cohort_only else "")
        return [dict(r) for r in c.execute(q + " ORDER BY last_name COLLATE NOCASE, first_name")]


def set_photo(user_id: int, data: bytes | None) -> None:
    with conn() as c:
        c.execute("UPDATE users SET photo = ?, photo_updated = ? WHERE id = ?",
                  (data, _now() if data else None, user_id))


def get_photo(user_id: int) -> bytes | None:
    with conn() as c:
        r = c.execute("SELECT photo FROM users WHERE id = ?", (user_id,)).fetchone()
    return r[0] if r else None


def delete_user(user_id: int) -> bool:
    with conn() as c:
        return c.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount > 0


# ---------------------------------------------------------------------------
# Passwords + resets
# ---------------------------------------------------------------------------
def set_password(user_id: int, password_hash: str) -> None:
    """New password, and bump session_version so every existing sign-in cookie dies.
    Also clears any temporary password and the forced-change flag."""
    with conn() as c:
        c.execute("UPDATE users SET password_hash = ?, session_version = session_version + 1, "
                  "temp_password_hash = NULL, temp_password_expires = NULL, "
                  "must_change_password = 0, updated_at = ? WHERE id = ?",
                  (password_hash, _now(), user_id))


def set_temp_password(user_id: int, temp_hash: str, expires_at: str) -> None:
    """Stored next to the real password, not over it, so a stranger who requests a reset
    for someone else's email can't lock them out."""
    with conn() as c:
        c.execute("UPDATE users SET temp_password_hash = ?, temp_password_expires = ? WHERE id = ?",
                  (temp_hash, expires_at, user_id))


def consume_temp_password(user_id: int) -> None:
    """Single use: signing in with it clears it and requires a new password."""
    with conn() as c:
        c.execute("UPDATE users SET temp_password_hash = NULL, temp_password_expires = NULL, "
                  "must_change_password = 1 WHERE id = ?", (user_id,))


def clear_temp_password(user_id: int) -> None:
    """They remembered their real password, so the emailed one is no longer needed."""
    with conn() as c:
        c.execute("UPDATE users SET temp_password_hash = NULL, temp_password_expires = NULL "
                  "WHERE id = ?", (user_id,))


def add_reset_request(user_id: int) -> None:
    """One open request per intern is enough; repeat clicks don't pile up."""
    with conn() as c:
        open_ = c.execute("SELECT 1 FROM reset_requests WHERE user_id = ? AND resolved_at IS NULL",
                          (user_id,)).fetchone()
        if not open_:
            c.execute("INSERT INTO reset_requests (user_id, created_at) VALUES (?, ?)",
                      (user_id, _now()))


def list_reset_requests() -> list[dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT r.id, r.user_id, r.created_at, u.first_name, u.last_name, u.slack_email "
            "FROM reset_requests r JOIN users u ON u.id = r.user_id "
            "WHERE r.resolved_at IS NULL ORDER BY r.created_at")]


def resolve_reset_requests(user_id: int) -> None:
    with conn() as c:
        c.execute("UPDATE reset_requests SET resolved_at = ? WHERE user_id = ? AND resolved_at IS NULL",
                  (_now(), user_id))


def create_reset_token(user_id: int, token_hash: str, expires_at: str, created_by: str) -> None:
    """Only the newest link works: any earlier unused link for this intern is retired."""
    with conn() as c:
        c.execute("UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                  (_now(), user_id))
        c.execute("INSERT INTO password_resets (user_id, token_hash, created_at, expires_at, "
                  "created_by) VALUES (?, ?, ?, ?, ?)",
                  (user_id, token_hash, _now(), expires_at, created_by))


def find_reset_token(token_hash: str) -> dict[str, Any] | None:
    """A token that exists, is unused, and hasn't expired."""
    with conn() as c:
        r = c.execute("SELECT * FROM password_resets WHERE token_hash = ? AND used_at IS NULL "
                      "AND expires_at > ?", (token_hash, _now())).fetchone()
    return dict(r) if r else None


def use_reset_token(token_id: int) -> bool:
    """Mark used. Returns False if someone else used it first (single use, race-safe)."""
    with conn() as c:
        return c.execute("UPDATE password_resets SET used_at = ? WHERE id = ? AND used_at IS NULL",
                         (_now(), token_id)).rowcount == 1


def get_setting(key: str) -> str | None:
    with conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return r[0] if r else None


def set_setting(key: str, value: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


# ---------------------------------------------------------------------------
# Usage + safety logs
# ---------------------------------------------------------------------------
def log_usage(session: str, agent: str, model: str, requests: int, tool_calls: int,
              input_tokens: int, cache_write: int, cache_read: int, output_tokens: int,
              cost: float, latency_ms: int, outcome: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO usage_log (ts, day, session, agent, model, requests, tool_calls, "
            "input_tokens, cache_write_tokens, cache_read_tokens, output_tokens, cost_usd, "
            "latency_ms, outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), date.today().isoformat(), session, agent, model, requests, tool_calls,
             input_tokens, cache_write, cache_read, output_tokens, cost, latency_ms, outcome),
        )


def tokens_used_today() -> int:
    with conn() as c:
        return int(c.execute(
            "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM usage_log WHERE day = ?",
            (date.today().isoformat(),)).fetchone()[0])


def log_safety(session: str, kind: str, detail: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO safety_log (ts, session, kind, detail) VALUES (?,?,?,?)",
                  (_now(), session, kind, detail[:500]))


def usage_summary() -> dict[str, Any]:
    with conn() as c:
        by_agent = [dict(r) for r in c.execute(
            "SELECT agent, model, COUNT(*) AS runs, SUM(input_tokens) AS input_tokens, "
            "SUM(cache_write_tokens) AS cache_write_tokens, SUM(cache_read_tokens) AS "
            "cache_read_tokens, SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost_usd, "
            "AVG(latency_ms) AS avg_latency_ms FROM usage_log GROUP BY agent, model ORDER BY agent")]
        by_day = [dict(r) for r in c.execute(
            "SELECT day, COUNT(DISTINCT session) AS sessions, COUNT(*) AS runs, "
            "SUM(input_tokens + output_tokens) AS tokens, SUM(cost_usd) AS cost_usd "
            "FROM usage_log GROUP BY day ORDER BY day DESC LIMIT 30")]
        totals = dict(c.execute(
            "SELECT COUNT(DISTINCT session) AS sessions, COUNT(*) AS runs, "
            "COALESCE(SUM(cost_usd), 0) AS cost_usd, COALESCE(SUM(input_tokens), 0) AS "
            "input_tokens, COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens FROM usage_log").fetchone())
        recent = [dict(r) for r in c.execute(
            "SELECT ts, agent, model, input_tokens, cache_write_tokens, cache_read_tokens, "
            "output_tokens, cost_usd, latency_ms, outcome FROM usage_log ORDER BY id DESC LIMIT 25")]
        safety = [dict(r) for r in c.execute(
            "SELECT ts, kind, detail FROM safety_log ORDER BY id DESC LIMIT 25")]
    return {"totals": totals, "by_agent": by_agent, "by_day": by_day, "recent": recent,
            "safety": safety, "tokens_today": tokens_used_today()}
