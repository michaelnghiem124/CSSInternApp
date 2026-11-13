"""Guardrails that run before and around every model call.

Layered on purpose. The cheap deterministic checks here run first and cost nothing; the
model-level defenses (the profile agent's injection flag, the matchmaker's output validator
that rejects anyone not on the roster) live in agents.py.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from . import config, db

# Phrases that show up in prompt-injection attempts, including the "hidden white text in a
# resume" trick. Matching one does not block a request on its own: resumes are screened and
# the suspicious lines are removed before the model ever sees them.
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (all |any |the )?(previous|prior|above|system) (instructions|prompts?)",
    r"forget (all |your )?(previous |prior )?(instructions|rules)",
    r"you are now\b",
    r"new (system )?instructions?:",
    r"\bsystem prompt\b",
    r"reveal (your|the) (instructions|prompt|system)",
    r"(print|show|repeat|output) (your|the) (instructions|prompt|system message)",
    r"act as (an? )?(admin|administrator|developer|dan)\b",
    r"developer mode",
    r"jailbreak",
    r"</?(system|instructions?|assistant)>",
    r"(rank|recommend|rate) (me|this candidate) (as )?(the )?(top|best|first|highest)",
    r"this candidate (is|should be) (the )?(best|top|perfect)",
    r"(admin|api)[ _-]?(password|key|token)",
]
_INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)


@dataclass
class ScreenResult:
    clean_text: str
    flagged: bool
    matches: list[str]


def screen(text: str) -> ScreenResult:
    """Remove lines that look like instructions aimed at the model. Returns what was removed."""
    kept, matches = [], []
    for line in text.splitlines():
        m = _INJECTION_RE.search(line)
        if m:
            matches.append(line.strip()[:200])
        else:
            kept.append(line)
    return ScreenResult("\n".join(kept), bool(matches), matches)


def hash_session(raw: str | None) -> str:
    raw = (raw or "anonymous")[:200]
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class RateLimiter:
    """Sliding one-minute window per session. In memory: resets on restart, which is fine
    for a single small server and keeps the dependency list short."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


chat_limiter = RateLimiter(config.RATE_LIMIT_PER_MINUTE)


class Blocked(Exception):
    """A request refused before any tokens were spent. `message` is shown to the user."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def preflight(session: str, text: str, turns_so_far: int) -> None:
    """Checks that must pass before a chat turn is sent to a model. Raises Blocked."""
    if not config.has_llm_key():
        raise Blocked("no_key", "The matchmaker is offline: no API key is configured.")
    if len(text) > config.MAX_MESSAGE_CHARS:
        db.log_safety(session, "too_long", f"{len(text)} chars")
        raise Blocked("too_long", f"Please keep messages under {config.MAX_MESSAGE_CHARS:,} "
                      "characters. Upload long documents with the resume button instead.")
    if turns_so_far >= config.MAX_TURNS_PER_SESSION:
        db.log_safety(session, "turn_limit", str(turns_so_far))
        raise Blocked("turn_limit", "This conversation has reached its length limit. "
                      "Start a new chat to keep going.")
    if not chat_limiter.allow(session):
        db.log_safety(session, "rate_limit", "per-minute limit hit")
        raise Blocked("rate_limit", "You're sending messages quickly. Wait a minute and try again.")
    used = db.tokens_used_today()
    if used >= config.DAILY_TOKEN_BUDGET:
        db.log_safety(session, "budget", f"{used:,} tokens used today")
        raise Blocked("budget", "The matchmaker has used today's budget. It resets at midnight; "
                      "the Directory and Calendar still work.")
