"""The Think Tank directory: load the bios, search them, and render a compact roster.

`data/think_tank_raw.json` is the bios as published on cybersecuritysummit.org.
`data/think_tank.json` adds the structured fields that `scripts/enrich_bios.py` extracts
(title, sector, expertise, interests). The app works from the raw file alone if the
enriched one is missing; matching is just less precise.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field

from .config import THINK_TANK_PATH, THINK_TANK_RAW_PATH

PHOTO_BASE = "https://www.cybersecuritysummit.org/wp-content/uploads/"
PROFILE_BASE = "https://www.cybersecuritysummit.org/speakers/"

Sector = Literal[
    "Financial services",
    "Healthcare & MedTech",
    "Government & public sector",
    "Military & defense",
    "Energy & critical infrastructure",
    "Manufacturing, food & ag",
    "Technology & security vendors",
    "Consulting, legal & audit",
    "Education & nonprofit",
    "Investment & venture",
]


class BioFacts(BaseModel):
    """What the enrichment agent pulls out of one published bio. Only facts stated in the bio."""

    title: str = Field(description="Current job title, short. Empty if the bio doesn't say.")
    sector: Sector = Field(description="The best single sector for their current role.")
    expertise: list[str] = Field(
        description="3-6 short cybersecurity specialties, e.g. 'identity & access management', "
        "'incident response', 'GRC', 'OT/ICS security', 'privacy law'.",
    )
    background: list[str] = Field(
        default_factory=list,
        description="Notable career facts an intern could relate to: military branch, former FBI, "
        "started as a teacher, first-generation, career changer, etc. Only if stated.",
    )
    education: list[str] = Field(default_factory=list, description="Schools and degrees named.")
    certifications: list[str] = Field(default_factory=list)
    personal_interests: list[str] = Field(
        default_factory=list,
        description="Hobbies, volunteering, causes stated in the bio (fishing, mentoring, cooking...).",
    )
    ask_them_about: list[str] = Field(
        description="2-3 concrete topics an intern could open a conversation with, grounded in the bio.",
    )
    is_ciso: bool = Field(description="True if they are or were a CISO / deputy CISO / CSO.")


class Person(BaseModel):
    slug: str
    name: str
    section: str  # Co-chair | Think Tank | Emeritus
    org: str
    bio: str
    photo_url: str
    profile_url: str
    linkedin_url: str = ""
    facts: BioFacts | None = None


def _photo(path: str) -> str:
    return path if path.startswith("http") else PHOTO_BASE + path


@lru_cache(maxsize=1)
def load_people() -> tuple[Person, ...]:
    raw = json.loads(THINK_TANK_RAW_PATH.read_text())
    enriched: dict[str, dict] = {}
    if THINK_TANK_PATH.is_file():
        enriched = {p["slug"]: p for p in json.loads(THINK_TANK_PATH.read_text())}
    people = []
    for r in raw:
        facts = enriched.get(r["slug"], {}).get("facts")
        people.append(
            Person(
                slug=r["slug"],
                name=r["name"],
                section=r["section"],
                org=r["org"],
                bio=r["bio"],
                photo_url=_photo(r["photo"]),
                profile_url=PROFILE_BASE + r["slug"] + "/",
                linkedin_url=("https://www." + r["linkedin"]) if r.get("linkedin") else "",
                facts=BioFacts(**facts) if facts else None,
            )
        )
    return tuple(people)


def by_slug() -> dict[str, Person]:
    return {p.slug: p for p in load_people()}


def get_person(slug: str) -> Person | None:
    return by_slug().get(slug)


def _haystack(p: Person) -> str:
    parts = [p.name, p.org, p.section, p.bio]
    if p.facts:
        f = p.facts
        parts += [f.title, f.sector, *f.expertise, *f.background, *f.education,
                  *f.certifications, *f.personal_interests]
    return " ".join(parts).lower()


def search(query: str = "", sector: str = "", section: str = "", limit: int = 64) -> list[Person]:
    """Keyword search, scored by how many query words appear. Deterministic and free."""
    words = [w for w in re.findall(r"[a-z0-9+#/&.-]+", query.lower()) if len(w) > 1]
    results: list[tuple[int, Person]] = []
    for p in load_people():
        if sector and (not p.facts or p.facts.sector != sector):
            continue
        if section and p.section != section:
            continue
        if not words:
            results.append((0, p))
            continue
        hay = _haystack(p)
        score = sum(hay.count(w) for w in words)
        if score:
            results.append((score, p))
    results.sort(key=lambda t: (-t[0], t[1].name))
    return [p for _, p in results[:limit]]


def roster_text() -> str:
    """One line per person. Goes into the matchmaker's cached instructions, so it must be
    byte-identical between requests: sorted, no timestamps, no per-user data."""
    lines = []
    for p in sorted(load_people(), key=lambda x: x.slug):
        if p.facts:
            f = p.facts
            bits = [f.title or "", p.org, f.sector, "; ".join(f.expertise)]
            if f.background:
                bits.append("bg: " + "; ".join(f.background[:3]))
            if f.personal_interests:
                bits.append("interests: " + "; ".join(f.personal_interests[:4]))
            if f.is_ciso:
                bits.append("CISO")
        else:
            bits = [p.org, p.bio[:160]]
        lines.append(f"- {p.slug} | {p.name} ({p.section}) | " + " | ".join(b for b in bits if b))
    return "\n".join(lines)
