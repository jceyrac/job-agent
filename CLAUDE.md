# CLAUDE.md — job_agent

Instructions for Claude Code. Read this before touching any file.
The constitution (`.specify/memory/constitution.md`, v2.1.0) takes precedence over this file.

---

## Project in one sentence

Python/SQLite pipeline that scrapes job postings from 25+ sources, extracts and scores them via LLM, and surfaces matches through a multi-page Streamlit tracker — currently migrating, step by step, to an API architecture (`docs/roadmap-api.md`). Solo project — no team conventions needed, just correctness and minimal diffs.

---

## Commands

```bash
# Install (editable, dev extras — run once in a fresh venv)
pip install -e ".[dev]"

# Run the tracker UI
streamlit run tracker.py

# Run the full pipeline (scrape + score)
python -m core.main

# Scrape only
python -m core.scrape

# Score only (active profile)
python -m core.score --profile <profile_id>

# Score only (rescore all)
python -m core.score --profile <profile_id> --rescore

# Field extraction only (profile-independent)
python -m core.score --extract

# CV + cover letter agent (LangGraph, human-in-the-loop) — reads/writes via the API
python -m core.cv_agent.cli <job_id | url>      # --paste reads a posting from stdin
python -m core.cv_agent.cli --paste --auto --title <t> --company <c>  # --resume/--approve/--reject
#   Agent env: JOB_AGENT_API_URL (API base; default https://verva.taild1340b.ts.net:8443/api),
#   JOB_AGENT_API_TOKEN (scoped bearer; else ~/.config/job_agent/agent_token, mode 0600),
#   CV_OUTPUT_DIR (render root), CV_AGENT_CHECKPOINT_DB (checkpointer sqlite),
#   CV_PUBLISH=off / CV_PUBLISH_DIR (Nextcloud).
#   Staging run (isolates the agent from prod output/checkpointer/Nextcloud):
#     export JOB_AGENT_API_URL=https://verva.taild1340b.ts.net:10000/api   # staging API
#     export JOB_AGENT_API_TOKEN=<staging-token>                      # read:me,write:me,tasks:run,ingest
#     export CV_OUTPUT_DIR=/tmp/cv_staging_outputs
#     export CV_AGENT_CHECKPOINT_DB=/tmp/cv_staging_checkpoints.sqlite
#     export CV_PUBLISH=off           # or CV_PUBLISH_DIR=Documents/00 Staging to a scratch folder

# Serve the read-only API locally (FastAPI, loopback)
uvicorn api.main:app --host 127.0.0.1 --port 8601

# API tokens (create / list / revoke) — scopes: read:catalogue, read:me,
# write:me, write:catalogue, tasks:run, tasks:pipeline, ingest
python -m scripts.api_token create --name <client> --scopes read:me [--expires-in <days>]
python -m scripts.api_token list [--user <id>] [--json]
python -m scripts.api_token revoke <token-id>

# Lint (E9/F63/F7/F82 — undefined names, syntax, statement errors)
ruff check .

# Run tests
python -m pytest tests/

# Check DB
sqlite3 data/jobs.db "SELECT COUNT(*) FROM jobs"
```

---

## Architecture

**Current:**

```
core/scrape.py  →  SQLite (data/jobs.db)  →  core/score.py --extract  →  core/score.py (per-profile)
                                                                                  ↓
                                                                        tracker.py (Streamlit UI)
```

**Target** (see `docs/roadmap-api.md` and constitution principles X–XI): monorepo with
`core/` (domain), `api/` (FastAPI, single DB writer), `web/` (new front), `tracker/` (Streamlit).
Shared catalogue (ingestion, jobs, extraction, company facts — no user) vs user space
(profile, scores, tracking, applications, `user_companies`, private contacts — keyed by `user_id`).
Migration is progressive: the app MUST stay operational after every step.

**Key files:**
- `core/models.py` — `JobPosting`, `JobFilter` dataclasses
- `core/storage.py` — all DB access via `JobStorage` class (WAL mode SQLite)
- `core/profiles.py` — `SearchProfile` dataclass + `load_active_profile(db)`
- `core/llm.py` — unified LLM client; the only file allowed to know the provider
- `core/scorer.py` — LLM extraction + Tier 0/Tier 1 evaluation
- `core/scrape.py` — discovers and runs all enabled scrapers
- `core/score.py` — CLI entry point for extraction and scoring
- `core/job_actions.py` — per-job actions (`extract_one`, `score_one`, `prepare_one`)
- `core/tasks.py` — task-type allow-list, command map, timeouts, `pipeline_lock` (flock shared by cron + worker)
- `core/worker.py` — single-consumer loop: claims one task, streams output to `task_events`, heartbeat
- `core/cv_agent/` — LangGraph CV/letter agent (`graph.py`, `nodes.py`, `state.py`, `cli.py`); HITL via `interrupt()` + `SqliteSaver` checkpointer
- `core/company_researcher.py` — company enrichment / ATS detection
- `scripts/ingestion_scope.py` — view/edit the platform ingestion scope (`config.ingestion_scope`; the prose path, no deploy)
- `tracker.py` — Streamlit entry point (multi-page via `st.navigation`)
- `tracker_views/` — one file per page: `dashboard.py`, `jobs.py`, `settings.py`, `preferences.py`, `companies.py`, `contacts.py`, `reports.py`, `shared.py`, `job_helpers.py`, `forms.py`, `*_detail.py`
- `core/monitoring_agent.py` — dev-side tool (writes specs and scraper stubs). Never runs in prod, never exposed by the API.

**Scrapers** live in `core/scrapers/` (`boards/`, `ats/`, `company_sites/`), discovered automatically via `core.scrape.discover_scrapers()`. Each extends `BaseScraper` and declares `SOURCE_NAME` and `ENABLED`.

Ingestion scope is a **platform parameter**, never derived from a profile (Constitution I).
Scrapers (boards d'agrégation) : le scope de source est une liste statique curée
à la main. NE JAMAIS le dériver de job_filter.titles par matching dynamique —
jugement de fit = scorer seul. Passer les titres en query à une vraie recherche
texte (hh.ru ?text=) est OK ; élaguer une liste curée ne l'est pas.

---

## NEVER modify these files

Unless the task — or the current roadmap step's spec — explicitly concerns them:

- `core/storage.py` — DB schema and all persistence logic. Extremely stable; breakage cascades everywhere.
- `core/models.py` — `JobPosting` and `JobFilter` dataclasses. Field changes require migration.
- `core/profiles.py` — `SearchProfile` definition and `load_active_profile()`. Touch only to add fields with defaults.
- `core/llm.py` — unified LLM client. Touch only for provider/retry changes.
- `core/scrape.py` — scraper orchestration. Touch only to add/remove scraper discovery.
- `core/scorer.py` — LLM scoring logic. Touch only for prompt changes.
- `core/main.py` — thin orchestrator. Touch only if the pipeline sequence changes.
- Any file in `core/scrapers/` — unless the task is specifically about that scraper.
- `tracker_views/shared.py` — shared helpers consumed by all pages. Changes here break every page.
- `tracker_views/onboarding.py` — first-run wizard. Do not touch unless explicitly asked.

---

## Safe to modify

- `tracker_views/dashboard.py`, `jobs.py`, `settings.py`, `preferences.py`, `reports.py` — UI pages
- `tracker_views/job_helpers.py` — action bar and state derivation for job cards
- `tracker_views/forms.py` — `@st.dialog` modals
- `tracker.py` — entry point, global CSS only

Any spec touching the tracker UI (or the future `web/` front) requires a mockup approved by Jean Claude before implementation.

---

## Code style

- **Surgical edits only** — change the minimum needed. No opportunistic refactors. Roadmap steps are planned refactors, each with its own spec and non-goals.
- **Dependencies** — Streamlit UI stays Streamlit-native (`st.html()` for targeted CSS only). FastAPI, uvicorn and pydantic are allowed for `api/`. Any other new dependency must be justified in its spec.
- **Preserve existing logic** — when reorganising, move code verbatim; don't rewrite it.
- Python 3.11: use `X | Y` union types, f-strings, dataclasses with defaults.
- No type annotations required unless the function is new and complex.
- DB access always goes through `JobStorage` methods — never raw `sqlite3` outside `core/storage.py`.
- Any read-then-write transaction must use `BEGIN IMMEDIATE` (write lock up front). A deferred `BEGIN` that upgrades to a write after reads fails with `SQLITE_BUSY` in WAL under the always-on worker's commits; `busy_timeout` cannot resolve a stale-snapshot upgrade.
- Housekeeping must never block ingestion — in `core.main`, a failed purge/cleanup is logged and skipped (the pipeline continues to scrape/score), never raised.
- No file other than `core/llm.py` names an LLM provider. Never put LLM calls or long-running work inside an HTTP request handler (use the `tasks` table + worker, or a streamed session).
- No user-dependent data on catalogue entities (`jobs`, `companies`); no user-independent logic keyed by profile (Constitution X).
- Session state keys follow the pattern `jobs_<name>`, `bg_<name>` etc. — don't add generic keys that could collide across pages.

---

## Infrastructure

- **Dev:** MacBook Pro M5 Pro, Python 3.11 venv at `.venv/`, repo at `/Users/jeanclaudevd/AI-Suite/job_agent/`. All development happens here.
- **Prod (verva):** HPE ProLiant Ubuntu server, Docker Compose, repo at `/opt/job-agent`. Deploy-only — never develop or write specs there. SSH access is available for live diagnostics.
- **Services:** `tracker` (always-on Streamlit :8501, Tailscale), `api` (always-on FastAPI :8601, Tailscale HTTPS under `/api`), `worker` (task-queue consumer, `python -m core.worker`, no network exposure), `agent` (cron scrape+score).
- **Network:** the production tracker is bound to loopback (`127.0.0.1:8501`) in compose and reached only over the tailnet via `tailscale serve`; the API is bound to `127.0.0.1:8601` and served as `https://<verva>.<tailnet>.ts.net:8443/api` via `tailscale serve --bg --https=8443 --set-path /api http://127.0.0.1:8601` (app sets `root_path="/api"`) — see `docs/infrastructure.md`. **Port 443 belongs to nginx (Nextcloud); `tailscale serve` must never use it.** Staging uses `:10000/api`. Clients read the API base URL from `JOB_AGENT_API_URL` (the cv_agent defaults to the prod `:8443/api` URL).
- **DB:** `data/jobs.db` — SQLite WAL, gitignored, 160+ MB. Docker named volume `job_data` in prod. Live data lives on verva; the local Mac DB is typically empty.
- **Locations:** `core/paths.py` is the single source of truth for every path (`PROJECT_ROOT`, `DATA_DIR`, `DB_PATH`, `OUTPUT_DIR`, `ENV_PATH`, `data_path()`). Env overrides: `JOB_AGENT_DATA_DIR` (data dir), `JOB_AGENT_OUTPUT_DIR` (outputs dir); `JOB_AGENT_REQUIRE_DB=1` makes `import core.paths` refuse startup (no dir/DB created) if the DB is missing. Rule: locations come from `core/paths.py` only — never derive `data`/`outputs`/`.env` from `__file__` or a CWD-relative `"data/…"` literal; scripts are launched as modules (`-m <module>`, e.g. `python -m core.scrape`), never by `.py` filename.
- **LLM:** DeepSeek only (`deepseek-chat`), configured in `.env` (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`) and called exclusively through `llm.call()`.
- **Deploy:** `git push` on Mac → `scripts/deploy.sh` on server (git pull, rebuild all images, restart).
- **Release process** (every step — branch → staging → ff-only merge → deploy → SHA check): work on a spec branch; validate the **exact SHA** on staging (`scripts/staging.sh up <full-sha>`, never the branch name); `git merge --ff-only <full-sha>` onto `main` (refuse non-ff); `./scripts/deploy.sh` on verva; verify `tailscale serve status` still lists `tcp 8501` and `https 8443 /api` (persistent config — no re-run needed); confirm `git -C /opt/job-agent rev-parse HEAD` equals the merged SHA. See `docs/migration-checklist.md`.

---

## LLM / API constraints

- All calls go through `llm.call()`: up to 3 retries with exponential backoff on 429/5xx/network (5s → 10s → 20s), plus a short `sleep_after` between calls. Don't add extra sleeps or retry loops elsewhere.
- If extraction or scoring fails after retries, return `None` — the caller saves the job as unscored for retry next run.
- The LLM produces prose only; structured fields used for control flow are derived deterministically (Constitution IV).
- Local Ollama models are not part of the pipeline.

---

## API (v1, read + write)

- `api/` is a FastAPI app over `core`, tailnet-only, auth with scoped bearer
  tokens (`read:catalogue`, `read:me`, `write:me`, `write:catalogue`,
  `tasks:run`, `tasks:pipeline`) bound to a user. Tokens are managed with
  `python -m scripts.api_token` (SHA-256 hashes only, raw token printed once).
- Endpoints are versioned under `/v1` and grouped one router per domain
  (`api/routers/catalogue.py`, `api/routers/me.py`); `/health` is outside `/v1`
  and unauthenticated. Each payload equals its `JobStorage` source field-for-field
  (pydantic `extra="forbid"`).
- **Writes (spec 042)** are synchronous, scoped, and mirror the tracker's
  `JobStorage` calls with no new storage behaviour:
  - `write:me` — job status + lifecycle events (`PUT /v1/me/jobs/{id}/status`,
    `POST …/events`, `PATCH/DELETE …/events/{event_id}`); company relationship
    (`PATCH /v1/me/companies/{id}`); contacts (`POST/PATCH /v1/me/contacts…`);
    interactions; active-profile switch and profile criteria edit
    (`PUT /v1/me/active-profile`, `PUT /v1/me/profiles/{id}` — no rescoring).
  - `write:catalogue` — shared company facts only (`PATCH /v1/companies/{id}`),
    fact fields (`name, website, company_country, industry_sector, company_size,
    x_handle, careers_url, ats_provider, ats_identifier, scraping_method,
    research_notes`); user-space `notes`/`status`/`monitored` are rejected.
  - Writes are capped at 64 KB (`413`), validated by explicit models
    (`extra="forbid"` → `422`), and emit one structured log line
    (`api/write_log.py` — token id, user id, route, resource id, status; never
    the body or token value).
- **Tasks (spec 043)** — long work is requested, not run, by clients. A `POST
  /v1/me/tasks` (`{type, params}`) validates and enqueues (or returns the
  identical active task) and answers `202`; the `worker` container claims and
  runs it as the same module command as today. `GET /v1/me/tasks`,
  `GET …/tasks/{id}`, `GET …/tasks/{id}/events` (SSE `Accept:
  text/event-stream` with `Last-Event-ID` resume + JSON fallback) and
  `POST …/tasks/{id}/cancel` complete the surface. Scopes: `extract`/`score`
  need `tasks:run`; `scrape`/`scrape_monitored` need `tasks:run` +
  `tasks:pipeline`; reads need `read:me`. No LLM/subprocess in a handler (guard
  test).
- **No SQL in `api/`** — all DB access goes through `JobStorage`; a guard test
  fails on any non-allow-listed method (read + write set), a `sqlite3` import,
  or an unbound `JobStorage` outside `api/deps.py`'s auth path. One `JobStorage`
  per request (unbound for auth, bound to the token's `user_id` for data). No
  LLM/long work in a handler.
- Scores are user-scoped in `JobStorage` (`get_user_scores_for_job`) — the API
  never calls the cross-user `get_scores_for_job` (spec 039 rule).
- Future public exposure is a deliberate separate step: see `docs/api-exposure.md`.

---

## DB schema (key tables)

```
jobs                — one row per unique job + extracted fields (summary, work_mode, geo_zone, company_size, contract_type, …) — SHARED catalogue
job_scores          — one row per (job_id, profile_id) — score, reason, country_code, comp_flag, scored_at — shared catalogue
users               — one row per user (id, display_name, active_profile_id, onboarding_completed_at)
user_companies      — one row per (user_id, company_id) — status, notes, monitored, watch_requested_at
job_tracking        — one row per (user_id, job_id) — status, notes, changed_at
status_history      — status changes per (user_id, job) (lifecycle dates)
job_applications    — analysis, cover letter, CV bullets per (user_id, job_id)
companies           — company records (facts + monitoring/ATS fields) — SHARED catalogue
company_status_history — company status changes (keyed by user_id)
contacts            — CRM contact records (keyed by user_id; UNIQUE (user_id, linkedin_url))
interactions        — outreach log (keyed by user_id)
search_profiles     — serialised SearchProfile JSON (id, user_id, name, criteria)
config              — key/value store (platform config; active_profile_id/onboarding_complete moved to `users`; `ingestion_scope` = the platform title scope, edited via `python -m scripts.ingestion_scope`)
runs                — run history (ran_at, status, jobs_scraped, jobs_scored, duration_seconds, run_type)
```

Step 3 (séparation des espaces) is done (2026-10-13): `users` + `user_companies` + `user_id`
on tracking/history/applications/contacts/interactions/company_status_history/search_profiles.
`companies.status`/`notes`/`monitored` are kept but unused (expand/contract — removed at step 10).
`JobStorage(db_path, user_id=…)` binds the user seam; `user_id=None` → `config.default_user_id`.

**Job statuses:** `new`, `queued`, `ready`, `applied`, `interviewing`, `offer`, `rejected`, `withdrawn`, `archived`, `expired`
- `expired` = offer no longer live (external decision)
- `archived` = candidate decision (not relevant), requires a note
- `rejected` = explicit recruiter rejection

Lifecycle dates are set manually (statuses are often updated in batch at month end), never from the click date.

---

## Testing

`ruff check .` and `python -m pytest tests/` must be green before every commit that touches Python code (constitution v2.1.0, Development Workflow). The suite covers storage (in-memory DB), the API contract/auth/isolation, the constitution guards, and scrapers on fixtures. The Claude Code Stop hook runs the suite automatically when a turn changed Python and **blocks once on a red suite** — fix the failure, don't weaken the hook.

Every bug fix ships a regression test that fails before the fix (unless the bug is only observable against live sources, then a known-case check per Principle VI).

Integration tests in `tests/run_all.py` hit live scrapers — only run intentionally.

Roadmap steps also require: next nightly cron OK, tracker smoke test (feed, status change, job detail), and the parity script green (Constitution XI).

---

## Specs

All new work is specified with SpecKit in `specs/0XX-feature-name/` (`spec.md` → `/speckit.clarify` → `/speckit.plan` → `/speckit.tasks` → `/speckit.analyze` → `/speckit.implement`). Read the relevant spec before starting any task.

`prompts/` holds legacy BUILD_*/SPEC_* files — historical reference only, never a source of current requirements.

---

## Current focus (as of 2026-10-27)

**API migration roadmap** — `docs/roadmap-api.md`. Steps 3–6b shipped (039 user-space
separation → 040 platform ingestion scope → 041 read API → 042 write API → 043 task
queue + worker + SSE → 044 agents via API), all in prod. Current focus: **step 7 —
Ingestion via API** (`core/scrape.py` → `/ingest/batch`). The nightly cron
"database is locked" hotfix (purge `BEGIN IMMEDIATE`, housekeeping continues on
failure) shipped 2026-10-27 (`cd5c107`).

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
at specs/045-ingestion-via-api/plan.md
<!-- SPECKIT END -->
