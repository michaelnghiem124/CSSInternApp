"""Cohort MCP server: the Pulse Analyst's only window into Slack and meeting notes.

Every tool is read-only and returns a privacy-safe view built by backend/pulse.py:
pseudonyms instead of names, no emails / phone numbers / links, and messages that look
like instructions to an AI are withheld and marked [FLAGGED]. The raw data never crosses
this boundary, so even a successful prompt injection has nothing private to leak.

Run standalone for debugging:  .venv/bin/python mcp_server/cohort_server.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP  # noqa: E402

from backend import pulse  # noqa: E402

mcp = FastMCP("cohort-pulse")


@mcp.tool
def get_cohort_signals() -> str:
    """Participation signals for every intern over the last few weeks, computed from Slack:
    days since last post, posts this week vs last week, whether they posted in the weekly
    check-in channel this week and last week, unanswered questions, and flagged messages.
    Start here."""
    return json.dumps(pulse.view_signals(), indent=1)


@mcp.tool
def list_channels() -> str:
    """Slack channels with activity in the window, with message counts."""
    rows = pulse.view_channels()
    for r in rows:
        r["last_activity"] = pulse._day(r.pop("last_ts")) if r.get("last_ts") else None
    return json.dumps(rows, indent=1)


@mcp.tool
def get_channel_messages(channel: str, days: int = 14) -> str:
    """Messages from one channel (pseudonymized and redacted), oldest first. days: 1-21."""
    return json.dumps(pulse.view_messages(channel, days), indent=1)


@mcp.tool
def get_open_threads() -> str:
    """Questions and blockers that got no reply for more than 2 days."""
    return json.dumps(pulse.view_open_threads(), indent=1)


@mcp.tool
def get_meeting_notes(days: int = 21) -> str:
    """Committee meeting notes from the last N days (pseudonymized), including action items
    with owners and due dates."""
    return json.dumps(pulse.view_notes(days), indent=1)


if __name__ == "__main__":
    mcp.run()
