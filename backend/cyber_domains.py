"""Twelve domains of cybersecurity work, with the vocabulary each one uses.

There is no single official "12 domains" list. This one merges the (ISC)² CISSP Common
Body of Knowledge (8 domains), the NIST NICE Workforce Framework work roles, and the
widely used Cybersecurity Domain Map, split so each domain is a recognizable early-career
path. It is used in three places:

  * the Job Classifier's instructions, so it recognizes security work even when a posting
    never says "cybersecurity" (e.g. "SOX ITGC testing", "Splunk", "Okta");
  * the Opportunities "best fit" score, mapping an intern's resume words onto domains;
  * the Opportunities Explorer, which shows interns which domain each role sits in.
"""

from __future__ import annotations

import re

DOMAINS: list[dict] = [
    {"key": "secops", "name": "Security Operations & Incident Response",
     "terms": ["soc", "security operations", "siem", "splunk", "sentinel", "qradar", "chronicle", "incident response",
               "incident handling", "triage", "alert", "detection", "threat hunting", "dfir", "forensics",
               "edr", "crowdstrike", "soar", "log analysis", "blue team", "monitoring"]},
    {"key": "threat-intel", "name": "Threat Intelligence",
     "terms": ["threat intelligence", "cti", "threat actor", "mitre att&ck", "att&ck", "ioc", "indicators of compromise",
               "dark web", "osint", "malware analysis", "reverse engineering", "fraud intelligence"]},
    {"key": "network", "name": "Network Security",
     "terms": ["network security", "firewall", "palo alto", "fortinet", "ids", "ips", "vpn", "segmentation",
               "packet", "wireshark", "tcp/ip", "routing", "switching", "cisco", "network engineering", "zscaler", "dns"]},
    {"key": "cloud", "name": "Cloud Security",
     "terms": ["cloud security", "aws", "azure", "gcp", "google cloud", "cspm", "cnapp", "kubernetes", "container",
               "terraform", "infrastructure as code", "cloud engineering", "devsecops", "wiz", "prisma"]},
    {"key": "appsec", "name": "Application & Software Security",
     "terms": ["application security", "appsec", "secure coding", "sdlc", "secure development", "code review",
               "sast", "dast", "owasp", "api security", "threat modeling", "software engineering", "devops", "ci/cd"]},
    {"key": "iam", "name": "Identity & Access Management",
     "terms": ["identity", "iam", "access management", "okta", "active directory", "entra", "sso", "mfa",
               "privileged access", "pam", "cyberark", "sailpoint", "provisioning", "role-based access", "rbac"]},
    {"key": "data", "name": "Data Security & Privacy",
     "terms": ["data security", "data protection", "dlp", "data loss", "encryption", "privacy", "gdpr", "hipaa",
               "ccpa", "pii", "data governance", "classification", "records management", "data privacy"]},
    {"key": "offensive", "name": "Vulnerability Management & Offensive Security",
     "terms": ["vulnerability", "vulnerability management", "penetration test", "pen test", "pentest", "red team",
               "ethical hacking", "exploit", "nessus", "qualys", "tenable", "burp", "bug bounty", "patching", "cve"]},
    {"key": "infra", "name": "Endpoint, Infrastructure & OT/ICS Security",
     "terms": ["endpoint", "hardening", "system administration", "sysadmin", "linux", "windows server",
               "infrastructure", "ot security", "ics", "scada", "operational technology", "iot",
               "medical device security", "product security", "help desk", "desktop support"]},
    {"key": "grc", "name": "Governance, Risk & Compliance (incl. IT Audit)",
     "terms": ["grc", "governance", "risk management", "cyber risk", "it risk", "compliance", "it audit", "itgc",
               "sox", "nist", "iso 27001", "800-53", "csf", "pci", "soc 2", "third-party risk", "vendor risk",
               "policy", "controls testing", "regulatory", "examiner", "ffiec", "information security risk"]},
    {"key": "architecture", "name": "Security Architecture & Engineering",
     "terms": ["security architecture", "security engineering", "zero trust", "cryptography", "pki",
               "secure design", "security controls", "security engineer", "systems security"]},
    {"key": "resilience", "name": "Resilience, Awareness & Physical Security",
     "terms": ["business continuity", "disaster recovery", "resilience", "crisis management", "security awareness",
               "phishing simulation", "training", "physical security", "insider threat", "fraud prevention"]},
]

NAMES = [d["name"] for d in DOMAINS]
BY_NAME = {d["name"]: d for d in DOMAINS}


def prompt_block() -> str:
    """The taxonomy, as text for an agent's instructions."""
    return "\n".join(f"- {d['name']}: {', '.join(d['terms'])}" for d in DOMAINS)


_PATTERNS = {d["name"]: re.compile(r"\b(" + "|".join(re.escape(t) for t in d["terms"]) + r")\b", re.I) for d in DOMAINS}


def domains_in(text: str) -> dict[str, list[str]]:
    """Which domains a piece of text (a resume, a profile, a question) touches, with the words
    that matched. Deterministic and free; used for 'best fit' and the Explorer's prefilter."""
    out: dict[str, list[str]] = {}
    for name, pat in _PATTERNS.items():
        hits = sorted({m.group(0).lower() for m in pat.finditer(text or "")})
        if hits:
            out[name] = hits
    # "security" alone is too broad to count, but "cybersecurity"/"information security" are
    # a general signal: credit Security Operations, the usual entry point, if nothing else hit.
    if not out and re.search(r"\b(cyber ?security|information security|infosec)\b", text or "", re.I):
        out["Security Operations & Incident Response"] = ["cybersecurity"]
    return out
