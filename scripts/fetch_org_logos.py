"""Download each job-feed employer's site icon from the employer's OWN website into
data/logos/ (gitignored), for the Opportunities Explorer tiles. Without these files the
Explorer shows colored monogram tiles instead.

    .venv/bin/python -m scripts.fetch_org_logos

Logos are the organizations' trademarks; they're used only to identify the employer
inside this internal program tool and are not committed to the repository.
"""

from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request

from backend import config
from backend.opportunities import SOURCES, USER_AGENT

SITES = {
    "3M": "https://www.3m.com", "Medtronic": "https://www.medtronic.com", "Federal Reserve": "https://www.federalreserve.gov",
    "Ameriprise Financial Services, LLC": "https://www.ameriprise.com", "Land O'Lakes, Inc.": "https://www.landolakesinc.com",
    "Securian Financial": "https://www.securian.com", "HealthPartners": "https://www.healthpartners.com",
    "Hormel": "https://www.hormelfoods.com", "Federated Insurance": "https://www.federatedinsurance.com",
    "Solera Health": "https://www.soleranetwork.com", "CISA": "https://www.cisa.gov", "EcoLab": "https://www.ecolab.com",
    "nVent": "https://www.nvent.com", "UnitedHealth Group": "https://www.unitedhealthgroup.com",
}
OUT = config.DATA_DIR / "logos"


def org_key(org: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", org.lower()).strip("-")


def get(url: str, limit: int = 3_000_000) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(limit), r.headers.get("Content-Type", "")


def icon_candidates(base: str, html: str) -> list[str]:
    links = []
    for tag in re.findall(r"<link[^>]+>", html, re.I):
        rel = re.search(r'rel=["\']([^"\']+)', tag, re.I)
        href = re.search(r'href=["\']([^"\']+)', tag, re.I)
        if rel and href and "icon" in rel.group(1).lower():
            size = re.search(r'sizes=["\'](\d+)', tag)
            score = (2 if "apple-touch" in rel.group(1).lower() else 1) * 1000 + int(size.group(1) if size else 32)
            links.append((score, urllib.parse.urljoin(base + "/", href.group(1))))
    return [u for _, u in sorted(links, reverse=True)] + [base + "/apple-touch-icon.png", base + "/favicon.ico"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for src in SOURCES:
        org, base = src["org"], SITES.get(src["org"])
        if not base:
            continue
        try:
            html = get(base)[0].decode("utf8", "ignore")
        except Exception as e:  # noqa: BLE001
            print(f"{org:36} homepage failed: {e}")
            html = ""
        saved = False
        for url in icon_candidates(base, html):
            try:
                data, ctype = get(url, 1_000_000)
            except Exception:  # noqa: BLE001
                continue
            ext = "svg" if "svg" in ctype or url.endswith(".svg") else "ico" if "icon" in ctype or url.endswith(".ico") else "png"
            if len(data) < 200 or (ext != "svg" and data[:4] not in (b"\x89PNG", b"\x00\x00\x01\x00") and data[:3] != b"\xff\xd8\xff"):
                continue
            if ext == "png" and data[:3] == b"\xff\xd8\xff":
                ext = "jpg"
            for old in OUT.glob(f"{org_key(org)}.*"):
                old.unlink()
            (OUT / f"{org_key(org)}.{ext}").write_bytes(data)
            print(f"{org:36} saved {ext} ({len(data):,} bytes) from {urllib.parse.urlparse(url).hostname}")
            saved = True
            break
        if not saved:
            print(f"{org:36} no icon found; the Explorer will show a monogram")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
