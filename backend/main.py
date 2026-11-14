"""FastAPI app: serves the single-page frontend and the JSON API.

Run:  .venv/bin/uvicorn backend.main:app --port 8000
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, field_validator
from pydantic_ai.exceptions import UsageLimitExceeded

import json as _json

from . import agents, auth, config, db, demo, directory, mailer, placements, pulse, safety

log = logging.getLogger("intern_hub")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    directory.load_people()
    auth.access_code()
    if not auth.admin_configured():
        log.warning("Admin sign-in is disabled until you run: "
                    ".venv/bin/python -m scripts.set_admin_password")
    yield


app = FastAPI(title="CSS Intern Hub", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")


# ---------------------------------------------------------------------------
# Session plumbing
# ---------------------------------------------------------------------------
STATES: dict[str, agents.ChatState] = {}
STATE_TTL_SECONDS = 3 * 3600


def session_key(user: Annotated[dict, Depends(auth.require_user)]) -> str:
    """Signed-in intern (or admin) -> a hashed key for logs and the chat state."""
    return safety.hash_session(f"user:{user['id']}")


def _profile_from_registration(user: dict) -> agents.InternProfile | None:
    """What the intern told us at sign-up, as a matchmaker profile. No model call needed."""
    if not user.get("slack_email"):
        return None
    split = lambda s: [x.strip() for x in re.split(r"[,;\n]", s or "") if x.strip()][:12]
    return agents.InternProfile(
        first_name=user["first_name"], school=user.get("school", ""),
        interests=split(user["interests"]), hobbies=split(user["hobbies"]),
        background=[f"From {user['city']}, {user['state']}"]
                   + ([f"School or employer: {user['school']}"] if user.get("school") else [])
                   + ([f"Fun fact: {user['fun_fact']}"] if user.get("fun_fact") else []),
        career_goal=(user["goals"] or "")[:300],
        summary=f"{user['first_name']} is a Cyber Security Summit intern from {user['city']}, "
                f"{user['state']}. Goals for the program: {(user['goals'] or 'not stated yet')[:300]}",
    )


def state_for(key: str, user: dict | None = None) -> agents.ChatState:
    now = time.time()
    for k in [k for k, s in STATES.items() if now - s.last_seen > STATE_TTL_SECONDS]:
        del STATES[k]
    st = STATES.get(key)
    if st is None:
        st = STATES[key] = agents.ChatState(profile=_profile_from_registration(user or {}))
    st.last_seen = now
    return st


def _blocked(exc: safety.Blocked) -> JSONResponse:
    code = 429 if exc.kind in ("rate_limit", "budget") else 400
    return JSONResponse({"blocked": exc.kind, "message": exc.message}, status_code=code)


# ---------------------------------------------------------------------------
# Accounts: intern registration / sign-in, admin sign-in
# ---------------------------------------------------------------------------
login_limiter = safety.RateLimiter(5)
register_limiter = safety.RateLimiter(3)

US_STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL","IN","IA",
             "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM",
             "NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA",
             "WV","WI","WY","PR","Other"}


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class ProfileFields(BaseModel):
    first_name: str = Field(min_length=1, max_length=60)
    last_name: str = Field(min_length=1, max_length=60)
    phone: str = Field(min_length=7, max_length=30)
    slack_email: EmailStr
    city: str = Field(min_length=1, max_length=80)
    state: str
    school: str = Field("", max_length=120, description="School or current employer.")
    interests: str = Field("", max_length=1000)
    hobbies: str = Field("", max_length=1000)
    goals: str = Field("", max_length=1500)
    fun_fact: str = Field("", max_length=300)
    linkedin_url: str = Field("", max_length=200)
    committee_prefs: list[str] = Field(default_factory=list, max_length=10)
    availability: str = Field("", max_length=300)
    share_with_cohort: bool = True

    @field_validator("committee_prefs")
    @classmethod
    def known_committees(cls, v: list[str]) -> list[str]:
        known = {c["slug"] for c in placements.list_committees(active_only=True)}
        out = []
        for slug in v:
            if slug in known and slug not in out:
                out.append(slug)
        return out[:3]

    @field_validator("first_name", "last_name", "city", "school", "interests", "hobbies", "goals",
                     "fun_fact", "availability")
    @classmethod
    def trim(cls, v: str) -> str:
        return v.strip()

    @field_validator("first_name", "last_name", "city")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v:
            raise ValueError("This field is required.")
        return v

    @field_validator("linkedin_url")
    @classmethod
    def linkedin(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return ""
        if not v.startswith(("http://", "https://")):
            v = "https://" + v
        m = re.match(r"^https?://([a-z]{2,3}\.)?(www\.)?linkedin\.com/(in|pub)/[A-Za-z0-9\-_%.]+/?$", v,
                     re.IGNORECASE)
        if not m:
            raise ValueError("Use your LinkedIn profile link, like linkedin.com/in/your-name.")
        return re.sub(r"^http://", "https://", v)

    @field_validator("phone")
    @classmethod
    def phone_digits(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 10:
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        if 7 <= len(digits) <= 15 and v.strip().startswith("+"):
            return "+" + digits
        raise ValueError("Enter a 10-digit US phone number (or +country code).")

    @field_validator("state")
    @classmethod
    def us_state(cls, v: str) -> str:
        v = v.strip()
        if v.upper() in US_STATES:
            return v.upper() if v != "Other" else v
        raise ValueError("Pick a state.")


class RegisterBody(ProfileFields):
    password: str = Field(max_length=200)
    access_code: str = Field(max_length=100)


class LoginBody(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class AdminLoginBody(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


def _public_user(u: dict, include_private: bool = False) -> dict:
    keep = ["id", "first_name", "last_name", "slack_email", "city", "state", "school",
            "interests", "hobbies", "goals", "fun_fact", "linkedin_url", "share_with_cohort",
            "created_at"]
    if include_private:
        keep += ["phone", "last_login", "updated_at", "must_change_password", "availability",
                 "is_sample"]
    d = {k: u.get(k) for k in keep}
    d["share_with_cohort"] = bool(d["share_with_cohort"])
    if include_private:
        try:
            d["committee_prefs"] = _json.loads(u.get("committee_prefs") or "[]")
        except ValueError:
            d["committee_prefs"] = []
        d["is_sample"] = bool(u.get("is_sample"))
    if "must_change_password" in d:
        d["must_change_password"] = bool(d["must_change_password"])
    # Cache-busting URL; the endpoint itself checks the viewer is signed in.
    d["photo_url"] = (f"/api/users/{u['id']}/photo?v={u['photo_updated']}"
                      if u.get("photo_updated") else "")
    return d


@app.post("/api/auth/register")
def register(body: RegisterBody, request: Request, response: Response):
    if not register_limiter.allow(_ip(request)):
        raise HTTPException(429, "Too many sign-up attempts. Wait a minute.")
    if body.access_code.strip().lower() != auth.access_code().lower():
        db.log_safety(_ip(request), "bad_access_code", "")
        raise HTTPException(403, "That program access code isn't right. Ask the Program Chair for it.")
    if db.get_user_by_email(body.slack_email):
        raise HTTPException(409, "An account with that email already exists. Sign in instead.")
    auth.check_password_strength(body.password, str(body.slack_email))
    data = body.model_dump(exclude={"password", "access_code"})
    data["slack_email"] = str(body.slack_email).lower()
    uid = db.create_user(data, auth.hash_password(body.password))
    db.touch_login(uid)
    user = db.get_user(uid)
    auth.set_cookie(response, request, auth.USER_COOKIE, auth.user_cookie_value(user),
                    auth.USER_SESSION_SECONDS)
    return {"user": _public_user(user, include_private=True)}


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    if not login_limiter.allow(_ip(request)):
        raise HTTPException(429, "Too many attempts. Wait a minute.")
    u = db.get_user_by_email(body.email.strip().lower())
    ok = auth.verify_password(body.password, u["password_hash"] if u else auth._DUMMY_HASH)
    used_temp = False
    if u and not ok and auth.temp_password_matches(u, body.password):
        ok = used_temp = True
    if not (u and ok):
        db.log_safety(_ip(request), "login_failed", body.email[:80])
        raise HTTPException(401, "Email or password is incorrect.")
    if used_temp:
        db.consume_temp_password(u["id"])  # single use; must_change_password = 1
        db.log_safety(_ip(request), "temp_password_used", f"user {u['id']}")
    elif u.get("temp_password_hash"):
        db.clear_temp_password(u["id"])  # they remembered the real one
    db.touch_login(u["id"])
    u = db.get_user(u["id"])
    auth.set_cookie(response, request, auth.USER_COOKIE, auth.user_cookie_value(u),
                    auth.USER_SESSION_SECONDS)
    return {"user": _public_user(u, include_private=True),
            "must_change_password": bool(u["must_change_password"])}


# ---------------------------------------------------------------------------
# Password reset + change
# ---------------------------------------------------------------------------
forgot_limiter = safety.RateLimiter(3)
reset_limiter = safety.RateLimiter(10)


class ForgotBody(BaseModel):
    email: str = Field(max_length=200)


class ResetBody(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(max_length=200)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def _reset_link(request: Request, token: str) -> str:
    # The token rides in the URL fragment (#...), which browsers never send to the server,
    # so it doesn't land in access logs or Referer headers.
    return f"{config.PUBLIC_URL or str(request.base_url).rstrip('/')}/#reset={token}"


@app.post("/api/auth/forgot")
def forgot_password(body: ForgotBody, request: Request):
    """Same answer whether or not the email has an account, so this can't be used to find
    out who is registered."""
    if not forgot_limiter.allow(_ip(request)):
        raise HTTPException(429, "Too many requests. Wait a minute.")
    u = db.get_user_by_email(body.email.strip().lower())
    if u:
        emailed = False
        if mailer.enabled():
            temp = auth.new_temp_password(u["id"])
            emailed = mailer.send_temp_password_email(
                u["slack_email"], u["first_name"], temp, auth.TEMP_PASSWORD_TTL_HOURS,
                config.PUBLIC_URL or str(request.base_url).rstrip("/"))
        if not emailed:
            # No email set up (or sending failed): the Chair sees it on the Admin page.
            db.add_reset_request(u["id"])
        db.log_safety(_ip(request), "reset_requested",
                      f"user {u['id']} ({'temp password emailed' if emailed else 'admin queue'})")
    return {"ok": True, "email_enabled": mailer.enabled()}


@app.get("/api/auth/reset/{token}")
def check_reset_token(token: str, request: Request):
    if not reset_limiter.allow(_ip(request)):
        raise HTTPException(429, "Too many attempts. Wait a minute.")
    row = db.find_reset_token(auth.hash_reset_token(token))
    if not row:
        raise HTTPException(400, "This reset link is invalid, already used, or expired. "
                            "Ask for a new one.")
    u = db.get_user(row["user_id"])
    return {"first_name": u["first_name"] if u else "", "email": u["slack_email"] if u else "",
            "expires_at": row["expires_at"]}


@app.post("/api/auth/reset")
def reset_password(body: ResetBody, request: Request, response: Response):
    if not reset_limiter.allow(_ip(request)):
        raise HTTPException(429, "Too many attempts. Wait a minute.")
    row = db.find_reset_token(auth.hash_reset_token(body.token))
    u = db.get_user(row["user_id"]) if row else None
    if not (row and u):
        raise HTTPException(400, "This reset link is invalid, already used, or expired. "
                            "Ask for a new one.")
    auth.check_password_strength(body.password, u["slack_email"])
    if not db.use_reset_token(row["id"]):
        raise HTTPException(400, "This reset link was just used. Ask for a new one.")
    db.set_password(u["id"], auth.hash_password(body.password))  # signs out every device
    db.resolve_reset_requests(u["id"])
    db.log_safety(_ip(request), "password_reset", f"user {u['id']}")
    response.delete_cookie(auth.USER_COOKIE)
    return {"ok": True, "email": u["slack_email"]}


class NewPasswordBody(BaseModel):
    new_password: str = Field(max_length=200)


@app.post("/api/me/new-password")
def forced_new_password(body: NewPasswordBody, request: Request, response: Response,
                        user: Annotated[dict, Depends(auth.require_user)]):
    """Only for an intern who just signed in with an emailed temporary password."""
    if user.get("is_admin_view") or not user.get("must_change_password"):
        raise HTTPException(400, "Use Change password in Edit my profile instead.")
    auth.check_password_strength(body.new_password, user["slack_email"])
    if auth.verify_password(body.new_password, user["password_hash"]):
        raise HTTPException(400, "Choose a password you haven't used here before.")
    db.set_password(user["id"], auth.hash_password(body.new_password))
    db.resolve_reset_requests(user["id"])
    fresh = db.get_user(user["id"])
    auth.set_cookie(response, request, auth.USER_COOKIE, auth.user_cookie_value(fresh),
                    auth.USER_SESSION_SECONDS)
    return {"ok": True}


@app.post("/api/me/password")
def change_password(body: ChangePasswordBody, request: Request, response: Response,
                    user: Annotated[dict, Depends(auth.require_user)]):
    if user.get("is_admin_view"):
        raise HTTPException(400, "Change the admin password with scripts/set_admin_password.py.")
    if not login_limiter.allow(_ip(request)):
        raise HTTPException(429, "Too many attempts. Wait a minute.")
    if not auth.verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(400, "Your current password isn't right.")
    auth.check_password_strength(body.new_password, user["slack_email"])
    db.set_password(user["id"], auth.hash_password(body.new_password))
    # Other devices are signed out by the version bump; keep this one signed in.
    fresh = db.get_user(user["id"])
    auth.set_cookie(response, request, auth.USER_COOKIE, auth.user_cookie_value(fresh),
                    auth.USER_SESSION_SECONDS)
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.USER_COOKIE)
    response.delete_cookie(auth.ADMIN_COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user: Annotated[dict, Depends(auth.require_user)]):
    if user.get("is_admin_view"):
        return {"user": None, "admin_view": True}
    return {"user": _public_user(user, include_private=True)}


@app.put("/api/me")
def update_me(body: ProfileFields, request: Request,
              user: Annotated[dict, Depends(auth.require_user)]):
    if user.get("is_admin_view"):
        raise HTTPException(400, "The admin account has no intern profile.")
    email = str(body.slack_email).lower()
    other = db.get_user_by_email(email)
    if other and other["id"] != user["id"]:
        raise HTTPException(409, "Another account already uses that email.")
    db.update_user(user["id"], {**body.model_dump(), "slack_email": email})
    STATES.pop(safety.hash_session(f"user:{user['id']}"), None)  # matchmaker re-reads it
    return {"user": _public_user(db.get_user(user["id"]), include_private=True)}


PHOTO_MAX_BYTES = 5_000_000
PHOTO_SIZE = 480


def _process_photo(data: bytes) -> bytes:
    """Re-encode as a square JPEG. Re-encoding drops EXIF (including GPS location) and
    anything that isn't really an image."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = 40_000_000
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("JPEG", "PNG", "WEBP", "MPO", "HEIF", "GIF"):
            raise HTTPException(400, "Upload a JPG, PNG, or WEBP photo.")
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise HTTPException(400, "That file isn't a photo we can read. Try a JPG or PNG.")
    img = ImageOps.fit(img, (PHOTO_SIZE, PHOTO_SIZE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


@app.post("/api/me/photo")
async def upload_photo(user: Annotated[dict, Depends(auth.require_user)],
                       file: Annotated[UploadFile, File()]):
    if user.get("is_admin_view"):
        raise HTTPException(400, "The admin account has no intern profile.")
    data = await file.read(PHOTO_MAX_BYTES + 1)
    if len(data) > PHOTO_MAX_BYTES:
        raise HTTPException(400, "That photo is over 5 MB.")
    db.set_photo(user["id"], _process_photo(data))
    return {"user": _public_user(db.get_user(user["id"]), include_private=True)}


@app.delete("/api/me/photo")
def delete_photo(user: Annotated[dict, Depends(auth.require_user)]):
    if not user.get("is_admin_view"):
        db.set_photo(user["id"], None)
    return {"ok": True}


@app.get("/api/users/{user_id}/photo")
def user_photo(user_id: int, request: Request, viewer: Annotated[dict, Depends(auth.require_user)]):
    u = db.get_user(user_id)
    # Other interns only see photos of people who share with the cohort; the admin and the
    # owner always can.
    allowed = u and (u["share_with_cohort"] or viewer.get("id") == user_id
                     or viewer.get("is_admin_view") or auth.is_admin(request))
    data = db.get_photo(user_id) if allowed else None
    if not data:
        raise HTTPException(404, "No photo.")
    return RawResponse(data, media_type="image/jpeg",
                       headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/cohort")
def cohort(user: Annotated[dict, Depends(auth.require_user)]):
    """Other interns who chose to share. Phone numbers are never included here. Fictional
    demo interns are visible to the admin (and to each other) but never to real interns."""
    show_samples = bool(user.get("is_admin_view") or user.get("is_sample"))
    return [_public_user(u) for u in db.list_users(cohort_only=True)
            if show_samples or not u.get("is_sample")]


@app.post("/api/admin/login")
def admin_login(body: AdminLoginBody, request: Request, response: Response):
    if not auth.admin_configured():
        raise HTTPException(503, "Admin sign-in isn't set up on this server. Run "
                            "scripts/set_admin_password.py, then restart.")
    if not login_limiter.allow(_ip(request)):
        raise HTTPException(429, "Too many attempts. Wait a minute.")
    if not auth.check_admin_login(body.username, body.password):
        db.log_safety(_ip(request), "admin_login_failed", body.username[:40])
        raise HTTPException(401, "Wrong username or password.")
    auth.set_cookie(response, request, auth.ADMIN_COOKIE,
                    auth.make_token("admin", config.ADMIN_USERNAME.lower(),
                                    auth.ADMIN_SESSION_SECONDS),
                    auth.ADMIN_SESSION_SECONDS)
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(response: Response):
    response.delete_cookie(auth.ADMIN_COOKIE)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Meta + directory
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    """Serve the page with ?v=<last-modified> on its CSS and JS, so browsers fetch the new
    files after every update instead of reusing a stale cached copy."""
    html = (config.FRONTEND_DIR / "index.html").read_text()
    for name in ("styles.css", "app.js"):
        version = int((config.FRONTEND_DIR / name).stat().st_mtime)
        html = html.replace(f"/static/{name}\"", f"/static/{name}?v={version}\"")
    return RawResponse(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/api/meta")
def meta(request: Request):
    return {
        "admin_name": config.ADMIN_NAME,
        "admin_title": config.ADMIN_TITLE,
        "is_admin": auth.is_admin(request),
        "admin_configured": auth.admin_configured(),
        "email_enabled": mailer.enabled(),
        "signed_in": auth.current_user(request) is not None or auth.is_admin(request),
        "provider": config.provider(),
        "match_model": config.model_name("match"),
        "fast_model": config.model_name("fast"),
        "llm_ready": config.has_llm_key(),
        "us_states": sorted(US_STATES - {"Other"}) + ["Other"],
        "sectors": list(directory.Sector.__args__),  # type: ignore[attr-defined]
        "people_count": len(directory.load_people()),
    }


def _card(p: directory.Person, full: bool = False) -> dict:
    d = p.model_dump()
    if not full:
        d["bio"] = p.bio[:400]
    return d


@app.get("/api/committees")
def public_committees():
    return [{k: c[k] for k in ("slug", "name", "description")}
            for c in placements.list_committees(active_only=True)]


@app.get("/api/people", dependencies=[Depends(auth.require_user)])
def people(q: str = "", sector: str = "", section: str = ""):
    return [_card(p, full=True) for p in directory.search(q[:200], sector, section)]


@app.get("/api/people/{slug}", dependencies=[Depends(auth.require_user)])
def person(slug: str):
    p = directory.get_person(slug)
    if not p:
        raise HTTPException(404, "Not found")
    talks = [e for e in db.list_events(upcoming_only=True) if slug in e["speaker_slugs"]]
    return {**_card(p, full=True), "talks": talks}


# ---------------------------------------------------------------------------
# Resume + chat
# ---------------------------------------------------------------------------
def _extract_text(name: str, data: bytes) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages[:10])
    if lower.endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    if lower.endswith((".txt", ".md")):
        return data.decode("utf8", "ignore")
    raise HTTPException(400, "Upload a PDF, DOCX, or TXT file.")


@app.post("/api/profile")
async def build_profile(
    session: Annotated[str, Depends(session_key)],
    user: Annotated[dict, Depends(auth.require_user)],
    intro: Annotated[str, Form()] = "",
    resume_text: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
):
    text = resume_text
    if file is not None and file.filename:
        data = await file.read(config.MAX_UPLOAD_BYTES + 1)
        if len(data) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(400, "That file is over 2 MB.")
        text = _extract_text(file.filename, data)
    text = text[: config.MAX_RESUME_CHARS]
    if not text.strip() and not intro.strip():
        raise HTTPException(400, "Add a resume or tell us a bit about yourself.")
    try:
        safety.preflight(session, intro[: config.MAX_MESSAGE_CHARS], 0)
    except safety.Blocked as exc:
        return _blocked(exc)

    # Layer 1: deterministic screen strips instruction-like lines before any model sees them.
    screened = safety.screen(text)
    intro_screened = safety.screen(intro)
    removed = screened.matches + intro_screened.matches
    if removed:
        db.log_safety(session, "injection_screened", " || ".join(removed))

    # Layer 2: the profile agent reads the cleaned text and flags anything subtler.
    try:
        profile, report = await agents.read_profile(session, screened.clean_text,
                                                    intro_screened.clean_text)
    except Exception as exc:  # noqa: BLE001  provider outage, bad key, etc.
        log.exception("profile read failed")
        db.log_safety(session, "model_error", type(exc).__name__)
        return JSONResponse({"blocked": "model_error", "message": _provider_message(exc)},
                            status_code=502)
    st = state_for(session, user)
    st.profile = profile
    await agents.profile_update_turn(st)
    return {
        "profile": profile.model_dump(),
        "usage": report.__dict__,
        "screened_lines": removed,
        "chars_read": len(text),
    }


class ChatBody(BaseModel):
    message: str = Field(min_length=1)

    @field_validator("message")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


@app.post("/api/chat")
async def chat(body: ChatBody, session: Annotated[str, Depends(session_key)],
               user: Annotated[dict, Depends(auth.require_user)]):
    st = state_for(session, user)
    try:
        safety.preflight(session, body.message, st.turns)
    except safety.Blocked as exc:
        return _blocked(exc)

    screened = safety.screen(body.message)
    if screened.flagged:
        db.log_safety(session, "injection_in_chat", " || ".join(screened.matches))
        if not screened.clean_text.strip():
            return {
                "reply": "I can only help you find the right Think Tank members to meet and "
                         "prepare for talks. I can't change my instructions or share them. "
                         "What are you interested in?",
                "recommendations": [], "events": [], "usage": None,
                "guardrail": "Blocked an instruction-override attempt before it reached the model.",
            }

    try:
        out, report = await agents.chat_turn(st, session, screened.clean_text)
    except UsageLimitExceeded:
        return JSONResponse({"blocked": "run_limit", "message": "That request needed more work "
                             "than one turn allows. Try a narrower question."}, status_code=400)
    except Exception as exc:  # provider outage, bad key, etc.
        log.exception("chat failed")
        db.log_safety(session, "model_error", type(exc).__name__)
        return JSONResponse({"blocked": "model_error", "message": _provider_message(exc)},
                            status_code=502)

    people_map = directory.by_slug()
    recs = [{**r.model_dump(), "person": _card(people_map[r.slug])} for r in out.recommendations]
    events = [e for e in db.list_events(upcoming_only=True) if e["id"] in out.suggested_event_ids]
    return {
        "reply": out.reply,
        "recommendations": recs,
        "events": events,
        "usage": report.__dict__,
        "guardrail": "Removed instruction-like text from your message." if screened.flagged else None,
    }


@app.post("/api/chat/reset")
def chat_reset(session: Annotated[str, Depends(session_key)]):
    STATES.pop(session, None)
    return {"ok": True}


@app.get("/api/chat/state")
def chat_state(session: Annotated[str, Depends(session_key)],
               user: Annotated[dict, Depends(auth.require_user)]):
    st = state_for(session, user)
    return {"profile": st.profile.model_dump() if st and st.profile else None,
            "turns": st.turns if st else 0, "max_turns": config.MAX_TURNS_PER_SESSION}


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
def _with_speakers(e: dict) -> dict:
    people_map = directory.by_slug()
    e["speakers"] = [{"slug": s, "name": people_map[s].name, "org": people_map[s].org,
                      "photo_url": people_map[s].photo_url}
                     for s in e["speaker_slugs"] if s in people_map]
    return e


@app.get("/api/events", dependencies=[Depends(auth.require_user)])
def events(include_past: bool = False):
    return [_with_speakers(e) for e in db.list_events(upcoming_only=not include_past)]


@app.post("/api/events/{event_id}/rsvp")
def rsvp(event_id: int, user: Annotated[dict, Depends(auth.require_user)]):
    if not db.get_event(event_id):
        raise HTTPException(404, "No such event.")
    name = f"{user['first_name']} {user['last_name']}".strip()
    if user.get("slack_email"):
        name += f" <{user['slack_email']}>"
    return {"rsvp_count": db.add_rsvp(event_id, name)}


@app.get("/api/announcements", dependencies=[Depends(auth.require_user)])
def announcements():
    return db.list_announcements()


# iCalendar export. VALARMs give interns phone reminders 1 day and 1 hour before.
VTIMEZONE = """BEGIN:VTIMEZONE
TZID:America/Chicago
BEGIN:DAYLIGHT
TZOFFSETFROM:-0600
TZOFFSETTO:-0500
TZNAME:CDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0500
TZOFFSETTO:-0600
TZNAME:CST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def _ics_dt(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%Y%m%dT%H%M%S")


def _vevent(e: dict) -> str:
    speakers = ", ".join(s["name"] for s in _with_speakers(e)["speakers"])
    desc = "\n".join(x for x in [e["description"], f"Speakers: {speakers}" if speakers else "",
                                 f"Prep: {e['prep_note']}" if e["prep_note"] else "",
                                 e["join_url"]] if x)
    lines = [
        "BEGIN:VEVENT",
        f"UID:css-intern-event-{e['id']}@cyber-security-summit-interns",
        f"DTSTAMP:{datetime.utcnow():%Y%m%dT%H%M%SZ}",
        f"DTSTART;TZID=America/Chicago:{_ics_dt(e['starts_at'])}",
        f"DTEND;TZID=America/Chicago:{_ics_dt(e['ends_at'])}",
        f"SUMMARY:{_ics_escape(e['title'])}",
        f"DESCRIPTION:{_ics_escape(desc)}",
        f"LOCATION:{_ics_escape(e['join_url'] or e['location'])}",
    ]
    for trigger, label in (("-P1D", "tomorrow"), ("-PT1H", "in 1 hour")):
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{trigger}",
                  f"DESCRIPTION:{_ics_escape(e['title'])} {label}", "END:VALARM"]
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def _calendar(evts: list[dict]) -> PlainTextResponse:
    body = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0",
                        "PRODID:-//CSS Intern Hub//EN", "CALSCALE:GREGORIAN",
                        "X-WR-CALNAME:CSS Intern Think Tank Talks",
                        VTIMEZONE.replace("\n", "\r\n"), *(_vevent(e) for e in evts),
                        "END:VCALENDAR"]) + "\r\n"
    return PlainTextResponse(body, media_type="text/calendar",
                             headers={"Content-Disposition": 'attachment; filename="css-talks.ics"'})


@app.get("/api/calendar.ics", dependencies=[Depends(auth.require_user)])
def calendar_feed():
    return _calendar(db.list_events(upcoming_only=True))


@app.get("/api/events/{event_id}.ics", dependencies=[Depends(auth.require_user)])
def event_ics(event_id: int):
    e = db.get_event(event_id)
    if not e:
        raise HTTPException(404, "No such event.")
    return _calendar([e])


# ---------------------------------------------------------------------------
# Admin: events, announcements, usage
# ---------------------------------------------------------------------------
class EventBody(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    kind: str = Field("Think Tank talk", max_length=60)
    speaker_slugs: list[str] = Field(default_factory=list, max_length=8)
    starts_at: str
    ends_at: str
    location: str = Field("", max_length=200)
    join_url: str = Field("", max_length=500)
    description: str = Field("", max_length=2000)
    prep_note: str = Field("", max_length=500)
    required: bool = False

    @field_validator("starts_at", "ends_at")
    @classmethod
    def iso_minute(cls, v: str) -> str:
        return datetime.fromisoformat(v).strftime("%Y-%m-%dT%H:%M")

    @field_validator("join_url")
    @classmethod
    def http_only(cls, v: str) -> str:
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("Join link must start with https://")
        return v

    @field_validator("speaker_slugs")
    @classmethod
    def known_speakers(cls, v: list[str]) -> list[str]:
        known = directory.by_slug()
        unknown = [s for s in v if s not in known]
        if unknown:
            raise ValueError(f"Unknown speakers: {unknown}")
        return v


def _check_times(body: EventBody) -> None:
    if datetime.fromisoformat(body.ends_at) <= datetime.fromisoformat(body.starts_at):
        raise HTTPException(400, "End time must be after start time.")
    if datetime.fromisoformat(body.ends_at) - datetime.fromisoformat(body.starts_at) > timedelta(hours=12):
        raise HTTPException(400, "Events longer than 12 hours aren't supported.")


@app.get("/api/admin/events", dependencies=[Depends(auth.require_admin)])
def admin_events():
    return [_with_speakers(e) for e in db.list_events()]


@app.post("/api/admin/events", dependencies=[Depends(auth.require_admin)])
def admin_create_event(body: EventBody):
    _check_times(body)
    return {"id": db.save_event(body.model_dump())}


@app.put("/api/admin/events/{event_id}", dependencies=[Depends(auth.require_admin)])
def admin_update_event(event_id: int, body: EventBody):
    if not db.get_event(event_id):
        raise HTTPException(404, "No such event.")
    _check_times(body)
    return {"id": db.save_event(body.model_dump(), event_id)}


@app.delete("/api/admin/events/{event_id}", dependencies=[Depends(auth.require_admin)])
def admin_delete_event(event_id: int):
    if not db.delete_event(event_id):
        raise HTTPException(404, "No such event.")
    return {"ok": True}


@app.get("/api/admin/events/{event_id}/rsvps", dependencies=[Depends(auth.require_admin)])
def admin_rsvps(event_id: int):
    return {"names": db.list_rsvps(event_id)}


class AnnouncementBody(BaseModel):
    message: str = Field(min_length=3, max_length=400)


@app.post("/api/admin/announcements", dependencies=[Depends(auth.require_admin)])
def admin_add_announcement(body: AnnouncementBody):
    return {"id": db.add_announcement(body.message.strip())}


@app.delete("/api/admin/announcements/{ann_id}", dependencies=[Depends(auth.require_admin)])
def admin_archive_announcement(ann_id: int):
    db.archive_announcement(ann_id)
    return {"ok": True}


@app.get("/api/admin/usage", dependencies=[Depends(auth.require_admin)])
def admin_usage():
    return {**db.usage_summary(), "daily_budget": config.DAILY_TOKEN_BUDGET,
            "match_model": config.model_name("match"), "fast_model": config.model_name("fast")}


# ---------------------------------------------------------------------------
# Admin: intern profiles + access code
# ---------------------------------------------------------------------------
@app.get("/api/admin/interns", dependencies=[Depends(auth.require_admin)])
def admin_interns():
    return [_public_user(u, include_private=True) for u in db.list_users()]


@app.delete("/api/admin/interns/{user_id}", dependencies=[Depends(auth.require_admin)])
def admin_delete_intern(user_id: int):
    if not db.delete_user(user_id):
        raise HTTPException(404, "No such intern.")
    STATES.pop(safety.hash_session(f"user:{user_id}"), None)
    return {"ok": True}


@app.get("/api/admin/interns.csv", dependencies=[Depends(auth.require_admin)])
def admin_interns_csv():
    cols = ["first_name", "last_name", "phone", "slack_email", "city", "state", "school",
            "interests", "hobbies", "goals", "fun_fact", "linkedin_url", "share_with_cohort",
            "created_at", "last_login"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c.replace("slack_email", "preferred_email").replace("school", "school_or_employer")
                for c in cols])
    for u in db.list_users():
        # Neutralize spreadsheet formulas in user-typed fields (CSV injection).
        w.writerow([("'" + str(u[c])) if str(u[c] or "").startswith(("=", "+", "-", "@"))
                    and c != "phone" else u[c] for c in cols])
    return RawResponse(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="css-interns-{datetime.now():%Y%m%d}.csv"'})


@app.get("/api/admin/reset-requests", dependencies=[Depends(auth.require_admin)])
def admin_reset_requests():
    return {"requests": db.list_reset_requests(), "email_enabled": mailer.enabled()}


@app.post("/api/admin/interns/{user_id}/reset-link", dependencies=[Depends(auth.require_admin)])
def admin_reset_link(user_id: int, request: Request):
    u = db.get_user(user_id)
    if not u:
        raise HTTPException(404, "No such intern.")
    token = auth.new_reset_token(user_id, "admin")
    db.resolve_reset_requests(user_id)
    return {"link": _reset_link(request, token), "hours": auth.RESET_TTL_HOURS,
            "name": f"{u['first_name']} {u['last_name']}", "email": u["slack_email"]}


@app.get("/api/admin/access-code", dependencies=[Depends(auth.require_admin)])
def admin_access_code():
    return {"code": auth.access_code()}


@app.post("/api/admin/access-code", dependencies=[Depends(auth.require_admin)])
def admin_rotate_access_code():
    return {"code": auth.rotate_access_code()}


# ---------------------------------------------------------------------------
# Admin: Committee & Cyber Coach Matchmaker
# ---------------------------------------------------------------------------
class CommitteeBody(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field("", max_length=600)
    needs: str = Field("", max_length=600)
    capacity: int = Field(ge=0, le=50)
    director_slug: str = Field("", max_length=80)
    director_name: str = Field("", max_length=120)
    director_email: str = Field("", max_length=200)
    meeting_day: str = Field("", pattern=r"^(|Mon|Tue|Wed|Thu|Fri|Sat|Sun)$")
    meeting_time: str = Field("", pattern=r"^(|[01]\d:[0-5]\d|2[0-3]:[0-5]\d)$")
    meeting_note: str = Field("", max_length=120)
    active: bool = True

    @field_validator("director_slug")
    @classmethod
    def real_person(cls, v: str) -> str:
        if v and not directory.get_person(v):
            raise ValueError("Unknown Think Tank member.")
        return v


class CoachBody(BaseModel):
    capacity: int | None = Field(None, ge=0, le=10)
    active: bool | None = None


class PlacementBody(BaseModel):
    committee: str | None = None
    coach: str | None = None
    status: str | None = Field(None, pattern=r"^(proposed|approved)$")


def _agent_error(exc: Exception) -> JSONResponse:
    log.exception("admin agent failed")
    msg = str(exc) if isinstance(exc, ValueError) else _provider_message(exc)
    return JSONResponse({"message": msg}, status_code=400 if isinstance(exc, ValueError) else 502)


def _provider_message(exc: Exception) -> str:
    """Plain-language reason an AI call failed, without exposing any key."""
    text = f"{type(exc).__name__} {exc}".lower()
    if "authentication" in text or "invalid x-api-key" in text or "401" in text:
        return ("The AI service rejected this app's API key, so AI features are paused. "
                "The Program Chair needs to update the key in .env and restart the app.")
    if "credit" in text or "billing" in text or "402" in text:
        return "The AI account is out of credits. The Program Chair needs to check billing."
    if "rate" in text and "limit" in text or "429" in text or "overloaded" in text or "529" in text:
        return "The AI service is busy right now. Wait a minute and try again."
    return "The AI service returned an error. Try again in a moment."


@app.get("/api/admin/committees", dependencies=[Depends(auth.require_admin)])
def admin_committees():
    return placements.list_committees()


@app.put("/api/admin/committees/{slug}", dependencies=[Depends(auth.require_admin)])
def admin_update_committee(slug: str, body: CommitteeBody):
    if slug not in {c["slug"] for c in placements.list_committees()}:
        raise HTTPException(404, "No such committee.")
    placements.update_committee(slug, body.model_dump())
    return {"ok": True}


@app.get("/api/admin/coaches", dependencies=[Depends(auth.require_admin)])
def admin_coaches():
    return placements.list_coaches()


@app.put("/api/admin/coaches/{slug}", dependencies=[Depends(auth.require_admin)])
def admin_update_coach(slug: str, body: CoachBody):
    if not directory.get_person(slug):
        raise HTTPException(404, "No such Think Tank member.")
    placements.update_coach(slug, body.capacity, body.active)
    return {"ok": True}


@app.get("/api/admin/placements", dependencies=[Depends(auth.require_admin)])
def admin_placements():
    return placements.latest_run() or {"rows": [], "unplaced": [], "run_id": None}


@app.post("/api/admin/placements/run", dependencies=[Depends(auth.require_admin)])
async def admin_run_placements():
    try:
        return await placements.run_matching()
    except Exception as exc:  # noqa: BLE001
        return _agent_error(exc)


@app.put("/api/admin/placements/{user_id}", dependencies=[Depends(auth.require_admin)])
def admin_update_placement(user_id: int, body: PlacementBody):
    if body.committee and body.committee not in {c["slug"] for c in placements.list_committees()}:
        raise HTTPException(400, "Unknown committee.")
    if body.coach and not directory.get_person(body.coach):
        raise HTTPException(400, "Unknown coach.")
    placements.set_placement(user_id, body.committee, body.coach, body.status)
    return {"ok": True}


@app.get("/api/admin/placements/{user_id}/drafts", dependencies=[Depends(auth.require_admin)])
def admin_get_drafts(user_id: int):
    return {"drafts": placements.get_drafts(user_id)}


@app.post("/api/admin/placements/{user_id}/drafts", dependencies=[Depends(auth.require_admin)])
async def admin_make_drafts(user_id: int):
    try:
        return await placements.draft_intros(user_id)
    except Exception as exc:  # noqa: BLE001
        return _agent_error(exc)


# ---------------------------------------------------------------------------
# Admin: demo data
# ---------------------------------------------------------------------------
@app.get("/api/admin/demo", dependencies=[Depends(auth.require_admin)])
def admin_demo_status():
    return demo.status()


@app.post("/api/admin/demo", dependencies=[Depends(auth.require_admin)])
def admin_demo_load():
    return demo.load()


@app.delete("/api/admin/demo", dependencies=[Depends(auth.require_admin)])
def admin_demo_remove():
    return demo.remove()


# ---------------------------------------------------------------------------
# Admin: Cohort Pulse & Monday Brief
# ---------------------------------------------------------------------------
@app.get("/api/admin/pulse/sources", dependencies=[Depends(auth.require_admin)])
def admin_pulse_sources():
    return pulse.sources_status()


@app.post("/api/admin/pulse/slack-export", dependencies=[Depends(auth.require_admin)])
async def admin_pulse_export(file: Annotated[UploadFile, File()]):
    data = await file.read(50_000_001)
    if len(data) > 50_000_000:
        raise HTTPException(400, "Export is over 50 MB. Export a shorter date range.")
    try:
        return pulse.ingest_slack_export(data)
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise HTTPException(400, "That doesn't look like a Slack export .zip.")


@app.post("/api/admin/pulse/slack-sync", dependencies=[Depends(auth.require_admin)])
def admin_pulse_sync():
    if not pulse.slack_live_enabled():
        raise HTTPException(400, "Add SLACK_BOT_TOKEN to .env and restart to connect Slack.")
    try:
        return pulse.sync_slack_live()
    except Exception as exc:  # noqa: BLE001
        log.exception("slack sync failed")
        raise HTTPException(502, f"Slack sync failed: {exc}")


@app.post("/api/admin/pulse/notes", dependencies=[Depends(auth.require_admin)])
async def admin_pulse_add_notes(
    committee: Annotated[str, Form(max_length=120)] = "",
    meeting_date: Annotated[str, Form()] = "",
    title: Annotated[str, Form(max_length=200)] = "",
    text: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
):
    if file is not None and file.filename:
        data = await file.read(config.MAX_UPLOAD_BYTES + 1)
        if len(data) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(400, "Notes file is over 2 MB.")
        text = _extract_text(file.filename, data)
    if not text.strip():
        raise HTTPException(400, "Paste notes or upload a file.")
    try:
        meeting_date = datetime.fromisoformat(meeting_date).strftime("%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Pick the meeting date.")
    return {"id": pulse.add_notes(committee.strip(), meeting_date, title.strip(), text)}


@app.delete("/api/admin/pulse/notes/{note_id}", dependencies=[Depends(auth.require_admin)])
def admin_pulse_delete_notes(note_id: int):
    pulse.delete_notes(note_id)
    return {"ok": True}


@app.post("/api/admin/pulse/brief", dependencies=[Depends(auth.require_admin)])
async def admin_pulse_brief():
    try:
        return await pulse.generate_brief()
    except Exception as exc:  # noqa: BLE001
        return _agent_error(exc)


@app.get("/api/admin/pulse/briefs", dependencies=[Depends(auth.require_admin)])
def admin_pulse_briefs():
    return {"briefs": pulse.list_briefs(), "latest": pulse.get_brief()}


@app.get("/api/admin/pulse/briefs/{brief_id}", dependencies=[Depends(auth.require_admin)])
def admin_pulse_brief_get(brief_id: int):
    b = pulse.get_brief(brief_id)
    if not b:
        raise HTTPException(404, "No such brief.")
    return b


@app.get("/api/admin/pulse/briefs/{brief_id}/markdown", dependencies=[Depends(auth.require_admin)])
def admin_pulse_brief_md(brief_id: int):
    b = pulse.get_brief(brief_id)
    if not b:
        raise HTTPException(404, "No such brief.")
    return PlainTextResponse(pulse.brief_markdown(b), media_type="text/markdown", headers={
        "Content-Disposition": f'attachment; filename="chairs-monday-brief-{b["week_of"]}.md"'})
