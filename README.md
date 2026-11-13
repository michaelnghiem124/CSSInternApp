# CSS Intern Hub

An AI-powered resource for the **Cyber Security Summit Intern Program** (Minneapolis). Interns use it to find out which Think Tank members (CISOs, security directors, military cyber officers, government and legal leaders) they should meet, and why. The Program Chair uses it to put Think Tank talks on the interns' calendars.

- **Matchmaker**: an intern uploads a resume and says what they're curious about. Two agents read it and recommend 3 to 5 Think Tank members, each with a reason grounded in that person's published bio, shared ground, and conversation starters.
- **Think Tank directory**: all 64 published bios (2 co-chairs, 49 members, 13 emeritus), searchable by name, employer, school, skill, or hobby, and filterable by sector.
- **Talks calendar**: the Chair schedules CISO firesides, panels, and office hours. Interns RSVP and add talks to their own calendars (`.ics` with reminders 1 day and 1 hour before). Talks within 48 hours, and the Chair's announcements, show as banners on every page.
- **Admin**: event and announcement management, RSVP lists, and a live dashboard of AI token usage, cost, and blocked safety events.

## Run it locally

Requirements: Python 3.11 or newer. These steps take about 3 minutes.

```bash
git clone <this repo> css-intern-hub
cd css-intern-hub
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**API key.** The app looks for `.env` in this folder first, then in the **folder above it**. It needs one of:

| Key | Model used |
|---|---|
| `ANTHROPIC_API_KEY` | Claude Sonnet 5.5 (matchmaker) + Claude Haiku 5.5 (profile reader). Used if present. |
| `PORTKEY_API_KEY` | `gpt-6-luna` through the course's Portkey gateway, for both agents. |

To use your own `.env` here instead of the one above: `cp .env.example .env` and fill in one key.

**Start:**

```bash
.venv/bin/uvicorn backend.main:app --port 8000
```

Open <http://localhost:8000>.

## Accounts and sign-in

The whole site sits behind a sign-in. Only the sign-in/register screen is public.

- **Interns** choose **Register** and enter the **program access code**, which the admin sees and can rotate on the Admin page. Registration asks for:
  - first and last name, cell phone, Slack email (used to sign in), city and state
  - school (optional)
  - interests, hobbies, and goals for the program
  - a password

  The matchmaker uses the interests, hobbies, and goals straight away; a resume is optional.
- **Cohort tab:** interns who opt in see each other's name, location, school, Slack email, interests, hobbies, and goals. **Phone numbers are visible only to the admin.**
- **Admin (Program Chair):** one account. Set it once:

  ```bash
  .venv/bin/python -m scripts.set_admin_password
  ```

  You type a username and password at a hidden prompt. Only a scrypt hash is saved to `.env`. Restart the server, then choose **Admin** on the sign-in screen. The Admin page lists every intern profile (with search, a detail view, delete, and CSV export), the access code, talks, announcements, and AI usage. **There is no default admin password: until you run the script, admin sign-in is disabled.**

**Testing from a fresh clone:** run the setup script to create your own admin login. Sign in as admin to see every tab and the access code, then register a test intern with that code.

Passwords are hashed with scrypt. Sessions are signed HttpOnly cookies (14 days for interns, 8 hours for admin). Sign-in and registration are rate-limited.

**Try it fast:** `samples/sample_resume_jordan.txt` is a fictional student resume you can upload in the Matchmaker. `samples/injected_resume.txt` contains a hidden prompt injection, for watching the defenses work.

Without any API key, the Directory, Calendar, and Admin pages still work, and the Matchmaker says it is offline.

## How it works

```
Browser (frontend/: HTML + JS, no build step)
   │
FastAPI (backend/main.py)
   ├─ safety.preflight ─ length caps · per-session rate limit · turn cap · daily token budget
   ├─ safety.screen ──── strips instruction-like lines from resumes and messages (free, deterministic)
   │
   ├─ Agent 1: Profile Reader (Haiku 5.5 / gpt-6-luna)
   │     resume + intro → InternProfile (skills, interests, hobbies, background)
   │     flags hidden instructions it notices → injection_detected
   │
   └─ Agent 2: Matchmaker (Sonnet 5.5 / gpt-6-luna)
         cached instructions = program context + compact roster of all 64 members
         tools: get_bio(slug) · search_directory(query, sector) · upcoming_talks()
         output: MatchReply (reply, recommendations[], suggested_event_ids[])
         output validator: every slug and event id must exist, or the model retries
```

- **Data.** `data/think_tank_raw.json` contains the bios as published at cybersecuritysummit.org/2026-think-tank. `data/think_tank.json` adds structured facts (title, sector, expertise, background, interests). `scripts/enrich_bios.py` extracted them once with Haiku: 64 calls, $0.023 total.
- **Storage.** SQLite at `data/app.db` (gitignored), created on first run with three sample talks marked "Sample". It holds intern accounts, events, RSVPs, announcements, the access code, and token/safety logs. **Resumes and chat text are never written to disk.** Conversations live in server memory for 3 hours.
- **Cost controls.** The roster sits in the matchmaker's instructions, which never change between requests, so the provider caches them (`cache=True`). Thinking effort is `low`. The cheap model reads resumes, so the expensive model never sees raw resume text. Every reply shows its own token counts and cost.

## Configuration

Every setting is in `backend/config.py` and can be overridden from `.env`: model names, prices, rate limit, daily token budget, per-run request/tool/token limits, and admin name/title.

## Deploying

Any host that runs a Python web process works (Render, Railway, Fly.io). Set these environment variables:
- `ANTHROPIC_API_KEY` (or `PORTKEY_API_KEY`)
- `ADMIN_USERNAME` and `ADMIN_PASSWORD_HASH` (copy both from the `.env` the setup script wrote)
- `SESSION_SECRET` (any long random string)
- optionally `DB_PATH` on a persistent disk, so events survive restarts

Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.

## Project layout

```
backend/   main.py (API) · agents.py · safety.py · directory.py · db.py · config.py · prompts/
frontend/  index.html · styles.css · app.js
data/      think_tank_raw.json · think_tank.json
scripts/   enrich_bios.py
samples/   fictional resumes for testing
```

Bios and headshots belong to the Cyber Security Summit and its Think Tank members. They are shown here for the intern program's use.
