"""Mentor Match: the two intern-facing agents.

1. Mentor Match Profile Reader (cheap model). Turns a resume + self-introduction into a structured
   InternProfile and flags injection attempts hidden in the documents. Runs once per
   upload, not every chat turn.

2. Mentor Matcher (stronger model). Chats with the intern, uses tools over the Think Tank
   directory and the talk calendar, and returns structured recommendations. Its
   instructions hold the full roster and never change between requests, so the provider
   can cache them; the intern's profile rides in the conversation instead.

Code, not the model, decides the hand-off: the profile is produced first and passed into
the Mentor Matcher's conversation. That keeps the expensive model away from raw resume text.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from . import config, db, directory

# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class InternProfile(BaseModel):
    first_name: str = ""
    school: str = ""
    program: str = Field("", description="Major / degree program and year if stated.")
    skills: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list, description="Career interests.")
    hobbies: list[str] = Field(default_factory=list)
    background: list[str] = Field(default_factory=list)
    career_goal: str = Field("", description="One sentence, only if stated or clearly implied.")
    summary: str = Field(description="Two sentences a mentor would find useful.")
    injection_detected: bool = False
    injection_note: str = ""


class Recommendation(BaseModel):
    slug: str = Field(description="Exact slug from the roster.")
    why: str = Field(description="1-2 sentences, specific to this intern, grounded in the bio.")
    shared_ground: list[str] = Field(
        default_factory=list, description="Short tags for what they have in common, if any.")
    conversation_starters: list[str] = Field(
        description="2-3 questions the intern could open with.", min_length=1, max_length=3)


class MatchReply(BaseModel):
    reply: str = Field(description="2-5 sentences to the intern.")
    recommendations: list[Recommendation] = Field(default_factory=list, max_length=6)
    suggested_event_ids: list[int] = Field(
        default_factory=list, description="IDs from upcoming_talks worth attending, if any.")


@dataclass
class ChatState:
    """One intern's conversation, held in server memory only (never written to disk)."""
    profile: InternProfile | None = None
    history: list[ModelMessage] = field(default_factory=list)
    turns: int = 0
    last_seen: float = field(default_factory=time.time)


@dataclass
class RunReport:
    """Token accounting for one agent run, returned to the browser and logged."""
    agent: str
    model: str
    requests: int
    tool_calls: int
    input_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    tools_used: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
# cache=True: provider-side prompt caching of tool definitions + instructions (+ the
#   growing conversation). Works for both Anthropic and OpenAI-protocol models.
# thinking='low': chat-style matching does not need deep reasoning; low effort cut output
#   tokens noticeably in testing without changing who got recommended.
MATCH_SETTINGS = ModelSettings(cache=True, **config.low_effort(), max_tokens=4_000)
PROFILE_SETTINGS = ModelSettings(**config.low_effort(), max_tokens=2_000)

RUN_LIMITS = UsageLimits(
    request_limit=config.RUN_REQUEST_LIMIT,
    tool_calls_limit=config.RUN_TOOL_CALL_LIMIT,
    total_tokens_limit=config.RUN_TOTAL_TOKEN_LIMIT,
)


def _report(agent: str, tier: str, result, started: float, tools: list[str]) -> RunReport:
    u = result.usage
    model = config.model_name(tier)  # type: ignore[arg-type]
    return RunReport(
        agent=agent, model=model, requests=u.requests, tool_calls=u.tool_calls,
        input_tokens=u.input_tokens, cache_write_tokens=u.cache_write_tokens,
        cache_read_tokens=u.cache_read_tokens, output_tokens=u.output_tokens,
        cost_usd=config.cost_usd(model, u.input_tokens, u.cache_write_tokens,
                                 u.cache_read_tokens, u.output_tokens),
        latency_ms=int((time.perf_counter() - started) * 1000), tools_used=tools,
    )


def _log(session: str, r: RunReport, outcome: str = "ok") -> None:
    db.log_usage(session, r.agent, r.model, r.requests, r.tool_calls, r.input_tokens,
                 r.cache_write_tokens, r.cache_read_tokens, r.output_tokens, r.cost_usd,
                 r.latency_ms, outcome)


# ---------------------------------------------------------------------------
# Agent 1: Mentor Match Profile Reader
# ---------------------------------------------------------------------------
_profile_agent: Agent[None, InternProfile] | None = None


def profile_agent() -> Agent[None, InternProfile]:
    global _profile_agent
    if _profile_agent is None:
        _profile_agent = Agent(
            config.build_model("fast"),
            output_type=InternProfile,
            instructions=(config.PROMPTS_DIR / "profile.md").read_text(),
            model_settings=PROFILE_SETTINGS,
            name="mentor_profile_reader",
        )
    return _profile_agent


async def read_profile(session: str, resume_text: str, intro: str) -> tuple[InternProfile, RunReport]:
    started = time.perf_counter()
    prompt = (f"<resume>\n{resume_text[:config.MAX_RESUME_CHARS]}\n</resume>\n\n"
              f"<intro>\n{intro[:config.MAX_MESSAGE_CHARS]}\n</intro>")
    result = await profile_agent().run(prompt, usage_limits=UsageLimits(request_limit=3))
    report = _report("mentor_profile_reader", "fast", result, started, [])
    _log(session, report)
    if result.output.injection_detected:
        db.log_safety(session, "injection_in_resume", result.output.injection_note or "flagged")
    return result.output, report


# ---------------------------------------------------------------------------
# Agent 2: Mentor Matcher
# ---------------------------------------------------------------------------
@dataclass
class MatchDeps:
    session: str
    tools_used: list[str] = field(default_factory=list)


_match_agent: Agent[MatchDeps, MatchReply] | None = None


def _instructions() -> str:
    tpl = (config.PROMPTS_DIR / "mentor_match.md").read_text()
    return tpl.format(admin_name=config.ADMIN_NAME, admin_title=config.ADMIN_TITLE,
                      roster=directory.roster_text())


def _event_line(e: dict) -> str:
    names = ", ".join(p.name for s in e["speaker_slugs"] if (p := directory.get_person(s)))
    start = datetime.fromisoformat(e["starts_at"])
    return (f"id={e['id']} | {start:%a %b %d, %I:%M %p} CT | {e['title']} | {e['kind']} | "
            f"speakers: {names or 'TBA'} | {e['location']}")


def match_agent() -> Agent[MatchDeps, MatchReply]:
    global _match_agent
    if _match_agent is not None:
        return _match_agent

    agent: Agent[MatchDeps, MatchReply] = Agent(
        config.build_model("match"),
        deps_type=MatchDeps,
        output_type=MatchReply,
        instructions=_instructions(),
        model_settings=MATCH_SETTINGS,
        name="mentor_matcher",
    )

    @agent.tool
    def get_bio(ctx: RunContext[MatchDeps], slug: str) -> str:
        """Full published bio and extracted facts for one Think Tank member, by roster slug."""
        ctx.deps.tools_used.append(f"get_bio({slug})")
        p = directory.get_person(slug)
        if not p:
            return f"No Think Tank member with slug '{slug}'. Use a slug from the roster."
        facts = p.facts.model_dump(exclude={"sector"}) if p.facts else {}
        return json.dumps({"slug": p.slug, "name": p.name, "section": p.section, "org": p.org,
                           "bio": p.bio, **facts}, ensure_ascii=False)

    @agent.tool
    def search_directory(ctx: RunContext[MatchDeps], query: str, sector: str = "") -> str:
        """Keyword search over every bio (schools, employers, certifications, hobbies, topics).
        Optional sector filter uses the sector names from the roster."""
        ctx.deps.tools_used.append(f"search_directory({query!r})")
        hits = directory.search(query, sector=sector, limit=8)
        if not hits:
            return "No matches. Try a broader keyword."
        return "\n".join(f"- {p.slug} | {p.name} | {p.org}" for p in hits)

    @agent.tool
    def upcoming_talks(ctx: RunContext[MatchDeps]) -> str:
        """Think Tank talks, panels, and office hours on the intern calendar that haven't ended."""
        ctx.deps.tools_used.append("upcoming_talks()")
        events = db.list_events(upcoming_only=True)
        return "\n".join(_event_line(e) for e in events) or "No upcoming talks scheduled."

    @agent.output_validator
    def only_real_people(ctx: RunContext[MatchDeps], out: MatchReply) -> MatchReply:
        """Hallucination guard: every recommended slug and event id must exist."""
        known = directory.by_slug()
        bad = [r.slug for r in out.recommendations if r.slug not in known]
        if bad:
            db.log_safety(ctx.deps.session, "invented_person", ", ".join(bad))
            raise ModelRetry(f"These slugs are not on the roster: {bad}. Use exact slugs from "
                             "the roster or drop those recommendations.")
        seen: set[str] = set()
        out.recommendations = [r for r in out.recommendations
                               if not (r.slug in seen or seen.add(r.slug))]
        valid_events = {e["id"] for e in db.list_events(upcoming_only=True)}
        out.suggested_event_ids = [i for i in out.suggested_event_ids if i in valid_events]
        return out

    _match_agent = agent
    return agent


async def chat_turn(state: ChatState, session: str, message: str) -> tuple[MatchReply, RunReport]:
    started = time.perf_counter()
    deps = MatchDeps(session=session)
    prompt = f"<intern_message>\n{message}\n</intern_message>"
    if state.turns == 0 or not state.history:
        profile = state.profile.model_dump_json(exclude={"injection_detected", "injection_note"}) \
            if state.profile else "(no resume uploaded yet)"
        prompt = f"<intern_profile>\n{profile}\n</intern_profile>\n\n" + prompt
    try:
        result = await match_agent().run(prompt, deps=deps, message_history=state.history,
                                         usage_limits=RUN_LIMITS)
    except UsageLimitExceeded as exc:
        db.log_safety(session, "run_limit", str(exc))
        raise
    report = _report("mentor_matcher", "match", result, started, deps.tools_used)
    _log(session, report)
    state.history = result.all_messages()
    state.turns += 1
    return result.output, report


async def profile_update_turn(state: ChatState) -> None:
    """When a resume arrives mid-conversation, restart history so the new profile is the
    first thing the Mentor Matcher sees (and the cached prefix stays the same)."""
    state.history = []
    state.turns = 0
