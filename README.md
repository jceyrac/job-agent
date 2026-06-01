# job_agent

Single-profile job scraping and scoring system for **Senior Product Manager** roles.

Scrapes 12+ job boards, extracts structured fields from descriptions with LLMs, scores each posting (deterministic Tier 0 + LLM Tier 1) against the active profile, and delivers a digest via email and Joplin. A multi-page Streamlit tracker UI lets you browse, filter, track applications, manage companies and contacts, and log interactions — a lightweight CRM for your job search. Runs in Docker with an optional email monitor that auto-updates application status from recruiter emails.

---

## How it works

The pipeline has two distinct phases after scraping: **extraction** (profile-independent, fills structured fields) and **evaluation** (per-profile scoring using those fields).

```
scrape.py → SQLite DB ─┬─ score.py --extract (field extraction)
                        ├─ score.py (scoring against the active profile)
                        │      └→ email digest + Joplin note
                        ├─ prepare.py --ready (application packages: queued → ready)
                        └─ tracker.py (multi-page Streamlit UI + CRM)
```

1. **`scrape.py`** fetches raw job postings from all enabled scrapers and stores only new ones in the DB (deduplication by URL).
2. **`score.py --extract`** reads jobs that haven't been extracted yet and fills structured fields (`company_country`, `industry_sector`, `language_required`, `work_mode`, `geo_zone`, `company_size`, `contract_type`, `summary`). This is profile-independent — run it once, not per profile.
3. **`score.py`** evaluates jobs against the active profile (defaults to `unified_jc`; `--profile` accepts an optional override). It auto-extracts any unextracted survivors first, then applies a two-tier scoring system:
   - **Tier 0** — deterministic filters using the extracted fields (language, sector, country, work mode mismatches are rejected with score 0).
   - **Tier 1** — LLM evaluation for jobs that pass Tier 0.
4. **`tracker.py`** is a multi-page Streamlit app (Dashboard, Jobs, Companies, Contacts, Settings) with inline company/contact links, interaction logging, and application status tracking. Doubles as a lightweight CRM.

### Model chains (Groq, with automatic fallback)

**Extraction** (`--extract`). Groq first, DeepSeek as last resort. Writes `extracted_by` to the DB to track which model filled the fields.

| Priority | Model | Provider |
|----------|-------|----------|
| 1 | `llama-3.3-70b-versatile` | Groq |
| 2 | `meta-llama/llama-4-scout-17b-16e-instruct` | Groq |
| 3 | `deepseek-v4-pro` | DeepSeek |

**Evaluation** (`--profile`). Small/cheap models only. Writes `scored_by` to the DB (`tier_0` for deterministic rejection, the model name for Tier 1).

| Priority | Model | Provider |
|----------|-------|----------|
| 1 | `llama-3.1-8b-instant` | Groq |
| 2 | `meta-llama/llama-4-scout-17b-16e-instruct` | Groq |

If a model hits its daily quota, the next one in the chain is tried automatically. Extraction falls back from Groq to DeepSeek if all Groq models are exhausted.

---

## CLI reference

### Scrape new jobs

Fetches from all enabled scrapers and adds only new postings to the DB. Safe to run daily — duplicates are silently skipped.

```bash
python scrape.py
```

### List existing profiles

Creating and listing profiles is dormant in single-profile mode — the app manages only the active profile. `create_profile.py` is kept as part of the multi-profile revert path.

```bash
python create_profile.py --list   # dormant — lists profiles but the UI only uses the active one
```

### Extract structured fields from job descriptions

Profile-independent — run once after scraping, not per profile. Fills `company_country`, `industry_sector`, `language_required`, `work_mode`, `geo_zone`, `company_size`, `contract_type`, and `summary` by reading the job description. Skips jobs already extracted (idempotent). Respects `--limit N`.

```bash
python score.py --extract
```

### Score unscored jobs

Only jobs not yet scored for the active profile are processed. Auto-extracts any unextracted survivors first. Safe to re-run after scraping new jobs. `--profile` is optional — defaults to the active profile.

```bash
python score.py
python score.py --profile <profile_id>   # optional override
```

### Cap the number of jobs processed

Limits the run to at most N jobs. The remainder are deferred to the next run — useful for staying within API rate limits.

```bash
python score.py --extract --limit 20
python score.py --limit 15
```

### Rescore all jobs (wipes existing scores)

Deletes all existing scores for the active profile and re-evaluates every job that passes the SQL pre-filter. `--profile` overrides the active profile if needed.

```bash
python score.py --rescore
python score.py --profile <profile_id> --rescore   # optional override
```

### Test the active profile's scoring context (no DB writes)

Scores 3 hardcoded sample jobs to verify the active profile's LLM instructions produce sensible results.

```bash
python score.py --mock
```

### Delete a profile

Removes the profile and all its scores from the DB. Dormant in single-profile mode. Prompts for confirmation.

```bash
python create_profile.py --delete <profile_id>
```

### Prepare applications for queued jobs

Generates a full application package: tailored cover letter, CV bullet selection/re-ordering, company research notes, and screening Q&A drafts. Picks up jobs with status `queued` (set via the tracker UI's "Queue" button) and promotes them to `ready` on success.

```bash
python prepare.py --job <job_id>                    # uses active profile
python prepare.py --job <job_id> --profile <id>     # optional override
python prepare.py --ready                           # prepare all queued jobs (queued → ready)
python prepare.py --ready --limit 5
python prepare.py --job <id> --redo                 # overwrite existing application
python prepare.py --job <id> --mock                 # dry-run: print to stdout, no writes
```

Outputs land in the `job_applications` SQLite table and as markdown files at `outputs/applications/<job_id>__<company>__<title>.md`.

Model chain: Groq primary (`llama-3.3-70b-versatile` for cover letter and screening answers, `llama-4-scout` for bullet selection and company research), with `deepseek-v4-pro` as automatic fallback if Groq daily quota is exhausted. Language matches `jobs.language_required`, defaulting to English.

### Launch the tracker UI

```bash
streamlit run tracker.py
```

---

## Search profiles

The app runs in **single-profile mode** on `unified_jc` (Unified JC). Profiles live in `profiles.py`. Each profile controls:

| Setting | Purpose |
|---------|---------|
| `allowed_geo_zones` | Post-scoring geo filter (e.g. `europe`, `global_remote`) |
| `allowed_work_modes` | Post-scoring work mode filter (e.g. `remote`, `hybrid`) |
| `location_keywords` | Pre-scoring SQL filter — only jobs matching at least one keyword in `base_location` are scored |
| `pre_filter` | Additional SQL filters: `title_contains`, `exclude_title_contains`, `location_contains`, `exclude_location_contains` |
| `score_threshold` | Minimum score to appear in the digest |
| `scoring_context` | LLM system-prompt suffix with profile-specific scoring instructions |
| `company_sizes` | Post-scoring company size filter |

### Active profile

| ID | Name | Focus |
|----|------|-------|
| `unified_jc` | Unified JC | Senior PM, Web3/fintech/AI, remote/hybrid, Switzerland + global |

### Profile mode

The app runs in single-profile mode. `UNIFIED_JC` is the only active profile. The multi-profile infrastructure (profile_id in job_scores, search_profiles table) remains as the seam for multi-user/multi-search support in a future phase.

---

## Database schema

| Table | Key | Purpose |
|-------|-----|---------|
| `jobs` | `id` (SHA-256 of URL) | Raw job postings + extracted fields. Linked to `companies` via `company_id` FK. `extracted_by` records which model filled the fields; `extracted_at` is the timestamp. |
| `job_scores` | `(job_id, profile_id)` | Per-profile scores and evaluation metadata. `scored_by` is `tier_0` for deterministic rejections or the model name for Tier 1 LLM evaluations. |
| `job_tracking` | `job_id` | Pipeline status (`new→queued→ready→applied→rejected→archived`) and notes — profile-independent. Status changes auto-log interactions for key transitions. |
| `job_applications` | `job_id` | Application analysis and cover letter — profile-independent |
| `companies` | `id` (autoincrement) | Normalized company records: name, website, country, industry sector, size, status, enrichment metadata. Status changes are logged to `company_status_history`. |
| `contacts` | `id` (autoincrement) | People at companies: name, role, email, LinkedIn, social handles, phone, verification status. Deduplicated by LinkedIn URL and email-within-company during upsert. |
| `interactions` | `id` (autoincrement) | Timeline of communications: type (outreach_sent, reply_received, interview, decision_received, note, etc.), direction, outcome, subject, body excerpt. Linked to company, contact, and optionally job. |
| `company_status_history` | `(company_id, changed_at)` | Audit log of company status transitions |
| `status_history` | `(job_id, changed_at)` | Audit log of job tracking status transitions |
| `search_profiles` | `id` | Profile definitions and criteria (JSON) |

The status of a job is **profile-independent**: marking a job "applied" in one profile view marks it applied everywhere. Scores remain per-profile since the same job can be evaluated differently under different search criteria. Extraction is profile-independent — fields are filled once and reused by all profiles.

Contacts have a **derived relationship status** computed from their interaction history (offer → interviewing → applied → replied → contacted → none), not stored as a column.

---

## Tracker UI

The multi-page Streamlit tracker (`streamlit run tracker.py`) has 5 pages:

### Dashboard
5 at-a-glance widgets: follow-ups due today, recent inbound (7 days), unverified contacts count, stale active outreach (14 days), hot jobs feed (top 10 by score). Pipeline stats bar (new / queued / ready / applied / rejected / archived).

### Jobs
Browse all jobs with filters in the sidebar (min score, status, date, location, work mode, geo zone, company size, sector, language, source, stale). Each job card shows score badge, title, company (clickable link to company detail), location, metadata, summary, and action buttons (Open, Details, Queue, Applied, Rejected, Not relevant). The detail page (accessible via `?id=`) has action buttons, status change dropdown, full description, contacts discovered from the posting, and application content preview.

### Companies
Company list with status/search filters. Each card shows job count, contact count, last interaction date, country/sector/size metadata, and current status. Detail page has 4 tabs: Jobs, Contacts, Interactions, Notes. Add contact and log interaction buttons. Status changes are logged to history.

### Contacts
Contact list with company, role family, unverified-only, and search filters. Each card shows relationship status (derived from interaction history: offer → interviewing → applied → replied → contacted). Detail page shows all channels (email, LinkedIn, X, Telegram, GitHub, phone), interactions timeline, verify/unverify toggle, and notes.

### Settings
Database stats (job/company/contact/interaction counts, contacts by role) and actions (clear cache, re-extract job fields).

### Cross-page navigation
Job cards link to company detail (`/companies?id=X`). Company detail links to individual jobs and contacts. Dashboard widgets link to relevant detail pages. All cross-page navigation uses URL query parameters for deep linking.

### Dialogs
Three `@st.dialog` modal forms: Add Company, Add Contact, Log Interaction. All clear the cache and rerun on submit.

### Legacy tracker
The original single-page tracker is preserved as `tracker_legacy.py` (`streamlit run tracker_legacy.py`).

---

## Scrapers

| Source | Method | Descriptions | Status | Notes |
|--------|--------|-------------|--------|-------|
| **LinkedIn** | `python-jobspy` | ✅ | ✅ Active | Europe-scoped, 4 queries × 20 results |
| **Indeed** | `python-jobspy` | ✅ | ✅ Active | 4 queries × 9 countries + Switzerland |
| **Greenhouse** | Public API | ✅ | ✅ Active | 30 crypto/Web3/fintech boards |
| **WeWorkRemotely** | RSS | ✅ | ✅ Active | |
| **Web3Career** | HTML scraping | ✅ | ✅ Active | Fetches individual job pages for descriptions |
| **RemoteOK** | JSON API | ✅ | ✅ Active | |
| **CryptoJobsList** | `__NEXT_DATA__` + JSON-LD | ✅ | ✅ Active | Individual pages at `/jobs/<slug>` |
| **CryptoJobs.com** | HTML scraping | ✅ | ✅ Active | Fetches individual job pages for descriptions |
| **DeFi Jobs** | HTML scraping | ✅ | ✅ Active | Falls back to crypto.jobs; JSON-LD descriptions |
| **TieTalent** | `__NEXT_DATA__` | ✅ | ✅ Active | Swiss-focused |
| **Jobup.ch** | HTML scraping | ❌ | ✅ Active | Swiss-focused; JS-rendered pages, no description available |
| **Wellfound** | RapidAPI | ✅ | ⚠️ Limited | 10 calls/month on free plan |
| **Xing** | HTML scraping | — | ❌ Disabled | JS-rendered |
| **Malt** | — | — | ❌ Disabled | JS-rendered SPA |
| **BeInCrypto Jobs** | — | — | ❌ Disabled | JS-rendered |
| **Jobs.ch** | — | — | ❌ Disabled | JS-rendered |

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/jceyrac/job-agent.git
cd job-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

| Variable | Required | Description |
|----------|----------|-------------|
| `GROQ_APIKEY` | ✅ | Groq API key — free at [console.groq.com](https://console.groq.com) |
| `GMAIL_FROM` | ✅ | Gmail address for sending the digest |
| `GMAIL_APP_PASSWORD` | ✅ | Gmail App Password — [generate here](https://myaccount.google.com/apppasswords) |
| `NOTIFY_TO` | ✅ | Destination email |
| `JOPLIN_TOKEN` | Optional | Joplin Web Clipper token |
| `X_RAPIDAPI_KEY` | Optional | RapidAPI key for Wellfound |

### 3. Typical daily workflow (local)

```bash
# 1. Fetch new jobs from all scrapers
python scrape.py

# 2. Extract structured fields from new job descriptions (once, profile-independent)
python score.py --extract

# 3. Evaluate jobs (defaults to the active profile)
python score.py

# 4. Review in the tracker UI
streamlit run tracker.py
```

### 4. Docker deployment

The project runs in Docker on an Ubuntu server. Mac is the code editor + git push.

```bash
# Build and start tracker (always-on)
docker compose up -d --build tracker

# Run agent pipeline (one-shot, via cron)
docker compose --profile manual run --rm agent

# Run email monitor (one-shot or daemon, via cron)
docker compose --profile manual run --rm email-monitor --once
docker compose --profile manual run --rm email-monitor --interval 300

# Seed Docker volume from local DB (first deploy)
bash scripts/seed-db.sh

# Deploy to server
bash scripts/deploy.sh
```

# --- Service management ---

```bash
# Check running containers
docker compose ps

# View live logs
docker compose logs -f tracker
docker compose logs -f agent
docker compose logs --tail=50 email-monitor

# Restart a service (e.g. after a config change)
docker compose restart tracker

# Stop and remove all containers (data volume is preserved)
docker compose down

# Full rebuild after code changes
docker compose up -d --build tracker
```

**Architecture:**
- `docker-compose.yml` — three services sharing a named `job_data` volume
- `docker-compose.override.yml` — Mac dev overrides (bind mount source, no auto-restart), gitignored
- `Dockerfile` — single `python:3.11-slim` image used by all services
- `email-monitor` uses `extra_hosts: host-gateway` to reach Hydroxide IMAP on the host

### 5. Email monitor

`email_monitor.py` watches Proton Mail via Hydroxide IMAP bridge, classifies unseen recruiter emails with Groq, and auto-updates job tracking status.

```bash
python email_monitor.py --dry-run          # test with 4 hardcoded emails
python email_monitor.py --once             # single IMAP pass (for cron)
python email_monitor.py --interval 300     # long-running daemon
```

Only acts on **high-confidence** classifications where exactly **1** applied job matches the extracted company name. Actions: `rejected` → status rejected, `interview_scheduled` → interviewing, `offer` → offer, `follow_up` → log-only.

### 6. Job status workflow

```
new → queued → ready → applied → rejected (by recruiter)
                               → expired (listing removed)
                               → archived (set aside by candidate)
```

Status is managed via the tracker UI action buttons or automatically via the email monitor. `rejected`, `expired`, and `archived` jobs are excluded from scoring.

---

## Running tests

```bash
python -m pytest tests/ -q
```

162 unit tests using an in-memory SQLite DB — safe to run at any time, no network calls, no DB writes. Covers schema contracts, profile-independent status/application design, scoring, digest queries, per-profile filters, company/contact CRUD, interaction logging, derived relationship status, auto-interaction hooks, dashboard data queries, badge formatting, and parameterized job filters.

```bash
python tests/run_all.py
```

Live integration tests — each scraper makes real HTTP calls. Exit code `0` if all pass or skip gracefully, `1` if any fail.

---

## Project structure

```
job_agent/
├── main.py                                # Orchestrator: scrape → score
├── scrape.py                              # Scrape all enabled sources → SQLite
├── score.py                               # Extract fields (--extract) and evaluate per profile (--profile)
├── prepare.py                             # Generate application packages from jobs in 'ready' status
├── tracker.py                             # Multi-page Streamlit UI entry point
├── tracker_legacy.py                      # Original single-page tracker (preserved)
├── tracker_views/                         # Tracker page modules
│   ├── shared.py                          # Constants, cached loaders, badges, filters, nav helpers
│   ├── dashboard.py                       # Landing page with 5 widgets
│   ├── jobs.py                            # Job list + detail view
│   ├── job_detail.py                      # Job detail: status, description, application preview
│   ├── job_helpers.py                     # Action buttons + state machine
│   ├── companies.py                       # Company list + detail view
│   ├── contacts.py                        # Contact list + detail view
│   ├── settings.py                        # Settings + stats
│   ├── preferences.py                     # Preference report
│   └── forms.py                           # @st.dialog modals (add company, add contact, log interaction)
├── create_profile.py                      # CLI: create / list / delete profiles
├── profiles.py                            # Built-in profile definitions
├── scorer.py                              # Field extraction (Groq → DeepSeek) + evaluation (Groq 8b → scout)
├── storage.py                             # SQLite persistence layer
├── models.py                              # JobPosting dataclass
├── filters.py                             # Pre-LLM filter engine
├── notifier.py                            # Email digest + Joplin export
├── email_monitor.py                       # Hydroxide IMAP → Groq classification → auto-status
├── Dockerfile                             # python:3.11-slim, shared by all services
├── docker-compose.yml                     # tracker + agent + email-monitor services
├── docker-compose.override.yml            # Mac dev overrides (gitignored)
├── .dockerignore                          # Exclude venv, data, tests, etc.
├── migrate_expired_status.py              # One-shot: reclassify archived → expired
├── migrate_single_status.py               # Migration: merged application_status → status
├── migrate_profile_independent_tracking.py # Migration: status/notes → job_tracking table
├── scripts/
│   ├── deploy.sh                          # git pull + docker compose up -d tracker
│   └── seed-db.sh                         # Seed Docker volume from local jobs.db
├── scrapers/                              # One module per job board
│   ├── base.py
│   ├── linkedin.py
│   ├── indeed.py
│   ├── _jobspy_helpers.py
│   ├── greenhouse.py
│   └── ...
├── tests/
│   ├── test_storage.py                    # Unit tests (in-memory DB)
│   └── run_all.py                         # Live scraper integration tests
├── data/jobs.db                           # SQLite database (gitignored)
└── outputs/                               # JSON digests (gitignored)
```

---

## License

MIT
