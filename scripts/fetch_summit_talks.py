"""Build data/summit_talks.json: which Think Tank and Emeritus members presented at past Cyber
Security Summits, from the Summit's own "YYYY Summit Presentations" pages.

    .venv/bin/python -m scripts.fetch_summit_talks

Each presentations page lists a talk title followed by a line of speaker names. A speaker is
linked to a roster member only when the last names match and the first names agree (same
name, or one is a short form of the other, like "Vince" and "Vincent"). Anything uncertain is
left out, so Mentor Match never claims a talk someone didn't give. The output is public
information from cybersecuritysummit.org.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.request

from backend import config, directory

UA = "InternSummitHub/1.0 (Cyber Security Summit intern program)"
PAGES = {
    2025: "https://www.cybersecuritysummit.org/2025-summit-presentations/",
    2024: "https://www.cybersecuritysummit.org/2024-summit-presentations/",
    2023: "https://www.cybersecuritysummit.org/2023-summit-presentations/",
    2022: "https://www.cybersecuritysummit.org/2022-summit-presentations/",
    2021: "https://www.cybersecuritysummit.org/2021-summit-presentations/",
    2020: "https://www.cybersecuritysummit.org/2020-summit-presentations/",
    2019: "https://www.cybersecuritysummit.org/2019-presentations/",
    2018: "https://www.cybersecuritysummit.org/2018-presentations/",
    2017: "https://www.cybersecuritysummit.org/2017-presentations/",
    2016: "https://www.cybersecuritysummit.org/2016-presentations/",
}
OUT = config.DATA_DIR / "summit_talks.json"
TRACKS = {"PS": "Public Sector Seminar", "MT": "MedTech Seminar", "Cyber101": "Cyber101 Workshop"}


def _lines(page: str) -> list[str]:
    t = re.sub(r"(?s)<(script|style|nav|header|footer).*?</\1>", "", page)
    t = re.sub(r"<(br|/p|/li|/h\d|/div|/tr|/td|/a)[^>]*>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return [re.sub(r"\s+", " ", x).strip() for x in t.split("\n") if x.strip()]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def _same_person(speaker: str, roster_name: str) -> bool:
    sp, rn = speaker.replace(".", " ").split(), re.sub(r"\(.*?\)", "", roster_name).replace(".", " ").split()
    sp = [w for w in sp if w.lower() not in ("dr", "jr", "sr", "ii", "iii")]
    rn = [w for w in rn if w.lower() not in ("dr", "jr", "sr", "ii", "iii")]
    if len(sp) < 2 or len(rn) < 2 or _norm(sp[-1]) != _norm(rn[-1]):
        return False
    a, b = _norm(sp[0]), _norm(rn[0])
    return a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def _looks_like_names(line: str) -> bool:
    parts = [p.strip() for p in re.split(r",| and |&", line) if p.strip()]
    return bool(parts) and all(re.fullmatch(r"(Dr\.? )?[A-Z][\w'’.-]+( [A-Z][\w'’.-]*){1,3}", p) for p in parts) and len(line) < 160


def main() -> None:
    people = directory.load_people()
    talks: dict[str, list[dict]] = {}
    unmatched = 0
    for year, url in PAGES.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            page = urllib.request.urlopen(req, timeout=30).read().decode("utf8", "ignore")
        except Exception as e:  # noqa: BLE001
            print(f"{year}: skipped ({e})")
            continue
        lines = _lines(page)
        n = 0
        for i in range(1, len(lines)):
            speakers_line, title = lines[i], lines[i - 1]
            if not _looks_like_names(speakers_line) or _looks_like_names(title) or len(title) < 8 or len(title) > 200:
                continue
            if re.search(r"(?i)summit guide|password|video library|click here|session details|presentations from", title):
                continue
            # Older pages break one title over two lines: "<speakers> / title part 1 / part 2 / <speakers>".
            if i >= 3 and "|" not in lines[i - 2] and len(lines[i - 2]) < 90 and (
                    title[:1].islower() or (_looks_like_names(lines[i - 3]) and not _looks_like_names(lines[i - 2]))):
                title = f"{lines[i - 2]} {title}"
            track = next((v for k, v in TRACKS.items() if title.startswith(k + " ")), "General session")
            clean_title = re.sub(r"^(PS|MT|Cyber101)\s*-\s*", "", title).strip()
            for sp in [p.strip() for p in re.split(r",| and |&", speakers_line) if p.strip()]:
                match = [p for p in people if _same_person(sp, p.name)]
                if len(match) == 1:
                    rec = {"year": year, "title": clean_title, "track": track, "source": url}
                    if rec not in talks.setdefault(match[0].slug, []):
                        talks[match[0].slug].append(rec)
                        n += 1
                else:
                    unmatched += 1
        print(f"{year}: {n} talks by Think Tank members")
        time.sleep(1.0)
    for v in talks.values():
        v.sort(key=lambda r: -r["year"])
    OUT.write_text(json.dumps(talks, indent=1, ensure_ascii=False))
    total = sum(len(v) for v in talks.values())
    print(f"Wrote {OUT.name}: {total} talks by {len(talks)} members ({unmatched} other speakers not on the roster).")
    for slug, v in sorted(talks.items()):
        print(f"  {slug}: " + "; ".join(f"{r['year']} {r['title'][:50]}" for r in v[:4]))


if __name__ == "__main__":
    main()
