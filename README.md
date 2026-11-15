# Intern Summit Hub

An AI-powered resource for the **Cyber Security Summit Intern Program** (Minneapolis). Interns use it to find out which Think Tank members (CISOs, security directors, military cyber officers, government and legal leaders) they should meet, and why. The Program Lead uses it to put Think Tank talks on the interns' calendars.

- **Mentor Match**: an intern uploads a resume and says what they're curious about. Two agents read it and recommend 3 to 5 Think Tank members, each with a reason grounded in that person's published bio, shared ground, and conversation starters.
- **Think Tank directory**: all 64 published bios (2 co-chairs, 49 members, 13 emeritus), searchable by name, employer, school, skill, or hobby, and filterable by sector.
- **Home**: the intern front door (and where the logo leads): a welcome hero with live counts (Think Tank leaders, member organizations, open roles, events this year), cards with icons for each intern tool, the next event, and a profile-completeness nudge. Admin tools aren't advertised there.
- **Profile**: click your name (top right) to update your photo, LinkedIn, interests, hobbies, goals, committee preferences, and contact details, upload or remove your resume (which also refreshes your Mentor Match profile), and change your password.
- **Mentor Match knows past Summit talks**: `data/summit_talks.json` (built by `scripts/fetch_summit_talks.py` from the Summit's own 2016–2025 presentations pages) records 67 talks by 33 Think Tank and Emeritus members; a speaker is linked only on a confident name match. The matcher has a `summit_talks(topic)` tool, `get_bio` returns each member's past talks, and each reply ends with 2–4 follow-up questions built from that answer. It answers hiring, publication, and visa-sponsorship questions only from bios, Summit records, and posting eligibility notes, and says so when it doesn't know.
- **Events**: Think Tank Talks (CISO firesides, panels, office hours) and the monthly **intern huddles** with the Program Co-Chairs. Import huddles from the program's tracking sheet with `.venv/bin/python -m scripts.import_huddles huddles.csv` (CSV export of the huddles tab); it reads only date, time, platform, and status, skips canceled huddles and duplicates, and never imports the notes or organizer columns, which can contain names and emails. The Program Lead schedules events in Admin → Talks. Interns RSVP and add talks to their own calendars, with reminders 1 day and 1 hour before. Each upcoming event has its own **Add to calendar** (`.ics`) button. A live subscription feed is built in the backend but has no button yet: `GET /api/me/calendar-link` returns a private per-person link to `/api/calendar/<token>.ics` (random token, talk schedule only, resettable). It stays hidden because Google and Outlook can only read it once the Hub is deployed, and using it alongside emailed invites would duplicate every talk. Next step: send invites through the Google Calendar API so the Program Lead keeps managing RSVPs in Google Calendar. Talks within 48 hours, and the Program Lead's announcements, show as banners on every page.
- **Admin**: intern profiles, talks and announcements, RSVP lists, a live dashboard of AI token usage, cost, and blocked safety events, plus two admin agents:
  - **Committee & Cyber Coach Matchmaker**: places every intern on a Summit committee and pairs them with a Think Tank Cyber Coach. It produces a ranked assignment matrix with one-sentence "why this match works" lines, and drafts intro emails and Slack DMs.
  - **Cohort Pulse & Program Lead's Monday Brief**: reads Slack (through an MCP server) and committee meeting notes. It flags interns who need a nudge, wins to spotlight, and blockers to escalate, with names pseudonymized before the AI sees anything.

## Course deliverables

- [`report.html`](report.html): the written report (13 printed pages, US Letter), self-contained with every screenshot embedded.
- [`slides.html`](slides.html): the 10-slide reveal.js deck. Arrow keys move between slides; **F** goes fullscreen.

Both are built from editable sources in `report/` (`report_src.html`, `slides_src.html`, `diagram.svg`, `img/`). After editing, run `python3 scripts/build_deliverables.py` to inline the images and regenerate both files.

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
| `ANTHROPIC_API_KEY` | Claude Sonnet 5.5 (Mentor Matcher, Placement Matcher, Pulse Analyst) + Claude Haiku 5.5 (profile readers, intro drafter). Used if present. |
| `PORTKEY_API_KEY` | `gpt-6-luna` through the course's Portkey gateway, for both agents. |

To use your own `.env` here instead of the one above: `cp .env.example .env` and fill in one key.

**Start:**

```bash
.venv/bin/uvicorn backend.main:app --port 8000
```

Open <http://localhost:8000>.

## Accounts and sign-in

The whole site sits behind a sign-in. Only the sign-in/register screen is public.

- **Interns** choose **Register** and enter the **program access code**, which the admin sees and can rotate on the Admin page.
  - **Required:** first name, last name, cell phone, preferred email (used to sign in), city, state, and a password.
  - **Optional:** profile photo, school or current employer, LinkedIn profile, interests, hobbies, what they want from the program, and one fun fact.

  Photos are re-encoded server-side to a 480×480 JPEG, which strips EXIF data such as GPS location, and they're served only to signed-in users. Mentor Match uses the interests, hobbies, and goals straight away; a resume is optional.
- **Cohort tab:** interns who opt in see each other's photo, name, location, school or employer, email, LinkedIn, interests, hobbies, goals, and fun fact. **Phone numbers are visible only to the admin.**
- **Admin (Program Lead):** one account. Set it once:

  ```bash
  .venv/bin/python -m scripts.set_admin_password
  ```

  You type a username and password at a hidden prompt. Only a scrypt hash is saved to `.env`. Restart the server, then choose **Admin** on the sign-in screen. The Admin page lists every intern profile (with search, a detail view, delete, and CSV export), the access code, talks, announcements, and AI usage.

  **Editing an intern's profile:** Admin → Interns → open an intern → **Edit profile**. The Program Lead can change everything on the card (name, contact details, school or employer, Summit role, background, interests, hobbies, goals, fun fact, LinkedIn) and upload or remove their photo. The same limits and LinkedIn check apply as on the intern's own form, and lines that read like instructions to an AI are removed, because profile text feeds the agents. Leaving email blank keeps an imported intern's placeholder login. The intern sees "Last updated by the Program Lead" on their profile and can change anything back; Mentor Match uses the new details on their next question. **There is no default admin password: until you run the script, admin sign-in is disabled.**

**Passwords:** at least 12 characters. Interns can change theirs under **Cohort → Edit my profile**; this requires the current password and signs out their other devices.

**Forgotten passwords:** the intern chooses **Forgot your password?** and enters their email. The response is the same whether or not the account exists. What happens next depends on whether email is set up.

- **With email set up (recommended).** A **temporary password**, 16 random characters such as `MXh6-AYjX-JERu-Qzev`, is emailed to the registered address.
  - It works **once** and expires after **24 hours**.
  - Signing in with it opens a **Set a new password** screen. Every other page and API call is blocked until a new 12+ character password is saved, and the new password can't be the old one.
  - The intern's **old password keeps working until the temporary one is used**, so nobody can lock an intern out by requesting resets for their email. Signing in with the old password cancels the temporary one.
  - Only a scrypt hash of the temporary password is stored.
- **Without email, or if sending fails.** The request appears in a **Password reset requests** banner on the Admin page. The admin clicks **Create reset link** and sends the one-time link (24 hours) on Slack. Only a SHA-256 hash of the link token is stored, and the token travels in the URL fragment, so it never reaches server logs.

Either way, setting the new password **signs out every other device**.

**The email itself** is branded so interns can tell it's genuine. It uses the Summit's navy header and logo, greets the intern by first name, names the account it was sent to and the Program Lead, shows the sign-in address as plain text, and closes with a "didn't ask for this?" note. A plain-text version is included for mail clients that don't show HTML.

- **Logo in every client:** save the Summit's logo as `frontend/img/css-logo.png` and it's embedded in each email. Without that file, the email loads the logo from cybersecuritysummit.org, and clients that block remote images still show the "CYBER SECURITY SUMMIT" header text.
- **Real web address in emails:** once deployed, set `PUBLIC_URL=https://your-site` in `.env` so the email links to the real site instead of localhost.

**Setting up email (Gmail example):**
1. Turn on 2-Step Verification for the Google account that will send the email.
2. Create an **App password** at myaccount.google.com → Security → App passwords.
3. Add these lines to `.env`, then restart the server:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=the-account@gmail.com
SMTP_PASSWORD=the-16-character-app-password
SMTP_FROM=Intern Summit Hub <the-account@gmail.com>
```

**Testing from a fresh clone:** run the setup script to create your own admin login. Sign in as admin to see every tab and the access code, then register a test intern with that code.

Passwords are hashed with scrypt. Sessions are signed HttpOnly cookies (14 days for interns, 8 hours for admin). Sign-in and registration are rate-limited.

**Try it fast:** `samples/sample_resume_jordan.txt` is a fictional student resume you can upload in Mentor Match. `samples/injected_resume.txt` contains a hidden prompt injection, for watching the defenses work.

Without any API key, the Directory, Calendar, and Admin pages still work, and Mentor Match says it is offline.

## How it works

```
Browser (frontend/: HTML + JS, no build step)
   │
FastAPI (backend/main.py)
   ├─ safety.preflight ─ length caps · per-session rate limit · turn cap · daily token budget
   ├─ safety.screen ──── strips instruction-like lines from resumes and messages (free, deterministic)
   │
   ├─ Mentor Match Profile Reader (Haiku 5.5 / gpt-6-luna)
   │     resume + intro → InternProfile (skills, interests, hobbies, background)
   │     flags hidden instructions it notices → injection_detected
   │
   └─ Mentor Matcher (Sonnet 5.5 / gpt-6-luna)
         cached instructions = program context + compact roster of all 64 members
         tools: get_bio(slug) · search_directory(query, sector) · upcoming_talks()
         output: MatchReply (reply, recommendations[], suggested_event_ids[])
         output validator: every slug and event id must exist, or the model retries
```

- **Data.** `data/think_tank_raw.json` contains the bios as published at cybersecuritysummit.org/2026-think-tank. `data/think_tank.json` adds structured facts (title, sector, expertise, background, interests). `scripts/enrich_bios.py` extracted them once with Haiku: 64 calls, $0.023 total.
- **Storage.** SQLite at `data/app.db` (gitignored), created on first run with three sample talks marked "Sample". It holds intern accounts, saved resumes, events, RSVPs, announcements, the access code, and token/safety logs.
  - **Resumes are saved to the intern's account**, so they don't have to upload again and Mentor Match picks up where they left off on any device. What's saved: the resume text (with instruction-like lines already removed, capped at 15,000 characters), the file name and date, and the profile Mentor Match built from it.
  - Only the intern and the Program Lead can see a saved resume (Admin → intern → **View resume**). The intern can delete it any time with **Remove** in Mentor Match.
  - The committee Fit Extractor uses the resume's skills and background when placing interns.
  - **Chat conversations are not saved.** They live in server memory for 3 hours.
- **Cost controls.** The roster sits in the Mentor Matcher's instructions, which never change between requests, so the provider caches them (`cache=True`). Thinking effort is `low`. The cheap model reads resumes, so the expensive model never sees raw resume text. Every reply shows its own token counts and cost.

## Admin agents

Both agents are on **Admin → Placements** and **Admin → Cohort Pulse**. To try them without real data, go to **Admin → Interns** and choose **Load demo cohort**. It loads 8 fictional interns, 3 weeks of Slack activity, and meeting notes, all marked "Sample", hidden from real interns, and removable in one click.

### Committee & Cyber Coach Matchmaker (`backend/placements.py`)

The work is split across three agents. The code, not a model, decides who runs next.

| Agent | Model | Does |
|---|---|---|
| Fit Extractor | Haiku 5.5, one call per intern | Profile → `InternFit` (skills, domains, strengths, experience level, committee preferences, times they can't meet). Cached by profile hash, so unchanged interns cost nothing on re-runs. |
| Placement Matcher | Sonnet 5.5, one call per cohort | Fit profiles + committees (needs, capacity, meeting time) + the Cyber Coach pool → `AssignmentMatrix`: top 3 committees per intern with scores, the assignment, a coach, and one-sentence rationales and flags. |
| Intro Drafter | Haiku 5.5, on demand | Approved placement → `IntroDrafts`: an email and a Slack DM to the intern, and the same to the committee director. These are drafts only and are never sent automatically. |

Hard rules live in code, not in the prompt. Before the matcher runs, the code works out each intern's schedule conflicts. An output validator then rejects any matrix that does any of the following, and the model has to fix it:
- breaks a committee or coach capacity
- puts an intern in a committee whose meeting they can't attend
- names a committee or coach that doesn't exist
- skips or duplicates an intern

The admin can override any assignment from the matrix and approve placements before drafting intros. Committees (capacity, needs, director, meeting day and time) and the coach pool are edited on the same screen. Interns pick up to 3 committees and describe their availability at registration or in Edit my profile.

### Cohort Pulse & Program Lead's Monday Brief (`backend/pulse.py`, `mcp_server/cohort_server.py`)

- **Data sources.** Slack comes from a live sync (set `SLACK_BOT_TOKEN`) or an uploaded Slack export `.zip`. Committee meeting notes can be pasted, or uploaded as TXT, MD, DOCX, or PDF.
- **The MCP boundary.** The Pulse Analyst (Sonnet 5.5) reads data only through the cohort MCP server's five read-only tools: `get_cohort_signals`, `list_channels`, `get_channel_messages`, `get_open_threads`, and `get_meeting_notes`. Every tool returns a privacy-safe view:
  - **PII redaction:** each person becomes a pseudonym (`Intern-03`, `Member-01`), and @mentions, emails, phone numbers, ID numbers, and links are replaced.
  - **Indirect prompt-injection guard:** messages or note lines that read like instructions to an AI are withheld and marked `[FLAGGED]`, and the prompt treats all tool output as data.
  - **Signals computed in code:** days since last post, check-ins this week and last, unanswered questions, and flagged messages. The model interprets them instead of counting.
- **Output guard.** A validator rejects any brief that contains an email, a phone number, a real name, a made-up pseudonym, or a nudge aimed at someone who isn't an intern. Only after it passes does code swap pseudonyms back to names, and only for the admin. The admin can toggle to see exactly what the AI saw.
- **Read-only.** The agent has no tool that can post to Slack or email anyone. Suggested DMs are text for the Program Lead to send.
- **Output.** A `MondayBrief`: (a) interns to nudge, with evidence and a suggested DM, (b) wins to spotlight, (c) blockers to escalate and to whom, plus watch items, safety notes, and data coverage. It is saved and downloadable as Markdown.

**Measured cost (demo cohort of 10):** the committee matchmaker costs about $0.13 for a first run and much less on re-runs (cached profiles). A Monday Brief costs about $0.03, and an intro draft about $0.001.

### Opportunities pilot (`backend/opportunities.py`, `backend/explorer.py`, `backend/cyber_domains.py`)

**Opportunity Match** (the second tab, after Mentor Match) shows internships, co-ops, and entry-level roles at Think Tank organizations, each with the Think Tank member who works there for a warm introduction. Interns get two views: the **Explorer** (ask a question, see it evaluated visually) and **Browse all roles** (filterable list). Mentor Match can also suggest openings in chat.

- **Connectors (plain code, no AI).** They read each employer's **official public job feed**, the same one its careers page uses. 25 Think Tank and Emeritus organizations, plus 4 federal ones:
  - Workday: 3M, Medtronic, the Federal Reserve, Ameriprise, Land O'Lakes, Securian, Ecolab, nVent, U.S. Bank, Center for Internet Security, Progressive Leasing, Donaldson
  - Oracle Cloud: HealthPartners, Hormel
  - Radancy: UnitedHealth Group (TalentBrew) and Edward Jones (the m-cloud job-search API its careers site calls; external postings only, one per title)
  - iCIMS: Federated Insurance, University of St. Thomas (staff jobs)
  - Greenhouse: Solera Health, Zscaler · Ashby: Flashpoint · UKG (UltiPro): Sunrise Banks
  - NEOGOV (governmentjobs.com): City of Saint Paul · SuccessFactors RSS: B. Braun (US postings only) · WordPress job API: Midwest Reliability Organization
  - USAJOBS: CISA, Air National Guard (Minnesota), Army National Guard (Minnesota), and U.S. Army, once `USAJOBS_API_KEY` and `USAJOBS_EMAIL` are set (free at developer.usajobs.gov). Agency codes come from USAJOBS's own public code list.

  The connectors wait 0.6 s between requests, send an honest User-Agent, and never get around a block or scrape LinkedIn or Indeed. Every link must point at the employer's own job host. A source's `people_orgs` maps it to the Think Tank directory names (for example Edward Jones ← "Edward Jones Information Security"), and warm intros include **Emeritus** members, labeled as such.

  **Not covered, and shown under the Explorer with the reason** (`NO_FEED_REASONS` in `opportunities.py`): Lumen (its job API returns 403, so the Hub leaves it alone), MNIT and the State Patrol (State of Minnesota jobs sit behind a CAPTCHA), Great River Energy (Infor) and the University of Minnesota (PeopleSoft), whose job boards only work in a browser session, Old Republic and Rosenblatt (LinkedIn only), and associations and small consultancies with no job feed.
- **Pre-filters (code).** Security search terms run first, so each employer's 40-detail budget goes to the most relevant roles. A title regex drops non-early-career and clearly unrelated titles (senior, manager, nursing, pharmacy, sales, clinical), and a location regex drops roles outside the US.
- **12 cybersecurity domains (`cyber_domains.py`).** There's no single official "12 domains" list, so this one merges the CISSP body of knowledge, the NIST NICE work roles, and the common Cybersecurity Domain Map: Security Operations & Incident Response, Threat Intelligence, Network, Cloud, Application & Software, Identity & Access, Data Security & Privacy, Vulnerability Management & Offensive, Endpoint/Infrastructure/OT, GRC & IT Audit, Security Architecture, and Resilience/Awareness/Physical. Each domain has the vocabulary postings actually use (Splunk, SIEM, Okta, SOX ITGC, NIST 800-53…), so a security job is recognized even when it never says "cybersecurity".
- **Job Classifier agent (Haiku 5.5 / gpt-6-luna, structured `JobClassification`).** It labels each new or changed posting with type, up to 3 cyber domains **with quoted evidence from the posting**, a 0–100 relevance score on a strict rubric, Minnesota/remote/elsewhere, term, eligibility flags, and deadline.
  - The rubric: 85+ security is the main job; 60–84 substantial security duties; 40–59 hands-on technical work with little security; below 40 analytical but far from security (economics, statistics, records, legal).
  - An output validator enforces it: no quoted evidence means no domain, and a role with no domain is capped at 59 and can't be labeled Cybersecurity. Being at a bank, regulator, or tech company is not evidence.
  - Posting text is screened for instruction-like lines first, and the classifier flags injection attempts. Results are cached by content hash plus classifier version, so a posting is paid for once (about $0.003 each).
- **What interns see.** Postings that are internship, co-op, or entry-level, score at least 40, are in the US, and aren't hidden by the Program Lead (Admin → Opportunities, which also shows each role's domains and evidence). The rubric cut the Federal Reserve from 26 shown roles to 9: its economics, regional-affairs, legal, statistics, and records internships no longer show.
- **Opportunities Explorer (Sonnet 5.5 / gpt-6-luna, structured `ExplorerAnswer`).** An intern asks a question ("Is there an entry-level SOC analyst role?"), and the page streams each step as it actually happens:
  1. Their resume and profile are mapped onto the 12 domains (code, free); the domains appear under their photo.
  2. Every employer's open roles are pre-scored in code against the question and resume; each employer's tile lights up as it's checked. Only employers with open roles sit on the ring (two interleaved rings past 16); employers whose feeds have no openings right now wait in a side rail and are still checked. Each tile carries a badge: a gold count of current Think Tank members there, or a grey E when only Emeritus members are.
  3. The 24 strongest candidates go to the agent, which ranks up to 6 with a fit score, the domain, a reason tied to resume evidence, and gaps to close (including eligibility). It says plainly when nothing is an exact match. An output validator rejects any id not in the candidate list.
  4. Matched employers glow gold with their fit; the cards below link to the employer's posting and the warm intro.

  One question costs about $0.01–0.02 and takes 6–10 seconds. Employer icons come from each employer's own website (or its own Workday career site's logo, or a preferred official file such as 3M's newsroom icon) via `scripts/fetch_org_logos.py` (pass org names to refresh just those), stored in `data/logos/` and not committed (they're trademarks); without them the tiles show colored monograms.
- **"Best fit for me" (list view).** A free, deterministic score: relevance, plus the cyber domains a role shares with the intern's resume and profile, plus word overlap, plus a bump for Minnesota or remote roles.
- **Refreshing.** Use Admin → Opportunities → **Refresh job feeds**, or run `.venv/bin/python -m scripts.refresh_opportunities` (cron-friendly, for example Mondays at 6 AM). Each refresh is capped at 400 classifications; a full re-classification of about 200 postings cost $0.59.

## Configuration

Every setting is in `backend/config.py` and can be overridden from `.env`: model names, prices, rate limit, daily token budget, per-run request/tool/token limits, and admin name/title.

## Deploying

**Render (recommended):** the repo includes a `render.yaml` Blueprint. In the Render dashboard choose **New → Blueprint**, pick this repository, and Render creates the web service from it:
- Build: `pip install -r requirements.txt`, then fetches employer logos (best effort).
- Start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'` (proxy headers so cookies and links use https behind Render's TLS).
- A 1 GB persistent disk at `/var/data` holds the SQLite database (`DB_PATH=/var/data/app.db`). Disks need a paid instance (Starter); on the free plan the database is wiped on every deploy and restart.
- `SESSION_SECRET` is generated by Render. Enter these in the dashboard when asked (never commit them): `PORTKEY_API_KEY` (or `ANTHROPIC_API_KEY`), `ADMIN_USERNAME` and `ADMIN_PASSWORD_HASH` (from the `.env` that `scripts/set_admin_password.py` wrote), and `PUBLIC_BASE_URL` (the service URL, e.g. `https://intern-summit-hub.onrender.com`). Optional: the SMTP settings for reset emails, `USAJOBS_API_KEY`/`USAJOBS_EMAIL`, `SLACK_BOT_TOKEN`.
- A new deployment starts with an empty database (plus 3 sample events). After the first deploy, sign in as admin, set the program access code, and run **Admin → Opportunities → Refresh job feeds** (about $0.60 of model calls for the first full classification).

**Moving an existing database to Render (one time):** first build a copy with no intern data, `.venv/bin/python -m scripts.export_public_db` (writes the gitignored `data/app-public.db`: no accounts, photos, resumes, RSVPs, Slack or safety-log data, and no access code; keeps talks, huddles, job postings, committees, coaches, and the anonymous usage log; it refuses to finish if any intern name, email, or phone number remains). Then set `ALLOW_DB_RESTORE=1` on the service, sign in as admin, and use **Restore a database** at the top of the Admin page to upload `data/app-public.db` (it must be a healthy Hub database; the server keeps its previous file as `app.db.before-restore`). Then remove `ALLOW_DB_RESTORE` so the upload is off again.

Any other Python host works the same way (Railway, Fly.io): the same environment variables and start command.

## Project layout

```
backend/     main.py (API) · agents.py (Mentor Match) · placements.py (committee matchmaker)
             pulse.py (Monday Brief) · opportunities.py (job feeds + classifier) · explorer.py (Opportunities Explorer)
             cyber_domains.py (12-domain vocabulary) · demo.py · auth.py · mailer.py · safety.py · db.py · config.py · prompts/
mcp_server/  cohort_server.py (read-only, privacy-safe Slack + notes tools)
frontend/    index.html · styles.css · app.js
data/        think_tank_raw.json · think_tank.json
scripts/     enrich_bios.py · set_admin_password.py
samples/     fictional resumes for testing
```

Bios and headshots belong to the Cyber Security Summit and its Think Tank members. They are shown here for the intern program's use.
