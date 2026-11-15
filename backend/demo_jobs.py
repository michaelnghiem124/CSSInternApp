"""Fictional demo listings in the Adzuna slot, for the public seed database.

Real Adzuna listings can't be published in the repo (Adzuna's terms require removing its data if
API access ends), and the free Render plan resets its database on every restart. So data/seed.db
carries these clearly fictional examples instead: every company name ends in "(demo)", each card
says it's a fictional example, the button opens a real Adzuna *search* (never a fake application),
and the AI agents are told they're demos. The first real Adzuna refresh retires them automatically
(`_store` deactivates Adzuna-platform rows it didn't see).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
from datetime import datetime

DEMO_PREFIX = "demo|"


def _search(q: str) -> str:
    return "https://www.adzuna.com/search?" + urllib.parse.urlencode({"q": q, "w": "Minnesota"})


# Each entry is a posting plus the label the Job Classifier would give it.
DEMO_POSTINGS = [
    {"org": "Lakeview Health (demo)", "title": "Cybersecurity Analyst Intern - Summer 2027", "location": "Minneapolis, MN",
     "search": "cybersecurity analyst intern",
     "description": "Fictional demo listing. Monitor SIEM alerts with the security operations team, triage phishing "
                    "reports, and help document incident response playbooks. Pursuing a degree in cybersecurity, IT, or computer science.",
     "cls": {"kind": "internship", "field": "Cybersecurity", "relevance": 88, "location_type": "Minnesota", "term": "Summer 2027",
             "cyber_domains": ["Security Operations & Incident Response"],
             "security_evidence": ["Monitor SIEM alerts", "triage phishing reports", "incident response playbooks"],
             "eligibility_notes": ["Pursuing a related degree"],
             "summary": "A fictional SOC-focused summer internship triaging alerts and phishing reports."}},
    {"org": "Northwoods Freight (demo)", "title": "IT Audit Intern (SOX ITGC) - Summer 2027", "location": "Saint Paul, MN",
     "search": "IT audit intern",
     "description": "Fictional demo listing. Test IT general controls for SOX, review user access and change management "
                    "evidence, and write up findings with the internal audit team. Accounting, MIS, or cybersecurity majors.",
     "cls": {"kind": "internship", "field": "Risk / Audit / Compliance", "relevance": 80, "location_type": "Minnesota", "term": "Summer 2027",
             "cyber_domains": ["Governance, Risk & Compliance (incl. IT Audit)"],
             "security_evidence": ["Test IT general controls for SOX", "review user access"],
             "eligibility_notes": ["Accounting, MIS, or cybersecurity major"],
             "summary": "A fictional IT audit internship testing SOX IT general controls."}},
    {"org": "Prairie Mutual (demo)", "title": "Identity & Access Management Intern", "location": "Bloomington, MN",
     "search": "identity access management intern",
     "description": "Fictional demo listing. Help run access reviews, automate joiner-mover-leaver tasks in Okta and "
                    "Active Directory, and clean up privileged accounts. Junior or senior standing.",
     "cls": {"kind": "internship", "field": "Cybersecurity", "relevance": 86, "location_type": "Minnesota", "term": "",
             "cyber_domains": ["Identity & Access Management"],
             "security_evidence": ["access reviews", "Okta and Active Directory", "privileged accounts"],
             "eligibility_notes": ["Junior or senior standing"],
             "summary": "A fictional IAM internship running access reviews in Okta and Active Directory."}},
    {"org": "Twin Rivers Credit Union (demo)", "title": "Junior SOC Analyst (Entry Level)", "location": "Eagan, MN",
     "search": "junior SOC analyst",
     "description": "Fictional demo listing. Entry-level role on a 24x7 security operations center: investigate alerts, "
                    "escalate incidents, and tune detection rules. Security+ or equivalent coursework preferred.",
     "cls": {"kind": "new-grad / entry-level", "field": "Cybersecurity", "relevance": 90, "location_type": "Minnesota", "term": "",
             "cyber_domains": ["Security Operations & Incident Response", "Threat Intelligence"],
             "security_evidence": ["investigate alerts", "escalate incidents", "tune detection rules"],
             "eligibility_notes": ["Security+ or equivalent coursework preferred"],
             "summary": "A fictional entry-level SOC analyst role investigating and escalating alerts."}},
    {"org": "Gopher State Manufacturing (demo)", "title": "OT Security Intern", "location": "Rochester, MN",
     "search": "OT security intern",
     "description": "Fictional demo listing. Inventory plant-floor devices, map industrial networks, and help apply "
                    "IEC 62443 segmentation guidance with the OT security engineer.",
     "cls": {"kind": "internship", "field": "Cybersecurity", "relevance": 78, "location_type": "Minnesota", "term": "",
             "cyber_domains": ["Endpoint, Infrastructure & OT/ICS Security", "Network Security"],
             "security_evidence": ["map industrial networks", "IEC 62443 segmentation"],
             "eligibility_notes": [],
             "summary": "A fictional OT security internship mapping industrial networks."}},
]


def add_demo_postings(conn: sqlite3.Connection, classifier_version: str) -> int:
    """Insert (or refresh) the demo rows. Returns how many were written."""
    now = datetime.now().isoformat(timespec="seconds")
    for i, d in enumerate(DEMO_POSTINGS, 1):
        h = hashlib.sha256(f"{d['title']}|{d['location']}|{d['description'][:6000]}".encode()).hexdigest()[:16]
        cls = {"deadline": "", "injection_detected": False, **d["cls"]}
        conn.execute(
            "INSERT OR REPLACE INTO job_postings (id, org, platform, title, location, url, posted, description, content_hash, "
            "classification_json, classified_hash, first_seen, last_seen, active, hidden) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)",
            (f"{DEMO_PREFIX}{i}", d["org"], "adzuna", d["title"], d["location"], _search(d["search"]), "", d["description"],
             h, json.dumps(cls), f"{h}:{classifier_version}", now, now))
    return len(DEMO_POSTINGS)


def is_demo(posting_id: str) -> bool:
    return (posting_id or "").startswith(DEMO_PREFIX)
