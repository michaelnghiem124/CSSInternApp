"""Configuration: where keys come from, which model runs, what it costs, and the safety limits.

Everything tunable lives here so the tokenomics and safety sections of the report can
point at one file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
PROMPTS_DIR = BACKEND_DIR / "prompts"
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "app.db"))
THINK_TANK_PATH = DATA_DIR / "think_tank.json"
THINK_TANK_RAW_PATH = DATA_DIR / "think_tank_raw.json"

# ---------------------------------------------------------------------------
# Keys: the repo's own .env first, then the folder above it (where the teaching
# staff keep theirs). A variable already set to a non-empty value in the real
# environment always wins. dotenv_values + an explicit "only if empty" check is
# used instead of load_dotenv because some shells export ANTHROPIC_API_KEY="".
# ---------------------------------------------------------------------------
for _env_file in (PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"):
    if _env_file.is_file():
        for _key, _value in dotenv_values(_env_file).items():
            if _value and not os.environ.get(_key):
                os.environ[_key] = _value


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Model provider
# ---------------------------------------------------------------------------
# Anthropic when ANTHROPIC_API_KEY is present (the hosted deployment); otherwise the
# course's Portkey key with gpt-6-luna, so a grader with only the course .env can run it.
# MODEL_PROVIDER=anthropic|portkey forces one.

Provider = Literal["anthropic", "portkey"]

PORTKEY_BASE_URL = os.environ.get("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")
PORTKEY_MODEL = os.environ.get("PORTKEY_MODEL", "gpt-6-luna")

# Two tiers: a cheap model reads resumes and screens input; a stronger one does matching.
ANTHROPIC_MATCH_MODEL = os.environ.get("ANTHROPIC_MATCH_MODEL", "claude-sonnet-5-5")
ANTHROPIC_FAST_MODEL = os.environ.get("ANTHROPIC_FAST_MODEL", "claude-haiku-5-5")


def provider() -> Provider:
    explicit = os.environ.get("MODEL_PROVIDER", "").strip().lower()
    if explicit in ("anthropic", "portkey"):
        return explicit  # type: ignore[return-value]
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("PORTKEY_API_KEY"):
        return "portkey"
    return "anthropic"  # fails later with a clear message


def model_name(tier: Literal["match", "fast"]) -> str:
    if provider() == "portkey":
        return PORTKEY_MODEL
    return ANTHROPIC_MATCH_MODEL if tier == "match" else ANTHROPIC_FAST_MODEL


def has_llm_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PORTKEY_API_KEY"))


def build_model(tier: Literal["match", "fast"]):
    """A PydanticAI model object for the active provider and tier."""
    if provider() == "portkey":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        key = os.environ.get("PORTKEY_API_KEY")
        if not key:
            raise RuntimeError("PORTKEY_API_KEY is not set.")
        return OpenAIChatModel(
            PORTKEY_MODEL, provider=OpenAIProvider(api_key=key, base_url=PORTKEY_BASE_URL)
        )

    from pydantic_ai.models.anthropic import AnthropicModel

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "No API key found. Put ANTHROPIC_API_KEY or PORTKEY_API_KEY in .env "
            "(in this folder or the one above it)."
        )
    return AnthropicModel(model_name(tier))


# ---------------------------------------------------------------------------
# Prices, USD per million tokens: (input, cache_write, cache_read, output).
# Anthropic list prices, Nov 2026. Cache write = 1.25x input (5-minute TTL),
# cache read = 0.1x input. gpt-6-luna is billed to the course key; its row is an
# estimate you can override with PRICE_OVERRIDE="in,cw,cr,out".
# ---------------------------------------------------------------------------
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.00, 2.50, 0.10, 10.00),
    "claude-haiku-5-5": (0.10, 0.125, 0.01, 0.50),
    "claude-opus-5-5": (4.00, 5.00, 0.20, 20.00),
    "gpt-6-luna": (1.25, 1.25, 0.125, 10.00),
}
if os.environ.get("PRICE_OVERRIDE"):
    _p = [float(x) for x in os.environ["PRICE_OVERRIDE"].split(",")]
    PRICES[PORTKEY_MODEL] = (_p[0], _p[1], _p[2], _p[3])


def cost_usd(model: str, input_tokens: int, cache_write: int, cache_read: int, output: int) -> float:
    """Cost of one run. `input_tokens` from PydanticAI includes cached tokens, so split them out."""
    p_in, p_cw, p_cr, p_out = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    uncached = max(input_tokens - cache_write - cache_read, 0)
    return (uncached * p_in + cache_write * p_cw + cache_read * p_cr + output * p_out) / 1_000_000


# ---------------------------------------------------------------------------
# Safety limits
# ---------------------------------------------------------------------------
MAX_MESSAGE_CHARS = _int("MAX_MESSAGE_CHARS", 4_000)        # one chat turn
MAX_RESUME_CHARS = _int("MAX_RESUME_CHARS", 15_000)         # pasted or extracted resume text
MAX_UPLOAD_BYTES = _int("MAX_UPLOAD_BYTES", 2_000_000)      # resume file
MAX_TURNS_PER_SESSION = _int("MAX_TURNS_PER_SESSION", 30)   # one intern's conversation
RATE_LIMIT_PER_MINUTE = _int("RATE_LIMIT_PER_MINUTE", 6)    # chat requests per browser session
DAILY_TOKEN_BUDGET = _int("DAILY_TOKEN_BUDGET", 2_000_000)  # whole app, all users
RUN_REQUEST_LIMIT = _int("RUN_REQUEST_LIMIT", 8)            # model calls inside one agent run
RUN_TOOL_CALL_LIMIT = _int("RUN_TOOL_CALL_LIMIT", 10)
RUN_TOTAL_TOKEN_LIMIT = _int("RUN_TOTAL_TOKEN_LIMIT", 120_000)

# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Michael Nghiem")
ADMIN_TITLE = os.environ.get("ADMIN_TITLE", "Chair, Intern Cyber Security Program")
# The address interns use to reach the site, shown in emails. Set it once deployed
# (e.g. https://intern-hub.onrender.com); otherwise the address of the current request is used.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")

# Written by scripts/set_admin_password.py. No default: admin sign-in is off until set.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()


def _session_secret() -> str:
    """Signs the admin cookie. From the environment when deployed; otherwise generated once
    and kept in data/.session_secret (gitignored) so a restart doesn't sign the admin out."""
    if os.environ.get("SESSION_SECRET"):
        return os.environ["SESSION_SECRET"]
    path = DATA_DIR / ".session_secret"
    try:
        if path.is_file():
            return path.read_text().strip()
        DATA_DIR.mkdir(exist_ok=True)
        secret = os.urandom(32).hex()
        path.write_text(secret)
        path.chmod(0o600)
        return secret
    except OSError:
        return os.urandom(32).hex()


SESSION_SECRET = _session_secret()
