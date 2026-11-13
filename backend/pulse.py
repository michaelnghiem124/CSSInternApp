"""Cohort Pulse & Committee Accountability Monitor (admin only).

Data in:  Slack (live via a bot token, or an uploaded Slack export .zip) and committee
          meeting notes the Chair uploads. Stored in SQLite.
Agent:    the Pulse Analyst reads that data ONLY through the cohort MCP server
          (mcp_server/cohort_server.py), which hands it a privacy-safe view:
            - every person is a pseudonym (Intern-03, Member-01); real names, @mentions,
              emails, phone numbers and links are replaced before the model sees them
            - each message is wrapped as data; messages that look like instructions to an
              AI are marked [FLAGGED] and their text withheld
            - participation signals (last post, missed check-ins, unanswered questions) are
              computed in code, so the model doesn't have to count
Output:   a ChairBrief. An output validator rejects any brief that contains an email,
          phone number, real name, or a pseudonym that doesn't exist. Only after that does
          code swap pseudonyms back to names, for the admin screen.

Read-only by design: the agent has no tool that can post to Slack or email anyone.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta


from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset, StdioTransport
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from . import config, db, safety

CHECKIN_CHANNEL = os.environ.get("PULSE_CHECKIN_CHANNEL", "check-ins")
WINDOW_DAYS = 21
MCP_SERVER = config.PROJECT_ROOT / "mcp_server" / "cohort_server.py"

# ---------------------------------------------------------------------------
# Redaction + pseudonyms
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
URL_RE = re.compile(r"https?://\S+|<https?://[^>]+>")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
MENTION_RE = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
ALIAS_RE = re.compile(r"\b(?:Intern|Member)-\d{2}\b")
# Name parts that are also everyday words (or our own pseudonym prefixes) are never matched
# on their own, or "Intern" in "Test Intern" would rewrite every use of the word "intern".
GENERIC_WORDS = {"intern", "interns", "member", "members", "test", "tester", "chair", "admin",
                 "director", "coach", "team", "summit", "cyber", "security", "user", "guest",
                 "sample", "demo", "new", "the", "and", "general"}


def _usable_name(part: str) -> bool:
    p = part.strip()
    return len(p) > 2 and p.lower() not in GENERIC_WORDS


class People:
    """Everyone who appears in the data, with a stable pseudonym. Deterministic, so the
    MCP server process and the web server compute the same mapping independently."""

    def __init__(self) -> None:
        interns = sorted(db.list_users(), key=lambda u: u["id"])
        with db.conn() as c:
            slack_users = [dict(r) for r in c.execute("SELECT * FROM slack_users ORDER BY slack_id")]
        self.alias_of_user: dict[int, str] = {}
        self.display: dict[str, str] = {}            # alias -> "First Last"
        self.alias_of_slack: dict[str, str] = {}
        self.names: list[tuple[str, str]] = []       # (name text, alias), longest first
        for n, u in enumerate(interns, 1):
            a = f"Intern-{n:02d}"
            self.alias_of_user[u["id"]] = a
            self.display[a] = f"{u['first_name']} {u['last_name']}"
            self.names.append((f"{u['first_name']} {u['last_name']}", a))
            if _usable_name(u["first_name"]):
                self.names.append((u["first_name"], a))
            if len(u["last_name"]) > 3 and _usable_name(u["last_name"]):
                self.names.append((u["last_name"], a))
        by_email = {u["slack_email"].lower(): u for u in interns}
        m = 0
        for su in slack_users:
            u = by_email.get((su["email"] or "").lower())
            if u:
                a = self.alias_of_user[u["id"]]
            else:
                m += 1
                a = f"Member-{m:02d}"
                self.display[a] = su["real_name"] or su["name"] or a
                for nm in (su["real_name"], su["name"]):
                    if nm and _usable_name(nm):
                        self.names.append((nm, a))
            self.alias_of_slack[su["slack_id"]] = a
        self.names.sort(key=lambda t: -len(t[0]))
        self.intern_aliases = set(self.alias_of_user.values())

    def redact(self, text: str) -> str:
        text = MENTION_RE.sub(lambda m: self.alias_of_slack.get(m.group(1), "someone"), text)
        text = URL_RE.sub("[link]", text)
        text = EMAIL_RE.sub("[email]", text)
        text = SSN_RE.sub("[id-number]", text)
        text = PHONE_RE.sub("[phone]", text)
        for name, alias in self.names:
            text = re.sub(rf"\b{re.escape(name)}\b", alias, text, flags=re.IGNORECASE)
        return text

    def unalias(self, text: str) -> str:
        return ALIAS_RE.sub(lambda m: self.display.get(m.group(0), m.group(0)), text)


def _safe_message(people: People, text: str) -> tuple[str, bool]:
    """Redact, then withhold anything that reads like instructions to an AI."""
    clean = people.redact(text)
    screened = safety.screen(clean)
    if screened.flagged:
        return "[FLAGGED: message contained instructions aimed at an AI; text withheld]", True
    return clean[:600], False


# ---------------------------------------------------------------------------
# Privacy-safe views (what the MCP server returns)
# ---------------------------------------------------------------------------
def _since(days: int) -> float:
    return (datetime.now() - timedelta(days=days)).timestamp()


def _day(ts: str) -> str:
    return datetime.fromtimestamp(float(ts)).strftime("%a %b %d")


def view_channels() -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT channel, COUNT(*) AS messages, MAX(ts) AS last_ts FROM slack_messages "
            "WHERE CAST(ts AS REAL) >= ? GROUP BY channel ORDER BY channel", (_since(WINDOW_DAYS),))]


def view_messages(channel: str, days: int = 14) -> list[dict]:
    people = People()
    days = max(1, min(days, WINDOW_DAYS))
    with db.conn() as c:
        rows = c.execute("SELECT * FROM slack_messages WHERE channel = ? AND CAST(ts AS REAL) >= ? "
                         "ORDER BY ts", (channel, _since(days))).fetchall()
    out = []
    for r in rows:
        text, flagged = _safe_message(people, r["text"])
        out.append({"when": _day(r["ts"]), "who": people.alias_of_slack.get(r["slack_user"], "unknown"),
                    "replies": r["reply_count"], "text": text, "flagged": flagged})
    return out


def view_signals() -> dict:
    """Per-intern participation signals, computed in code."""
    people = People()
    now = time.time()
    with db.conn() as c:
        msgs = [dict(r) for r in c.execute("SELECT * FROM slack_messages WHERE CAST(ts AS REAL) >= ?",
                                           (_since(WINDOW_DAYS),))]
    by_alias: dict[str, list[dict]] = {}
    for m in msgs:
        by_alias.setdefault(people.alias_of_slack.get(m["slack_user"], "unknown"), []).append(m)
    week = 7 * 86400
    interns = []
    for alias in sorted(people.intern_aliases):
        ms = by_alias.get(alias, [])
        ts = [float(m["ts"]) for m in ms]
        checkins = [float(m["ts"]) for m in ms if m["channel"] == CHECKIN_CHANNEL]
        open_q = [m for m in ms if m["reply_count"] == 0 and now - float(m["ts"]) > 2 * 86400
                  and re.search(r"\?|\bblock(ed|er)?\b|\bstuck\b|\bwaiting on\b", m["text"], re.I)]
        interns.append({
            "who": alias,
            "on_slack": alias in by_alias or any(a == alias for a in people.alias_of_slack.values()),
            "last_post_days_ago": round((now - max(ts)) / 86400, 1) if ts else None,
            "posts_last_7d": sum(1 for t in ts if now - t <= week),
            "posts_prior_7d": sum(1 for t in ts if week < now - t <= 2 * week),
            "checkin_this_week": any(now - t <= week for t in checkins),
            "checkin_last_week": any(week < now - t <= 2 * week for t in checkins),
            "unanswered_questions": len(open_q),
            "flagged_messages": sum(1 for m in ms if safety.screen(m["text"]).flagged),
        })
    return {"today": datetime.now().strftime("%a %b %d, %Y"), "checkin_channel": CHECKIN_CHANNEL,
            "window_days": WINDOW_DAYS, "interns": interns}


def view_open_threads() -> list[dict]:
    """Questions and blockers with no replies, older than 2 days."""
    people = People()
    now = time.time()
    with db.conn() as c:
        rows = c.execute("SELECT * FROM slack_messages WHERE reply_count = 0 AND CAST(ts AS REAL) >= ? "
                         "ORDER BY ts", (_since(WINDOW_DAYS),)).fetchall()
    out = []
    for r in rows:
        if now - float(r["ts"]) < 2 * 86400:
            continue
        if not re.search(r"\?|\bblock(ed|er)?\b|\bstuck\b|\bwaiting on\b|\bcan't\b", r["text"], re.I):
            continue
        text, flagged = _safe_message(people, r["text"])
        out.append({"channel": r["channel"], "who": people.alias_of_slack.get(r["slack_user"], "unknown"),
                    "waiting_days": round((now - float(r["ts"])) / 86400, 1), "text": text,
                    "flagged": flagged})
    return out


def view_notes(days: int = WINDOW_DAYS) -> list[dict]:
    people = People()
    cutoff = (datetime.now() - timedelta(days=max(1, min(days, 60)))).strftime("%Y-%m-%d")
    with db.conn() as c:
        rows = c.execute("SELECT * FROM meeting_notes WHERE meeting_date >= ? ORDER BY meeting_date",
                         (cutoff,)).fetchall()
    out = []
    for r in rows:
        lines = []
        for ln in people.redact(r["text"]).splitlines():
            lines.append("[FLAGGED: instruction-like line withheld]" if safety.screen(ln).flagged else ln)
        out.append({"committee": r["committee"], "date": r["meeting_date"], "title": r["title"],
                    "notes": "\n".join(lines)[:4000]})
    return out


# ---------------------------------------------------------------------------
# Ingest: Slack export zip, live Slack API, meeting notes
# ---------------------------------------------------------------------------
def ingest_slack_export(data: bytes) -> dict:
    """Standard Slack export: users.json, channels.json, <channel>/<YYYY-MM-DD>.json."""
    n_msgs = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        users = json.loads(z.read("users.json")) if "users.json" in names else []
        with db.conn() as c:
            for u in users:
                prof = u.get("profile", {})
                c.execute("INSERT OR REPLACE INTO slack_users (slack_id, name, real_name, email, is_bot) "
                          "VALUES (?,?,?,?,?)", (u["id"], u.get("name", ""), prof.get("real_name", ""),
                                                 prof.get("email", ""), int(u.get("is_bot", False))))
            for name in names:
                parts = name.split("/")
                if len(parts) != 2 or not parts[1].endswith(".json"):
                    continue
                try:
                    msgs = json.loads(z.read(name))
                except (json.JSONDecodeError, KeyError):
                    continue
                for m in msgs if isinstance(msgs, list) else []:
                    if m.get("type") != "message" or m.get("subtype") in ("channel_join", "bot_message"):
                        continue
                    c.execute("INSERT OR REPLACE INTO slack_messages (channel, ts, slack_user, text, thread_ts, "
                              "reply_count, source) VALUES (?,?,?,?,?,?, 'export')",
                              (parts[0], m["ts"], m.get("user", ""), m.get("text", "")[:4000],
                               m.get("thread_ts", ""), int(m.get("reply_count", 0))))
                    n_msgs += 1
    return {"users": len(users), "messages": n_msgs}


def slack_live_enabled() -> bool:
    return bool(os.environ.get("SLACK_BOT_TOKEN"))


def _slack_api(method: str, **params) -> dict:
    req = urllib.request.Request(
        f"https://slack.com/api/{method}?{urllib.parse.urlencode(params)}",
        headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        out = json.loads(r.read())
    if not out.get("ok"):
        raise RuntimeError(f"Slack {method}: {out.get('error')}")
    return out


def sync_slack_live(days: int = WINDOW_DAYS) -> dict:
    """Pull the channels the bot has been added to. Needs scopes: channels:read,
    channels:history, users:read, users:read.email."""
    users = _slack_api("users.list", limit=500)["members"]
    chans = [c for c in _slack_api("conversations.list", types="public_channel", limit=500,
                                   exclude_archived="true")["channels"] if c.get("is_member")]
    oldest = str(_since(days))
    n = 0
    with db.conn() as c:
        for u in users:
            p = u.get("profile", {})
            c.execute("INSERT OR REPLACE INTO slack_users (slack_id, name, real_name, email, is_bot) "
                      "VALUES (?,?,?,?,?)", (u["id"], u.get("name", ""), p.get("real_name", ""),
                                             p.get("email", ""), int(u.get("is_bot", False))))
        for ch in chans:
            hist = _slack_api("conversations.history", channel=ch["id"], oldest=oldest, limit=500)
            for m in hist.get("messages", []):
                if m.get("subtype"):
                    continue
                c.execute("INSERT OR REPLACE INTO slack_messages (channel, ts, slack_user, text, thread_ts, "
                          "reply_count, source) VALUES (?,?,?,?,?,?, 'live')",
                          (ch["name"], m["ts"], m.get("user", ""), m.get("text", "")[:4000],
                           m.get("thread_ts", ""), int(m.get("reply_count", 0))))
                n += 1
    return {"channels": len(chans), "messages": n, "users": len(users)}


def add_notes(committee: str, meeting_date: str, title: str, text: str) -> int:
    with db.conn() as c:
        return int(c.execute("INSERT INTO meeting_notes (committee, meeting_date, title, text, uploaded_at) "
                             "VALUES (?,?,?,?,?)", (committee, meeting_date, title, text[:20000],
                                                    datetime.now().isoformat(timespec="seconds"))).lastrowid)


def sources_status() -> dict:
    with db.conn() as c:
        by_src = {r[0]: r[1] for r in c.execute("SELECT source, COUNT(*) FROM slack_messages GROUP BY source")}
        notes = [dict(r) for r in c.execute("SELECT id, committee, meeting_date, title, source FROM meeting_notes "
                                            "ORDER BY meeting_date DESC LIMIT 20")]
        users = c.execute("SELECT COUNT(*) FROM slack_users").fetchone()[0]
    return {"slack_live": slack_live_enabled(), "messages_by_source": by_src, "slack_users": users,
            "notes": notes, "checkin_channel": CHECKIN_CHANNEL}


def delete_notes(note_id: int) -> None:
    with db.conn() as c:
        c.execute("DELETE FROM meeting_notes WHERE id = ?", (note_id,))


# ---------------------------------------------------------------------------
# The Pulse Analyst agent
# ---------------------------------------------------------------------------
class Nudge(BaseModel):
    who: str = Field(description="Intern pseudonym, e.g. Intern-04.")
    reason: str = Field(description="One sentence.")
    evidence: list[str] = Field(description="Specific signals: dates, counts, overdue item.", max_length=4)
    suggested_dm: str = Field(description="A short, kind check-in message the Chair could send. 1-3 sentences.")


class Win(BaseModel):
    who: str
    what: str = Field(description="One sentence worth saying out loud at all-hands.")
    evidence: str


class Blocker(BaseModel):
    committee: str
    raised_by: str = Field("", description="Pseudonym, or empty.")
    blocker: str
    waiting_days: float
    escalate_to: str = Field(description="Who should unblock it, by role: committee director, Program Chair...")


class ChairBrief(BaseModel):
    headline: str = Field(description="One sentence on the state of the cohort this week.")
    nudges: list[Nudge] = Field(default_factory=list, max_length=8)
    wins: list[Win] = Field(default_factory=list, max_length=6)
    blockers: list[Blocker] = Field(default_factory=list, max_length=8)
    watch: list[str] = Field(default_factory=list, max_length=5,
                             description="Lower-priority patterns to keep an eye on.")
    safety_notes: list[str] = Field(default_factory=list,
                                    description="Flagged or withheld content you noticed, described neutrally.")
    coverage: str = Field(description="What data this brief is based on (channels, dates, notes).")


class PulseDeps(BaseModel):
    intern_aliases: list[str]
    all_aliases: list[str]
    forbidden_names: list[str]


def _toolset() -> MCPToolset:
    return MCPToolset(
        StdioTransport(command=str(config.PROJECT_ROOT / ".venv" / "bin" / "python"),
                       args=[str(MCP_SERVER)],
                       env={**os.environ, "PYTHONPATH": str(config.PROJECT_ROOT)}),
        id="cohort-pulse",
    )


_analyst: Agent[PulseDeps, ChairBrief] | None = None


def analyst() -> Agent[PulseDeps, ChairBrief]:
    global _analyst
    if _analyst is not None:
        return _analyst
    agent: Agent[PulseDeps, ChairBrief] = Agent(
        config.build_model("match"), deps_type=PulseDeps, output_type=ChairBrief, name="pulse_analyst",
        retries=3,
        instructions=(config.PROMPTS_DIR / "pulse_analyst.md").read_text().format(
            chair=config.ADMIN_NAME, checkin=CHECKIN_CHANNEL),
        toolsets=[_toolset()],
        model_settings=ModelSettings(cache=True, thinking="low", max_tokens=8000),
    )

    @agent.output_validator
    def privacy_guard(ctx: RunContext[PulseDeps], out: ChairBrief) -> ChairBrief:
        blob = out.model_dump_json()
        problems = []
        if EMAIL_RE.search(blob):
            problems.append("it contains an email address")
        if PHONE_RE.search(blob):
            problems.append("it contains a phone number")
        lower = blob.lower()
        leaked = [n for n in ctx.deps.forbidden_names if re.search(rf"\b{re.escape(n.lower())}\b", lower)]
        if leaked:
            problems.append("it contains real names; use pseudonyms only")
        unknown = sorted(set(ALIAS_RE.findall(blob)) - set(ctx.deps.all_aliases))
        if unknown:
            problems.append(f"it mentions pseudonyms that don't exist: {unknown}")
        for n in out.nudges:
            if n.who not in ctx.deps.intern_aliases:
                problems.append(f"nudge target {n.who!r} is not an intern pseudonym")
        if problems:
            if leaked or EMAIL_RE.search(blob) or PHONE_RE.search(blob):
                db.log_safety("admin", "brief_privacy_retry", "; ".join(problems))
            raise ModelRetry("Rewrite the brief: " + "; ".join(problems))
        return out

    _analyst = agent
    return agent


async def generate_brief() -> dict:
    people = People()
    if not people.intern_aliases:
        raise ValueError("No interns yet.")
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM slack_messages").fetchone()[0] == 0 and \
           c.execute("SELECT COUNT(*) FROM meeting_notes").fetchone()[0] == 0:
            raise ValueError("No Slack activity or meeting notes yet. Connect Slack, upload an export, "
                             "or load the demo data.")
    forbidden = sorted({name for name, _ in people.names if len(name) > 3 and _usable_name(name)})
    deps = PulseDeps(intern_aliases=sorted(people.intern_aliases),
                     all_aliases=sorted(people.display), forbidden_names=forbidden)
    started = time.perf_counter()
    async with analyst():
        res = await analyst().run(
            f"Write this week's Chair's Monday Brief. Today is {datetime.now():%A %B %d, %Y}.",
            deps=deps, usage_limits=UsageLimits(request_limit=12, tool_calls_limit=20,
                                                total_tokens_limit=200_000))
    u = res.usage
    model = config.model_name("match")
    cost = config.cost_usd(model, u.input_tokens, u.cache_write_tokens, u.cache_read_tokens, u.output_tokens)
    latency = int((time.perf_counter() - started) * 1000)
    db.log_usage("admin", "pulse_analyst", model, u.requests, u.tool_calls, u.input_tokens,
                 u.cache_write_tokens, u.cache_read_tokens, u.output_tokens, cost, latency, "ok")
    usage = {"model": model, "requests": u.requests, "tool_calls": u.tool_calls,
             "input_tokens": u.input_tokens, "cache_read_tokens": u.cache_read_tokens,
             "output_tokens": u.output_tokens, "cost_usd": cost, "latency_ms": latency}
    week_of = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
    with db.conn() as c:
        bid = c.execute("INSERT INTO briefs (week_of, created_at, brief_json, alias_map_json, usage_json) "
                        "VALUES (?,?,?,?,?)", (week_of, datetime.now().isoformat(timespec="seconds"),
                                               res.output.model_dump_json(), json.dumps(people.display),
                                               json.dumps(usage))).lastrowid
    return get_brief(int(bid))


def get_brief(brief_id: int | None = None) -> dict | None:
    with db.conn() as c:
        r = (c.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)) if brief_id else
             c.execute("SELECT * FROM briefs ORDER BY id DESC LIMIT 1")).fetchone()
    if not r:
        return None
    alias_map = json.loads(r["alias_map_json"])
    raw = json.loads(r["brief_json"])
    named = json.loads(ALIAS_RE.sub(lambda m: alias_map.get(m.group(0), m.group(0)), r["brief_json"]))
    return {"id": r["id"], "week_of": r["week_of"], "created_at": r["created_at"],
            "brief": named, "pseudonymized": raw, "usage": json.loads(r["usage_json"])}


def list_briefs() -> list[dict]:
    with db.conn() as c:
        return [dict(r) for r in c.execute("SELECT id, week_of, created_at FROM briefs ORDER BY id DESC LIMIT 12")]


def brief_markdown(b: dict) -> str:
    x = b["brief"]
    lines = [f"# Chair's Monday Brief, week of {b['week_of']}", "", x["headline"], ""]
    if x["nudges"]:
        lines += ["## Interns who need a quick 1-on-1 nudge", ""]
        for n in x["nudges"]:
            lines += [f"**{n['who']}**: {n['reason']}"] + [f"- {e}" for e in n["evidence"]] + \
                     [f"> Suggested DM: {n['suggested_dm']}", ""]
    if x["wins"]:
        lines += ["## Wins to spotlight at all-hands", ""] + \
                 [f"- **{w['who']}**: {w['what']} _({w['evidence']})_" for w in x["wins"]] + [""]
    if x["blockers"]:
        lines += ["## Blockers to escalate", ""] + \
                 [f"- **{bl['committee']}**: {bl['blocker']} (waiting {bl['waiting_days']:.0f} days"
                  f"{', raised by ' + bl['raised_by'] if bl['raised_by'] else ''}) → {bl['escalate_to']}"
                  for bl in x["blockers"]] + [""]
    if x["watch"]:
        lines += ["## Keep an eye on", ""] + [f"- {w}" for w in x["watch"]] + [""]
    if x["safety_notes"]:
        lines += ["## Safety notes", ""] + [f"- {s}" for s in x["safety_notes"]] + [""]
    lines += [f"_Coverage: {x['coverage']}_"]
    return "\n".join(lines)
