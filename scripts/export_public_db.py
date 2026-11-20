"""Make a copy of the database that is safe to put on a public server: no intern data.

    .venv/bin/python -m scripts.export_public_db [out_path] [--demo-jobs]   (default: data/app-public.db)

Removes every intern account (with photos, resumes, and contact details), RSVPs, password
resets, placements, fit profiles, Slack and meeting-note data, Monday Briefs, and the safety
log (its details can contain emails), and drops the program access code so the new server
generates its own. Keeps the Think Tank Talks and huddles, job postings and their labels,
committees and Cyber Coach pool. Announcements and the AI usage log are cleared too, so a new
deployment starts clean for real interns (the local database keeps its usage log for the report).
`--demo-jobs` adds 5 clearly fictional "(demo)" listings in the Adzuna slot (off by default since
2026-11-20, when the live site opened to real interns).

Then it checks the copy for any intern's full name, email, or phone number from the source
database and refuses to finish if one is found. The output is gitignored (data/*.db).
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from backend import config

CLEAR = ["announcements", "usage_log", "rsvps", "password_resets", "reset_requests", "placements", "fit_profiles", "match_runs",
         "slack_users", "slack_messages", "meeting_notes", "briefs", "safety_log", "users"]


def main(out: Path, demo_jobs: bool = False) -> None:
    src = sqlite3.connect(config.DB_PATH)
    people = src.execute("SELECT first_name, last_name, slack_email, phone FROM users").fetchall()
    if out.exists():
        out.unlink()
    dst = sqlite3.connect(out)
    src.backup(dst)
    src.close()

    tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in CLEAR:
        if t in tables:
            dst.execute(f"DELETE FROM {t}")
    dst.execute("DELETE FROM settings WHERE key IN ('access_code', 'admin_calendar_token', 'adzuna_calls', 'adzuna_last_run')")
    # Adzuna's terms: its data must be removable if API access ends, so it never goes into a public
    # file. A deployment with ADZUNA_APP_ID/ADZUNA_APP_KEY fetches its own on the next refresh.
    dst.execute("DELETE FROM job_postings WHERE platform = 'adzuna'")
    dst.execute("DELETE FROM job_postings WHERE id LIKE 'demo|%'")
    if demo_jobs:  # optional: clearly fictional listings so graders see how Adzuna roles appear
        from backend import demo_jobs as demo, opportunities  # noqa: PLC0415
        demo.add_demo_postings(dst, opportunities.CLASSIFIER_VERSION)
    dst.commit()
    dst.execute("VACUUM")

    # Leak check: full names, emails, and phone digits of every account in the source.
    needles = set()
    for first, last, email, phone in people:
        if first and last:
            needles.add(f"{first} {last}".lower())
        if email:
            needles.add(email.lower())
        digits = re.sub(r"\D", "", phone or "")
        if len(digits) >= 10:
            needles.add(digits[-10:])
    leaks = []
    for t in [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
        for row in dst.execute(f"SELECT * FROM {t}"):
            text = " ".join(str(v) for v in row if isinstance(v, (str, int))).lower()
            flat = re.sub(r"\D", "", text)
            leaks += [(t, n) for n in needles if n in text or (n.isdigit() and n in flat)]
    counts = {t: dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("users", "events", "job_postings", "committees", "coaches", "usage_log")}
    dst.close()
    if leaks:
        out.unlink()
        raise SystemExit(f"Stopped: intern data still found in {sorted({t for t, _ in leaks})}. Nothing written.")
    print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB). No intern names, emails, or phone numbers found.")
    print("Rows:", counts)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(Path(args[0]) if args else config.DATA_DIR / "app-public.db", demo_jobs="--demo-jobs" in sys.argv)
