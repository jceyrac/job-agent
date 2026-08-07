# CLAUDE.md — job_agent

Instructions for Claude Code. Read this before touching any file.

---

## Project in one sentence

Python/SQLite/Streamlit pipeline that scrapes job postings from 12+ sources, scores them via LLM, and surfaces matches through a multi-page tracker dashboard. Solo project — no team conventions needed, just correctness and minimal diffs.

---

## Commands

```bash
# Run the tracker UI
streamlit run tracker.py

# Run the full pipeline (scrape + score)
python main.py

# Scrape only
python scrape.py

# Score only (active profile)
python score.py --profile <profile_id>

# Score only (rescore all)
python score.py --profile <profile_id> --rescore

# Field extraction only (profile-independent)
python score.py --extract

# Run tests
python -m pytest tests/

# Check DB
sqlite3 data/jobs.db "SELECT COUNT(*) FROM jobs"
```

---

## Architecture

```
scrape.py  →  SQLite (data/jobs.db)  →  score.py --extract  →  score.py (per-profile)
                                                                        ↓
                                                              tracker.py (Streamlit UI)
```

**Key files:**
- `models.py` — `JobPosting`, `JobFilter` dataclasses
- `storage.py` — all DB access via `JobStorage` class (WAL mode SQLite)
- `profiles.py` — `SearchProfile` dataclass + `load_active_profile(db)`
- `scorer.py` — LLM extraction + Tier 0/Tier 1 evaluation
- `scrape.py` — discovers and runs all enabled scrapers
- `score.py` — CLI entry point for extraction and scoring
- `tracker.py` — Streamlit entry point (multi-page via `st.navigation`)
- `tracker_views/` — one file per page: `dashboard.py`, `jobs.py`, `settings.py`, `preferences.py`, `companies.py`, `contacts.py`, `shared.py`, `job_helpers.py`, `forms.py`, `*_detail.py`

**Scrapers** live in `scrapers/`, discovered automatically via `scrape.discover_scrapers()`. Each extends `BaseScraper` and declares `SOURCE_NAME` and `ENABLED`.

Scrapers (boards d'agrégation) : le scope de source est une liste statique curée
à la main. NE JAMAIS le dériver de job_filter.titles par matching dynamique —
jugement de fit = scorer seul (Constitution I). Passer les titres en query à une
vraie recherche texte (hh.ru ?text=) est OK ; élaguer une liste curée ne l'est pas.

---

## NEVER modify these files

- `storage.py` — DB schema and all persistence logic. Extremely stable; breakage cascades everywhere.
- `models.py` — `JobPosting` and `JobFilter` dataclasses. Field changes require migration.
- `profiles.py` — `SearchProfile` definition and `load_active_profile()`. Touch only to add fields with defaults.
- `scrape.py` — scraper orchestration. Touch only to add/remove scraper discovery.
- `scorer.py` — LLM scoring logic. Touch only for prompt changes or new fallback models.
- `main.py` — thin orchestrator. Touch only if the pipeline sequence changes.
- Any file in `scrapers/` — unless the task is specifically about that scraper.
- `tracker_views/shared.py` — shared helpers consumed by all pages. Changes here break every page.
- `tracker_views/onboarding.py` — first-run wizard. Do not touch unless explicitly asked.
- Migration files (`migrate_*.py`) — one-shot scripts, already executed.

---

## Safe to modify

- `tracker_views/dashboard.py`, `jobs.py`, `settings.py`, `preferences.py` — UI pages
- `tracker_views/job_helpers.py` — action bar and state derivation for job cards
- `tracker_views/forms.py` — `@st.dialog` modals
- `tracker.py` — entry point, global CSS only
- `tracker_legacy.py` — legacy reference, not in production

---

## Code style

- **Surgical edits only** — change the minimum needed. No opportunistic refactors.
- **No new dependencies** — all UI is Streamlit-native. `st.html()` for targeted CSS only.
- **Preserve existing logic** — when reorganising UI, move code verbatim; don't rewrite it.
- Python 3.11: use `X | Y` union types, f-strings, dataclasses with defaults.
- No type annotations required unless the function is new and complex.
- DB access always goes through `JobStorage` methods — never raw `sqlite3` outside `storage.py`, except in one-off diagnostic blocks inside `settings.py` or `dashboard.py`.
- Session state keys follow the pattern `jobs_<name>`, `bg_<name>` etc. — don't add generic keys that could collide across pages.

---

## Infrastructure

- **Dev:** MacBook Pro M5 Pro, Python 3.11 venv at `.venv/`
- **Prod:** HPE ProLiant Ubuntu server, Docker Compose
- **Services:** `tracker` (always-on Streamlit :8501), `agent` (cron scrape+score), `email-monitor` (IMAP daemon)
- **DB:** `data/jobs.db` — SQLite WAL, gitignored, 160+ MB. Docker named volume `job_data` in prod.
- **LLM:** Groq API primary (`llama-3.3-70b-versatile` extraction, `llama-3.1-8b-instant` scoring), DeepSeek fallback.
- **Deploy:** `git push` on Mac → `git pull` + `docker compose up -d tracker` on server via `scripts/deploy.sh`.

---

## LLM / API constraints

- Groq free tier: 30 RPM, 1000 RPD on `llama-3.3-70b-versatile`.
- Retry logic in `scorer.py`: 5 exponential retries on 429 (2s → 32s). Don't add extra sleeps.
- If scoring fails after retries, return `None` — caller saves job as unscored for retry next run.
- Ollama is abandoned (Metal shader bug on M5 Pro with Ollama 0.19.0). Do not suggest it.

---

## DB schema (key tables)

```
jobs            — one row per unique job (id, title, company, url, source, location, posted_date, …)
job_scores      — one row per (job_id, profile_id) — score, reason, summary, work_mode, geo_zone, …
job_tracking    — one row per job_id — status, notes, status_changed_at (profile-independent)
applications    — cover letter + analysis per job_id
companies       — CRM company records
contacts        — CRM contact records
interactions    — outreach log
search_profiles — serialised SearchProfile JSON (id, name, criteria, scoring_context)
config          — key/value store (active_profile_id, onboarding_complete, cv.master_path, …)
pipeline_runs   — run history (ran_at, status, jobs_scraped, jobs_scored, duration_seconds)
```

**Job statuses:** `new` → `queued` → `ready` → `applied` → `rejected` / `expired` / `archived`
- `expired` = offer no longer live (external decision)
- `archived` = candidate decision (not relevant), requires a note
- `rejected` = explicit recruiter rejection

---

## Testing

Tests live in `tests/test_storage.py` (162 unit tests, in-memory DB — fast). Run before committing any change to `storage.py` or `models.py`.

Integration tests in `tests/run_all.py` hit live scrapers — only run intentionally.

---

## Specs and build files

All spec and build files live in `prompts/`. Read the relevant spec before starting any task.

Current active spec: `prompts/SPEC_tracker_redesign.md`

---

## Current focus (as of 2026-06-07)

**Tracker UI redesign** — see `prompts/SPEC_tracker_redesign.md`.

Changes in scope:
1. `tracker_views/jobs.py` — add Run controls bar at top (scrape, score, clear cache, re-extract + unscored count)
2. `tracker_views/dashboard.py` — add DB stats row (jobs, unscored, companies, contacts)
3. `tracker_views/settings.py` — remove Run section and DB stats (now on Jobs/Dashboard), add dividers
4. `tracker.py` — add global CSS tweaks (metric label size, card padding)

No new DB tables. No new dependencies. Preserve all existing logic.

<!-- SPECKIT START -->
Current feature: **Free-Work.com scraper** (`specs/025-free work scraper/`)
- Spec: `specs/025-free work scraper/025 - free-work-scraper-spec.md`
- Plan: `specs/025-free work scraper/plan.md`
- Tasks: *(not yet generated)*
<!-- SPECKIT END -->
