"""Download each job-feed employer's site icon from the employer's OWN website into
data/logos/ (gitignored), for the Opportunities Explorer tiles. Without these files the
Explorer shows colored monogram tiles instead.

    .venv/bin/python -m scripts.fetch_org_logos

Logos are the organizations' trademarks; they're used only to identify the employer
inside this internal program tool and are not committed to the repository.
"""

from __future__ import annotations

import re
import sqlite3
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
    "Edward Jones": "https://www.edwardjones.com", "U.S. Bank": "https://www.usbank.com",
    "Center for Internet Security": "https://www.cisecurity.org", "Progressive Leasing": "https://progleasing.com",
    "Donaldson": "https://www.donaldson.com", "University of St. Thomas": "https://www.stthomas.edu",
    "Zscaler": "https://www.zscaler.com", "Flashpoint": "https://flashpoint.io", "Sunrise Banks": "https://sunrisebanks.com",
    "City of Saint Paul": "https://www.stpaul.gov", "B. Braun": "https://www.bbraunusa.com",
    "Midwest Reliability Organization": "https://www.mro.net", "Air National Guard": "https://www.ang.af.mil",
    "Minnesota National Guard": "https://minnesotanationalguard.ng.mil", "U.S. Army": "https://www.army.mil",
    "Target": "https://corporate.target.com", "General Mills": "https://www.generalmills.com",
    "C.H. Robinson": "https://www.chrobinson.com", "Xcel Energy": "https://www.xcelenergy.com",
    "Solventum": "https://www.solventum.com", "Thrivent": "https://www.thrivent.com", "CHS": "https://www.chsinc.com",
    # Employers found through Adzuna (their own sites). New ones show a monogram until added here.
    "Graco": "https://www.graco.com",  # Koniag Cyber Solutions: its site only has a white logo, so it keeps a monogram
    "Geronimo Power": "https://geronimopower.com", "New Vision Co Op": "https://www.newvision.coop",
    "The Toro Company": "https://www.thetorocompany.com", "Lumen Technologies": "https://www.lumen.com",
    "AeroVironment": "https://www.avinc.com", "Highmark Health": "https://www.highmark.com",
    "Bracco": "https://www.bracco.com", "Entrust": "https://www.entrust.com", "Black & Veatch": "https://www.bv.com",
    "Northrop Grumman": "https://www.northropgrumman.com", "GardaWorld Security Services U.S.": "https://www.garda.com",
    "ASM Research, An Accenture Federal Services Company": "https://www.asmr.com", "PwC": "https://www.pwc.com",
    "IBM": "https://www.ibm.com", "Actalent": "https://www.actalentservices.com",
    "General Dynamics Mission Systems": "https://gdmissionsystems.com", "AECOM": "https://aecom.com",
}
# Preferred higher-resolution files, still from the organization's own sites. Tried first.
LOGO_URLS = {
    "3M": "https://news.3m.com/images/unicorn/3M_Bookmark_Icon_180x180.png",  # 3m.com refuses our User-Agent
    "UnitedHealth Group": "https://www.unitedhealthgroup.com/content/dam/uhg/icons/Icons/uhg-logo--stacked-2022.svg",
    # jobs.xcelenergy.com's own logo file (its Workday icon is white on transparent, invisible on a tile).
    "Xcel Energy": "https://dokumfe7mps0i.cloudfront.net/oms/5464/image/2025/6/JJR49_XElogo-DA1020600x1201MekmfG/XElogo-DA1020600x1201MekmfG.png",
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


def main(only: set[str] | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Feed employers, plus employers currently listed through the Adzuna aggregator.
    # Read-only, and only if the database already exists: this script runs during Render's build,
    # and opening a missing database would create an empty file that stops data/seed.db being used.
    via_adzuna = []
    if config.DB_PATH.exists():
        try:
            con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
            via_adzuna = [r[0] for r in con.execute("SELECT DISTINCT org FROM job_postings WHERE platform = 'adzuna' AND active = 1")]
            con.close()
        except sqlite3.Error:
            pass
    for src in [s for s in SOURCES if not s.get("aggregator")] + [{"org": o, "platform": "adzuna"} for o in via_adzuna]:
        if only and src["org"] not in only:
            continue
        org, base = src["org"], SITES.get(src["org"])
        if not base:
            continue
        try:
            html = "" if org in LOGO_URLS else get(base)[0].decode("utf8", "ignore")
        except Exception as e:  # noqa: BLE001
            print(f"{org:36} homepage failed: {e}")
            html = ""
        saved = False
        candidates = icon_candidates(base, html)
        if src["platform"] == "workday":  # the employer's own logo on its Workday career site
            candidates.insert(0, f"https://{src['host']}/{src['site']}/assets/logo")
        if org in LOGO_URLS:
            candidates.insert(0, LOGO_URLS[org])
        for url in candidates:
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
    import sys
    main(set(sys.argv[1:]) or None)  # optional: org names to refresh, e.g. "3M" "UnitedHealth Group"
