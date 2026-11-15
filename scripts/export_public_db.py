"""Make a copy of the database that is safe to put on a public server: no intern data.

    .venv/bin/python -m scripts.export_public_db [out_path]      (default: data/app-public.db)

Removes every intern account (with photos, resumes, and contact details), RSVPs, password
resets, placements, fit profiles, Slack and meeting-note data, Monday Briefs, and the safety
log (its details can contain emails), and drops the program access code so the new server
generates its own. Keeps the Think Tank Talks and huddles, job postings and their labels,
committees and Cyber Coach pool, announcements, and the anonymous AI usage log.

Then it checks the copy for any intern's full name, email, or phone number from the source
database and refuses to finish if one is found. The output is gitignored (data/*.db).
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from backend import config

CLEAR = ["rsvps", "password_resets", "reset_requests", "placements", "fit_profiles", "match_runs",
         "slack_users", "slack_messages", "meeting_notes", "briefs", "safety_log", "users"]


def main(out: Path) -> None:
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
    dst.execute("DELETE FROM settings WHERE key IN ('access_code', 'admin_calendar_token')")
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
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else config.DATA_DIR / "app-public.db")
