"""Who is allowed in.

Two kinds of people:
- Interns: register with the program access code, then sign in with their Slack email and
  a password. Everything in the app except sign-in and registration requires this.
- The administrator (the Program Lead): a single account whose username and password hash
  live in .env, written by `scripts/set_admin_password.py`. There is no default password;
  until the Program Lead runs that script, admin sign-in is disabled.

Passwords are hashed with scrypt (stdlib). Sessions are HMAC-signed, HttpOnly cookies, so
the server keeps no session table.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import HTTPException, Request, Response

from . import config, db

USER_COOKIE = "css_user"
ADMIN_COOKIE = "css_admin"
USER_SESSION_SECONDS = 14 * 24 * 3600
ADMIN_SESSION_SECONDS = 8 * 3600

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# A real-looking hash to verify against when the email is unknown, so a failed login takes
# the same time whether or not the account exists.
_DUMMY_HASH = hash_password(secrets.token_hex(8))

# ---------------------------------------------------------------------------
# Signed session tokens: "<subject>.<expiry>.<hmac>"
# ---------------------------------------------------------------------------


def _sign(payload: str) -> str:
    return hmac.new(config.SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(kind: str, subject: str, ttl: int) -> str:
    exp = int(time.time()) + ttl
    return f"{subject}.{exp}.{_sign(f'{kind}:{subject}:{exp}')}"


def read_token(kind: str, token: str | None) -> str | None:
    try:
        subject, exp_s, sig = (token or "").rsplit(".", 2)
        if int(exp_s) < time.time():
            return None
        if not hmac.compare_digest(sig, _sign(f"{kind}:{subject}:{exp_s}")):
            return None
        return subject
    except ValueError:
        return None


def set_cookie(response: Response, request: Request, name: str, value: str, ttl: int) -> None:
    response.set_cookie(name, value, httponly=True, samesite="strict", max_age=ttl,
                        secure=request.url.scheme == "https")


# ---------------------------------------------------------------------------
# Interns
# ---------------------------------------------------------------------------


MIN_PASSWORD = 12
RESET_TTL_HOURS = 24


def user_cookie_value(user: dict) -> str:
    """Subject is "<id>:<session_version>". Changing a password bumps the version, which
    invalidates every cookie issued before it (on every device)."""
    return make_token("user", f"{user['id']}:{user.get('session_version') or 0}",
                      USER_SESSION_SECONDS)


def current_user(request: Request) -> dict | None:
    subject = read_token("user", request.cookies.get(USER_COOKIE))
    if not subject or ":" not in subject:
        return None
    uid, ver = subject.split(":", 1)
    if not (uid.isdigit() and ver.isdigit()):
        return None
    user = db.get_user(int(uid))
    if not user or int(user.get("session_version") or 0) != int(ver):
        return None
    return user


def check_password_strength(password: str, email: str = "") -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Use at least {MIN_PASSWORD} characters for your password.")
    if email and password.lower() == email.lower():
        raise HTTPException(400, "Don't use your email address as your password.")


def new_reset_token(user_id: int, created_by: str) -> str:
    """Random 256-bit token. Only its sha256 is stored, so a leaked database can't be used
    to reset anyone's password."""
    from datetime import datetime, timedelta

    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=RESET_TTL_HOURS)).isoformat(timespec="seconds")
    db.create_reset_token(user_id, hash_reset_token(token), expires, created_by)
    return token


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


TEMP_PASSWORD_TTL_HOURS = 24
# No 0/O, 1/l/I, so it's easy to read from an email and type on a phone.
_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_temp_password(user_id: int) -> str:
    """xxxx-xxxx-xxxx-xxxx: 16 random characters (~92 bits). Only its scrypt hash is kept."""
    from datetime import datetime, timedelta

    raw = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(16))
    temp = "-".join(raw[i:i + 4] for i in range(0, 16, 4))
    expires = (datetime.now() + timedelta(hours=TEMP_PASSWORD_TTL_HOURS)).isoformat(timespec="seconds")
    db.set_temp_password(user_id, hash_password(temp), expires)
    return temp


def temp_password_matches(user: dict, password: str) -> bool:
    from datetime import datetime

    h, exp = user.get("temp_password_hash"), user.get("temp_password_expires")
    if not h or not exp or exp < datetime.now().isoformat(timespec="seconds"):
        return False
    return verify_password(password.strip(), h)


# While an intern still owes a new password (they signed in with an emailed temporary one),
# these are the only endpoints that work for them.
FORCED_CHANGE_ALLOWED = {"/api/me", "/api/me/new-password"}


def require_user(request: Request) -> dict:
    """Interns, or the admin (who can see everything an intern sees)."""
    user = current_user(request)
    if user:
        if user.get("must_change_password") and request.url.path not in FORCED_CHANGE_ALLOWED:
            raise HTTPException(403, "password_change_required")
        return user
    role = admin_role(request)
    if role in VIEW_ONLY_ROLES:
        return {"id": 0, "first_name": VIEW_ONLY_LABELS[role], "last_name": "", "is_admin_view": True,
                "is_grader": True}
    if role == "owner":
        return {"id": 0, "first_name": config.ADMIN_NAME.split()[0],
                "last_name": " ".join(config.ADMIN_NAME.split()[1:]), "is_admin_view": True}
    raise HTTPException(401, "Please sign in.")


def access_code() -> str:
    """The code interns need to register. Generated once; the admin can rotate it."""
    code = db.get_setting("access_code")
    if not code:
        code = rotate_access_code()
    return code


def rotate_access_code() -> str:
    words = ["shield", "cipher", "falcon", "beacon", "summit", "vector", "harbor", "lumen",
             "north", "anchor", "signal", "matrix", "quartz", "prairie", "aurora", "sentry"]
    code = f"{secrets.choice(words)}-{secrets.choice(words)}-{secrets.randbelow(900) + 100}"
    db.set_setting("access_code", code)
    return code


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def admin_configured() -> bool:
    return bool(config.ADMIN_USERNAME and config.ADMIN_PASSWORD_HASH)


# View-only admin logins: role -> (username, password hash). Each sees the admin pages and
# can run the demo agents, but nothing else that changes data (see GRADER_ALLOWED_WRITES).
VIEW_ONLY_ROLES = ("grader", "reviewer")
VIEW_ONLY_LABELS = {"grader": "Grader", "reviewer": "Reviewer"}


def _view_only_logins() -> dict[str, tuple[str, str]]:
    out = {"grader": (config.GRADER_USERNAME, config.GRADER_PASSWORD_HASH),
           "reviewer": (config.REVIEWER_USERNAME, config.REVIEWER_PASSWORD_HASH)}
    return {role: (u.lower(), h) for role, (u, h) in out.items() if u and h}


def grader_configured() -> bool:
    return "grader" in _view_only_logins()


# The admin cookie's subject: the Program Lead's username, or "<role>:<username>" for a
# view-only login. Usernames can't contain ':', so they can never be confused.
GRADER_PREFIX = "grader:"


def check_admin_login(username: str, password: str) -> str | None:
    """'owner', 'grader' or 'reviewer' when the username and password match, else None."""
    name = username.strip().lower()
    if admin_configured() and hmac.compare_digest(name, config.ADMIN_USERNAME.lower()):
        return "owner" if verify_password(password, config.ADMIN_PASSWORD_HASH) else None
    for role, (user, pw_hash) in _view_only_logins().items():
        if hmac.compare_digest(name, user):
            return role if verify_password(password, pw_hash) else None
    verify_password(password, _DUMMY_HASH)  # same timing whether or not the name exists
    return None


def admin_cookie_subject(role: str) -> str:
    if role in VIEW_ONLY_ROLES:
        return f"{role}:{_view_only_logins()[role][0]}"
    return config.ADMIN_USERNAME.lower()


def admin_role(request: Request) -> str | None:
    """'owner' (the Program Lead), 'grader' or 'reviewer' (view-only), or None."""
    subject = read_token("admin", request.cookies.get(ADMIN_COOKIE))
    if not subject:
        return None
    if admin_configured() and subject == config.ADMIN_USERNAME.lower():
        return "owner"
    for role, (user, _) in _view_only_logins().items():
        if subject == f"{role}:{user}":
            return role
    return None


def is_admin(request: Request) -> bool:
    return admin_role(request) is not None


def is_view_only(request: Request) -> bool:
    return admin_role(request) in VIEW_ONLY_ROLES


is_grader = is_view_only  # older name


# What the grader login may do beyond reading. Everything else that changes data is refused.
GRADER_ALLOWED_WRITES = [
    ("POST", r"/api/admin/demo"),                    # load the fictional demo cohort
    ("DELETE", r"/api/admin/demo"),                  # remove it again
    ("POST", r"/api/admin/placements/run"),          # run the Placement Matcher
    ("POST", r"/api/admin/placements/\d+/drafts"),   # draft intros (text only, never sent)
    ("POST", r"/api/admin/pulse/brief"),             # generate a Cohort Pulse brief
]
# Reads the grader may not make: they expose contact details, resumes, or sign-up secrets.
GRADER_BLOCKED_READS = [
    r"/api/admin/interns\.csv",
    r"/api/admin/interns/\d+/resume",
    r"/api/admin/access-code",
    r"/api/admin/reset-requests",
]


def grader_may(method: str, path: str) -> bool:
    if method in ("GET", "HEAD"):
        return not any(re.fullmatch(p, path) for p in GRADER_BLOCKED_READS)
    return any(method == m and re.fullmatch(p, path) for m, p in GRADER_ALLOWED_WRITES)


def require_admin(request: Request) -> None:
    role = admin_role(request)
    if not role:
        raise HTTPException(401, "Admin sign-in required.")
    if role in VIEW_ONLY_ROLES and not grader_may(request.method, request.url.path):
        raise HTTPException(403, "This login is view-only, so this is turned off. "
                                 "It can load the demo cohort and run the AI agents.")
