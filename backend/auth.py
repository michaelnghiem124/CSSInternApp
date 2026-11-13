"""Who is allowed in.

Two kinds of people:
- Interns: register with the program access code, then sign in with their Slack email and
  a password. Everything in the app except sign-in and registration requires this.
- The administrator (the Program Chair): a single account whose username and password hash
  live in .env, written by `scripts/set_admin_password.py`. There is no default password;
  until the Chair runs that script, admin sign-in is disabled.

Passwords are hashed with scrypt (stdlib). Sessions are HMAC-signed, HttpOnly cookies, so
the server keeps no session table.
"""

from __future__ import annotations

import hashlib
import hmac
import os
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


def require_user(request: Request) -> dict:
    """Interns, or the admin (who can see everything an intern sees)."""
    user = current_user(request)
    if user:
        return user
    if is_admin(request):
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


def check_admin_login(username: str, password: str) -> bool:
    if not admin_configured():
        verify_password(password, _DUMMY_HASH)
        return False
    user_ok = hmac.compare_digest(username.strip().lower(), config.ADMIN_USERNAME.lower())
    pw_ok = verify_password(password, config.ADMIN_PASSWORD_HASH)
    return user_ok and pw_ok


def is_admin(request: Request) -> bool:
    if not admin_configured():
        return False
    return read_token("admin", request.cookies.get(ADMIN_COOKIE)) == config.ADMIN_USERNAME.lower()


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "Admin sign-in required.")
