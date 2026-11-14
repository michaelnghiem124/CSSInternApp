"""Committee & Cyber Coach Matchmaker (admin only).

Three agents, orchestrated by code rather than by a model:

1. Fit Extractor (cheap model, one call per intern, cached): turns a registration profile
   into an InternFit: skills, cyber domains, strengths, experience level, committee
   preferences, and the times they can't meet. Re-runs only when the profile changes.
2. Placement Matcher (stronger model, one call for the whole cohort): scores each intern
   against every committee's needs, the schedule constraints, and the Cyber Coach pool,
   and returns a ranked AssignmentMatrix with one-sentence "why this match works" lines.
   Code computes the hard schedule conflicts first; an output validator rejects any
   assignment that breaks capacity, uses a conflicting meeting time, or names a committee
   or coach that doesn't exist, and makes the model fix it.
3. Intro Drafter (cheap model, on demand): drafts the intro email + Slack DM to the intern
   and to their committee director. Drafts only; nothing is sent.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import json
import time
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from . import config, db, directory

# ---------------------------------------------------------------------------
# Seed data: Summit committees (from the intern program's published roles) + coach pool
# ---------------------------------------------------------------------------
DEFAULT_COMMITTEES = [
    ("marketing", "Marketing, Blog, Cyber Pro Podcast & Social Media",
     "Promotes the Summit year-round: blog features, the Cyber Pro podcast, and social channels.",
     "writing, social media, podcast or video editing, graphic design, storytelling"),
    ("logistics", "Summit Logistics & Event Planning",
     "Plans and runs the October Summit on the ground: venue, schedules, vendors, day-of operations.",
     "organization, detail orientation, vendor coordination, calm under pressure"),
    ("general-session", "General Session Programming",
     "Shapes the main-stage agenda: researches topics and speakers and preps session content.",
     "research, current cyber trends, writing briefs, interest in public speaking"),
    ("golf", "Annual Golf Event",
     "Runs the sponsor golf fundraiser: registrations, sponsor relations, event-day logistics.",
     "sponsor relations, event operations, networking, organization"),
    ("partnerships", "Professional Organization Partnerships",
     "Builds ties with ISSA, ISC2, InfraGard, ISACA and other professional associations.",
     "outreach, relationship building, networking, follow-through"),
    ("cxo-connect", "CXO Connect",
     "Executive programming for CISOs and C-suite attendees.",
     "business acumen, executive communication, professional polish, risk and governance interest"),
    ("medtech", "MedTech Seminar",
     "The medical device and healthcare security seminar.",
     "healthcare or medical device security interest, regulation (FDA, HIPAA), biology or health background"),
    ("public-sector", "Public Sector Seminar & Village",
     "Government, military, and critical-infrastructure programming and the Public Sector Village.",
     "government or military interest, policy, critical infrastructure, clearance-eligible background"),
    ("international", "International Programming",
     "Global cybersecurity perspectives and international speakers.",
     "global affairs, languages, international relations, cross-cultural communication"),
    ("scholarships", "Scholarships",
     "Student scholarship program: outreach to schools and reviewing applications.",
     "education outreach, fairness, reviewing applications, campus connections"),
    ("speaker-support", "Speaker Support",
     "Supports speakers before and during the Summit: logistics, slides, AV, green room.",
     "communication, hospitality, AV or tech comfort, reliability"),
    ("tech-sessions", "Tech Sessions",
     "Hands-on technical breakout sessions.",
     "technical depth, hands-on security tools, cloud, network or application security"),
    ("vla", "Visionary Leadership Awards",
     "Researches nominees and supports the Visionary Leadership Awards.",
     "research, writing, interviewing, attention to detail"),
    ("website-app", "Website & Summit App Support",
     "Keeps the website and attendee app current.",
     "web development, content management, UX, data entry accuracy"),
    ("competition", "Competition Support (Capture the Flag)",
     "Builds and runs the Summit Capture the Flag and other competitions.",
     "CTF experience, offensive or defensive security skills, Linux, scripting"),
    ("legislative", "Legislative Program",
     "Tracks cyber legislation and supports policy programming with lawmakers.",
     "policy, law, government relations, writing policy summaries"),
    ("innovation-showcase", "Cyber Innovation Showcase",
     "Spotlights cybersecurity startups and emerging products at the Summit.",
     "startups, product thinking, venture or entrepreneurship interest, demos"),
]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def seed_defaults() -> None:
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM committees").fetchone()[0] == 0:
            for i, (slug, name, desc, needs) in enumerate(DEFAULT_COMMITTEES):
                c.execute("INSERT INTO committees (slug, name, description, needs, capacity, sort) "
                          "VALUES (?,?,?,?,2,?)", (slug, name, desc, needs, i))
        if c.execute("SELECT COUNT(*) FROM coaches").fetchone()[0] == 0:
            for p in directory.load_people():
                if p.section in ("Co-chair", "Think Tank"):
                    c.execute("INSERT INTO coaches (slug, capacity, active) VALUES (?, 2, 1)", (p.slug,))


# ---------------------------------------------------------------------------
# Committees + coaches (admin-editable)
# ---------------------------------------------------------------------------
COMMITTEE_FIELDS = ("name", "description", "needs", "capacity", "director_slug", "director_name",
                    "director_email", "meeting_day", "meeting_time", "meeting_note", "active")


def list_committees(active_only: bool = False) -> list[dict]:
    with db.conn() as c:
        q = "SELECT * FROM committees" + (" WHERE active = 1" if active_only else "")
        rows = [dict(r) for r in c.execute(q + " ORDER BY sort, name")]
    people = directory.by_slug()
    for r in rows:
        r["active"] = bool(r["active"])
        p = people.get(r["director_slug"])
        r["director_display"] = p.name if p else (r["director_name"] or "")
    return rows


def update_committee(slug: str, data: dict) -> None:
    keys = [k for k in COMMITTEE_FIELDS if k in data]
    with db.conn() as c:
        c.execute(f"UPDATE committees SET {', '.join(f'{k} = ?' for k in keys)} WHERE slug = ?",
                  (*(int(data[k]) if k == "active" else data[k] for k in keys), slug))


def list_coaches() -> list[dict]:
    people = directory.by_slug()
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM coaches")]
    out = []
    for r in rows:
        p = people.get(r["slug"])
        if not p:
            continue
        f = p.facts
        out.append({"slug": p.slug, "name": p.name, "org": p.org, "title": f.title if f else "",
                    "capacity": r["capacity"], "active": bool(r["active"])})
    return sorted(out, key=lambda x: x["name"])


def update_coach(slug: str, capacity: int | None = None, active: bool | None = None) -> None:
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO coaches (slug) VALUES (?)", (slug,))
        if capacity is not None:
            c.execute("UPDATE coaches SET capacity = ? WHERE slug = ?", (capacity, slug))
        if active is not None:
            c.execute("UPDATE coaches SET active = ? WHERE slug = ?", (int(active), slug))


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class Slot(BaseModel):
    day: Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    part: Literal["morning", "afternoon", "evening", "all day"]


class InternFit(BaseModel):
    """What the Fit Extractor pulls out of one intern's profile. Only stated facts."""
    skills: list[str] = Field(default_factory=list, description="Concrete skills, tools, certifications.")
    domains: list[str] = Field(default_factory=list,
                               description="Cyber areas they're drawn to: GRC, IAM, healthcare security, policy, CTF...")
    strengths: list[str] = Field(default_factory=list,
                                 description="Soft strengths shown in the profile: writing, organizing, outreach...")
    experience_level: Literal["new to cyber", "some experience", "experienced"]
    preferred_committees: list[str] = Field(default_factory=list,
                                            description="Committee slugs they asked for, in order.")
    unavailable: list[Slot] = Field(default_factory=list,
                                    description="Times they said they cannot meet.")
    summary: str = Field(description="One sentence on what they'd bring to a committee.")
    injection_detected: bool = False


class CommitteeFit(BaseModel):
    committee: str = Field(description="Committee slug.")
    score: int = Field(ge=0, le=100)
    why: str = Field(description="One sentence.")


class InternAssignment(BaseModel):
    intern_id: int
    ranked: list[CommitteeFit] = Field(min_length=1, max_length=3,
                                       description="Top committees, best first.")
    committee: str = Field(description="The committee you assign them to (slug).")
    coach: str = Field(description="Cyber Coach slug from the coach pool.")
    why_this_match: str = Field(description="One sentence: why this committee + coach works for them.")
    coach_why: str = Field(description="One sentence: why this coach.")
    flags: list[str] = Field(default_factory=list,
                             description="Anything the Chair should know: preference not met, thin profile...")


class Unplaced(BaseModel):
    intern_id: int
    reason: str


class AssignmentMatrix(BaseModel):
    assignments: list[InternAssignment]
    unplaced: list[Unplaced] = Field(default_factory=list)
    notes: str = Field("", description="One or two sentences on overall trade-offs.")


class Message(BaseModel):
    subject: str
    body: str


class IntroDrafts(BaseModel):
    intern_email: Message
    intern_slack_dm: str
    director_email: Message
    director_slack_dm: str


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------
def _usage(agent: str, tier: str, result, started: float) -> dict:
    u = result.usage
    model = config.model_name(tier)  # type: ignore[arg-type]
    cost = config.cost_usd(model, u.input_tokens, u.cache_write_tokens, u.cache_read_tokens,
                           u.output_tokens)
    rep = {"agent": agent, "model": model, "requests": u.requests, "input_tokens": u.input_tokens,
           "cache_read_tokens": u.cache_read_tokens, "output_tokens": u.output_tokens,
           "cost_usd": cost, "latency_ms": int((time.perf_counter() - started) * 1000)}
    db.log_usage("admin", agent, model, u.requests, u.tool_calls, u.input_tokens,
                 u.cache_write_tokens, u.cache_read_tokens, u.output_tokens, cost,
                 rep["latency_ms"], "ok")
    return rep


def _sum_usage(reports: list[dict]) -> dict:
    keys = ("input_tokens", "cache_read_tokens", "output_tokens", "cost_usd", "requests")
    return {k: sum(r[k] for r in reports) for k in keys} | {"calls": len(reports)}


# ---------------------------------------------------------------------------
# Agent 1: Fit Extractor
# ---------------------------------------------------------------------------
_extractor: Agent[None, InternFit] | None = None


def extractor() -> Agent[None, InternFit]:
    global _extractor
    if _extractor is None:
        _extractor = Agent(
            config.build_model("fast"), output_type=InternFit, name="fit_extractor",
            instructions=(config.PROMPTS_DIR / "fit_extractor.md").read_text(),
            model_settings=ModelSettings(**config.low_effort(), max_tokens=1500),
        )
    return _extractor


def _profile_hash(u: dict) -> str:
    keys = ("school", "interests", "hobbies", "goals", "fun_fact", "committee_prefs", "availability")
    return hashlib.sha256(json.dumps([u.get(k) for k in keys]).encode()).hexdigest()[:16]


async def extract_fit(u: dict, committee_slugs: set[str]) -> tuple[InternFit, dict | None]:
    """Cached by profile hash: unchanged interns cost nothing on re-runs."""
    h = _profile_hash(u)
    with db.conn() as c:
        row = c.execute("SELECT source_hash, profile_json FROM fit_profiles WHERE user_id = ?",
                        (u["id"],)).fetchone()
    if row and row["source_hash"] == h:
        return InternFit.model_validate_json(row["profile_json"]), None

    from . import safety  # noqa: PLC0415
    prefs = json.loads(u.get("committee_prefs") or "[]")
    fields = {k: safety.screen(str(u.get(k) or "")).clean_text for k in
              ("school", "interests", "hobbies", "goals", "fun_fact", "availability")}
    prompt = (f"<profile>\nSchool or employer: {fields['school']}\nInterests: {fields['interests']}\n"
              f"Hobbies: {fields['hobbies']}\nGoals for the program: {fields['goals']}\n"
              f"Fun fact: {fields['fun_fact']}\nCommittee preferences (slugs, in order): {prefs}\n"
              f"Availability notes: {fields['availability']}\n</profile>")
    started = time.perf_counter()
    res = await extractor().run(prompt, usage_limits=UsageLimits(request_limit=3))
    fit = res.output
    fit.preferred_committees = [s for s in (prefs or fit.preferred_committees) if s in committee_slugs][:3]
    with db.conn() as c:
        c.execute("INSERT INTO fit_profiles (user_id, source_hash, profile_json, created_at) "
                  "VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET source_hash = excluded.source_hash, "
                  "profile_json = excluded.profile_json, created_at = excluded.created_at",
                  (u["id"], h, fit.model_dump_json(), datetime.now().isoformat(timespec="seconds")))
    return fit, _usage("fit_extractor", "fast", res, started)


# ---------------------------------------------------------------------------
# Deterministic constraints
# ---------------------------------------------------------------------------
def _meeting_part(hhmm: str) -> str:
    try:
        h = int(hhmm.split(":")[0])
    except (ValueError, IndexError):
        return ""
    return "morning" if h < 12 else "afternoon" if h < 17 else "evening"


def schedule_conflicts(fit: InternFit, committees: list[dict]) -> list[str]:
    """Committee slugs whose meeting time the intern said they can't make."""
    bad = []
    for cm in committees:
        day, part = cm["meeting_day"], _meeting_part(cm["meeting_time"])
        if not day or not part:
            continue
        for s in fit.unavailable:
            if s.day == day and s.part in ("all day", part):
                bad.append(cm["slug"])
                break
    return bad


# ---------------------------------------------------------------------------
# Agent 2: Placement Matcher
# ---------------------------------------------------------------------------
class MatchDeps(BaseModel):
    intern_ids: list[int]
    committee_capacity: dict[str, int]
    coach_capacity: dict[str, int]
    conflicts: dict[int, list[str]]


_matcher: Agent[MatchDeps, AssignmentMatrix] | None = None


def matcher() -> Agent[MatchDeps, AssignmentMatrix]:
    global _matcher
    if _matcher is not None:
        return _matcher
    agent: Agent[MatchDeps, AssignmentMatrix] = Agent(
        config.build_model("match"), deps_type=MatchDeps, output_type=AssignmentMatrix,
        name="placement_matcher",
        instructions=(config.PROMPTS_DIR / "placement_matcher.md").read_text(),
        model_settings=ModelSettings(cache=True, **config.low_effort(), max_tokens=16_000),
    )

    @agent.output_validator
    def enforce_constraints(ctx: RunContext[MatchDeps], out: AssignmentMatrix) -> AssignmentMatrix:
        d, problems = ctx.deps, []
        seen = [a.intern_id for a in out.assignments] + [u.intern_id for u in out.unplaced]
        missing = sorted(set(d.intern_ids) - set(seen))
        unknown = sorted(set(seen) - set(d.intern_ids))
        dupes = sorted({i for i in seen if seen.count(i) > 1})
        if missing:
            problems.append(f"interns {missing} are neither assigned nor unplaced")
        if unknown:
            problems.append(f"intern ids {unknown} don't exist")
        if dupes:
            problems.append(f"interns {dupes} appear more than once")
        c_count: dict[str, int] = {}
        k_count: dict[str, int] = {}
        for a in out.assignments:
            if a.committee not in d.committee_capacity:
                problems.append(f"intern {a.intern_id}: committee '{a.committee}' isn't an active committee")
            if a.coach not in d.coach_capacity:
                problems.append(f"intern {a.intern_id}: coach '{a.coach}' isn't in the coach pool")
            if a.committee in d.conflicts.get(a.intern_id, []):
                problems.append(f"intern {a.intern_id} can't make {a.committee}'s meeting time")
            for r in a.ranked:
                if r.committee not in d.committee_capacity:
                    problems.append(f"intern {a.intern_id}: ranked committee '{r.committee}' doesn't exist")
            c_count[a.committee] = c_count.get(a.committee, 0) + 1
            k_count[a.coach] = k_count.get(a.coach, 0) + 1
        for slug, n in c_count.items():
            if slug in d.committee_capacity and n > d.committee_capacity[slug]:
                problems.append(f"committee '{slug}' has {n} interns but capacity {d.committee_capacity[slug]}")
        for slug, n in k_count.items():
            if slug in d.coach_capacity and n > d.coach_capacity[slug]:
                problems.append(f"coach '{slug}' has {n} interns but capacity {d.coach_capacity[slug]}")
        if problems:
            raise ModelRetry("Fix these and return the full matrix again: " + "; ".join(problems[:15]))
        return out

    _matcher = agent
    return agent


def _coach_lines(coaches: list[dict]) -> str:
    people = directory.by_slug()
    lines = []
    for c in coaches:
        p = people[c["slug"]]
        f = p.facts
        bits = [f.title if f else "", p.org, f.sector if f else "", "; ".join((f.expertise if f else [])[:4])]
        if f and f.background:
            bits.append("bg: " + "; ".join(f.background[:2]))
        if f and f.personal_interests:
            bits.append("interests: " + "; ".join(f.personal_interests[:3]))
        lines.append(f"- {p.slug} (capacity {c['capacity']}) | {p.name} | " + " | ".join(b for b in bits if b))
    return "\n".join(lines)


async def run_matching(user_ids: list[int] | None = None) -> dict:
    committees = list_committees(active_only=True)
    if not committees:
        raise ValueError("Turn on at least one committee first.")
    coaches = [c for c in list_coaches() if c["active"] and c["capacity"] > 0]
    if not coaches:
        raise ValueError("Add at least one active Cyber Coach first.")
    users = [u for u in db.list_users() if (user_ids is None or u["id"] in user_ids)]
    if not users:
        raise ValueError("No interns to place yet.")
    slugs = {c["slug"] for c in committees}

    # Agent 1, in parallel and cached.
    sem = asyncio.Semaphore(6)

    async def one(u):
        async with sem:
            return u, *(await extract_fit(u, slugs))

    extracted = await asyncio.gather(*(one(u) for u in users))
    reports = [r for _, _, r in extracted if r]
    fits = {u["id"]: (u, fit) for u, fit, _ in extracted}
    conflicts = {uid: schedule_conflicts(fit, committees) for uid, (u, fit) in fits.items()}

    # Agent 2, one call for the whole cohort. Names stay out of the prompt; ids only.
    cohort = [{"intern_id": uid, "school_or_employer": u.get("school") or "",
               "fit": fit.model_dump(exclude={"injection_detected"}),
               "cannot_attend_committees": conflicts[uid]}
              for uid, (u, fit) in fits.items()]
    committee_view = [{k: c[k] for k in ("slug", "name", "description", "needs", "capacity")}
                      | {"meeting": " ".join(x for x in (c["meeting_day"], c["meeting_time"],
                                                          c["meeting_note"]) if x) or "not set"}
                      for c in committees]
    prompt = (f"<committees>\n{json.dumps(committee_view, indent=1)}\n</committees>\n\n"
              f"<cyber_coaches>\n{_coach_lines(coaches)}\n</cyber_coaches>\n\n"
              f"<interns>\n{json.dumps(cohort, indent=1)}\n</interns>\n\n"
              f"Total committee seats: {sum(c['capacity'] for c in committees)}. "
              f"Interns to place: {len(cohort)}.")
    deps = MatchDeps(intern_ids=list(fits), conflicts=conflicts,
                     committee_capacity={c["slug"]: c["capacity"] for c in committees},
                     coach_capacity={c["slug"]: c["capacity"] for c in coaches})
    started = time.perf_counter()
    res = await matcher().run(prompt, deps=deps, usage_limits=UsageLimits(request_limit=4))
    reports.append(_usage("placement_matcher", "match", res, started))
    matrix = res.output

    usage = {"by_call": reports, "total": _sum_usage(reports),
             "cached_profiles": len(users) - sum(1 for r in reports if r["agent"] == "fit_extractor")}
    now = datetime.now().isoformat(timespec="seconds")
    with db.conn() as c:
        run_id = c.execute("INSERT INTO match_runs (created_at, matrix_json, usage_json) VALUES (?,?,?)",
                           (now, matrix.model_dump_json(), json.dumps(usage))).lastrowid
        for a in matrix.assignments:
            c.execute("INSERT INTO placements (user_id, committee_slug, coach_slug, why, status, run_id, "
                      "updated_at) VALUES (?,?,?,?, 'proposed', ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                      "committee_slug = excluded.committee_slug, coach_slug = excluded.coach_slug, "
                      "why = excluded.why, status = 'proposed', drafts_json = '', run_id = excluded.run_id, "
                      "updated_at = excluded.updated_at WHERE placements.status != 'approved'",
                      (a.intern_id, a.committee, a.coach, a.why_this_match, run_id, now))
    return latest_run()


def latest_run() -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT * FROM match_runs ORDER BY id DESC LIMIT 1").fetchone()
        placements = {row["user_id"]: dict(row) for row in c.execute("SELECT * FROM placements")}
    if not r:
        return None
    matrix = json.loads(r["matrix_json"])
    users = {u["id"]: u for u in db.list_users()}
    committees = {c["slug"]: c for c in list_committees()}
    people = directory.by_slug()
    rows = []
    for a in matrix["assignments"]:
        u = users.get(a["intern_id"])
        if not u:
            continue
        p = placements.get(a["intern_id"], {})
        committee = p.get("committee_slug") or a["committee"]
        coach = p.get("coach_slug") or a["coach"]
        rows.append({
            **a,
            "name": f"{u['first_name']} {u['last_name']}", "email": u["slack_email"],
            "school": u.get("school") or "", "is_sample": bool(u.get("is_sample")),
            "final_committee": committee, "final_coach": coach,
            "committee_name": committees.get(committee, {}).get("name", committee),
            "coach_name": people[coach].name if coach in people else coach,
            "status": p.get("status", "proposed"),
            "overridden": committee != a["committee"] or coach != a["coach"],
            "has_drafts": bool(p.get("drafts_json")),
            "ranked": [r | {"name": committees.get(r["committee"], {}).get("name", r["committee"])}
                       for r in a["ranked"]],
        })
    unplaced = [{**x, "name": f"{users[x['intern_id']]['first_name']} {users[x['intern_id']]['last_name']}"}
                for x in matrix.get("unplaced", []) if x["intern_id"] in users]
    # The model only knows intern ids; show the Chair names instead.
    def _name(m: re.Match) -> str:
        u = users.get(int(m.group(1)))
        return f"{u['first_name']} {u['last_name']}" if u else m.group(0)

    notes = re.sub(r"\b[Ii]ntern(?:_id)?\s*#?(\d+)\b", _name, matrix.get("notes", ""))
    for row in rows:
        row["flags"] = [re.sub(r"\b[Ii]ntern(?:_id)?\s*#?(\d+)\b", _name, f) for f in row["flags"]]
    return {"run_id": r["id"], "created_at": r["created_at"], "rows": rows, "unplaced": unplaced,
            "notes": notes, "usage": json.loads(r["usage_json"])}


def set_placement(user_id: int, committee: str | None = None, coach: str | None = None,
                  status: str | None = None) -> None:
    with db.conn() as c:
        if committee is not None:
            c.execute("UPDATE placements SET committee_slug = ?, drafts_json = '', updated_at = ? "
                      "WHERE user_id = ?", (committee, datetime.now().isoformat(timespec="seconds"), user_id))
        if coach is not None:
            c.execute("UPDATE placements SET coach_slug = ?, drafts_json = '', updated_at = ? "
                      "WHERE user_id = ?", (coach, datetime.now().isoformat(timespec="seconds"), user_id))
        if status is not None:
            c.execute("UPDATE placements SET status = ? WHERE user_id = ?", (status, user_id))


# ---------------------------------------------------------------------------
# Agent 3: Intro Drafter
# ---------------------------------------------------------------------------
_drafter: Agent[None, IntroDrafts] | None = None


def drafter() -> Agent[None, IntroDrafts]:
    global _drafter
    if _drafter is None:
        _drafter = Agent(
            config.build_model("fast"), output_type=IntroDrafts, name="intro_drafter",
            instructions=(config.PROMPTS_DIR / "intro_drafter.md").read_text().format(
                chair=config.ADMIN_NAME, chair_title=config.ADMIN_TITLE),
            model_settings=ModelSettings(**config.low_effort(), max_tokens=3000),
        )
    return _drafter


async def draft_intros(user_id: int) -> dict:
    with db.conn() as c:
        p = c.execute("SELECT * FROM placements WHERE user_id = ?", (user_id,)).fetchone()
    if not p:
        raise ValueError("Run the matchmaker first.")
    u = db.get_user(user_id)
    cm = {c["slug"]: c for c in list_committees()}.get(p["committee_slug"])
    coach = directory.get_person(p["coach_slug"])
    if not (u and cm):
        raise ValueError("That placement is out of date. Re-run the matchmaker.")
    run = latest_run() or {"rows": []}
    row = next((r for r in run["rows"] if r["intern_id"] == user_id), {})
    director = cm["director_display"] or "the committee director (to be named)"
    meeting = " ".join(x for x in (cm["meeting_note"], cm["meeting_day"], cm["meeting_time"]) if x) or "to be scheduled"
    prompt = (
        f"<intern>First name: {u['first_name']}\nLast name: {u['last_name']}\n"
        f"School or employer: {u.get('school') or ''}\nInterests: {u.get('interests') or ''}\n"
        f"Goals: {u.get('goals') or ''}\nFun fact: {u.get('fun_fact') or ''}</intern>\n"
        f"<committee>Name: {cm['name']}\nWhat it does: {cm['description']}\nDirector: {director}\n"
        f"Meets: {meeting}</committee>\n"
        f"<cyber_coach>{coach.name + ', ' + ((coach.facts.title + ', ') if coach and coach.facts and coach.facts.title else '') + coach.org if coach else 'to be assigned'}</cyber_coach>\n"
        f"<why_this_match>{p['why']}</why_this_match>\n"
        f"<coach_why>{row.get('coach_why', '')}</coach_why>"
    )
    started = time.perf_counter()
    res = await drafter().run(prompt, usage_limits=UsageLimits(request_limit=3))
    usage = _usage("intro_drafter", "fast", res, started)
    drafts = res.output.model_dump() | {"director": director, "committee": cm["name"],
                                         "intern_email_to": u["slack_email"],
                                         "director_email_to": cm["director_email"]}
    with db.conn() as c:
        c.execute("UPDATE placements SET drafts_json = ? WHERE user_id = ?", (json.dumps(drafts), user_id))
    return {"drafts": drafts, "usage": usage}


def get_drafts(user_id: int) -> dict | None:
    with db.conn() as c:
        r = c.execute("SELECT drafts_json FROM placements WHERE user_id = ?", (user_id,)).fetchone()
    return json.loads(r[0]) if r and r[0] else None
