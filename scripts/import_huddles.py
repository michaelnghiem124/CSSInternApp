"""Import intern monthly huddles from the program's tracking sheet (CSV export) as Events.

    .venv/bin/python -m scripts.import_huddles path/to/huddles.csv

Expects the "2026 intern monthly huddles" tab exported as CSV: a header row with Date,
Time (CT), Organizer / Host, Platform, Status, ... Only the date, time, platform, and status
are used. The Notes and Organizer columns are NOT imported: they can contain interns' names
and email addresses, which stay out of the Hub's events. Canceled huddles are skipped, and a
huddle already in the Hub (same date and time) is not added twice.
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import datetime

from backend import db

KIND = "Intern huddle"
TITLE = "Intern monthly huddle"
DESCRIPTION = "Monthly check-in for the intern cohort with the Program Co-Chairs: updates, questions, and what's coming up."


def _time(date: str, text: str) -> tuple[str, str]:
    """'5:30 PM – 6:30 PM' on 2026-05-06 -> ('2026-05-06T17:30', '2026-05-06T18:30')."""
    parts = [p.strip() for p in re.split(r"[–—-]", text) if p.strip()]
    start, end = (datetime.strptime(f"{date} {p}", "%Y-%m-%d %I:%M %p") for p in parts[:2])
    return start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")


def main(path: str) -> None:
    db.init()
    rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
    header_i = next(i for i, r in enumerate(rows) if any(c.strip() == "Date" for c in r))
    header = [c.strip() for c in rows[header_i]]
    col = {name: header.index(name) for name in ("Date", "Time (CT)", "Platform", "Status")}
    existing = {(e["kind"], e["starts_at"]) for e in db.list_events()}
    added = skipped = 0
    for r in rows[header_i + 1:]:
        date = r[col["Date"]].strip() if len(r) > col["Date"] else ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        if r[col["Status"]].strip().lower().startswith("cancel"):
            skipped += 1
            continue
        starts, ends = _time(date, r[col["Time (CT)"]])
        if (KIND, starts) in existing:
            skipped += 1
            continue
        db.save_event({"title": TITLE, "kind": KIND, "speaker_slugs": [], "starts_at": starts, "ends_at": ends,
                       "location": r[col["Platform"]].strip(), "join_url": "", "description": DESCRIPTION,
                       "prep_note": "", "required": True})
        added += 1
    print(f"Added {added} huddles; skipped {skipped} (canceled or already in the Hub).")


if __name__ == "__main__":
    main(sys.argv[1])
