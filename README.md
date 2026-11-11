# job_agent

[![tests](https://github.com/jceyrac/job-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/jceyrac/job-agent/actions/workflows/tests.yml)

Personal automation agent for a Senior Product Manager job search across
**Web3 / DeFi / fintech / AI**: a wide-net job ingestion pipeline, LLM
extraction and scoring against a unified search profile, targeted company
monitoring via ATS adapters, a lightweight CRM, a CV / cover-letter agent —
all behind a **versioned, token-scoped HTTP API** (FastAPI) that the Streamlit
tracker, the background worker, the scrapers and the agents consume.

> **Status (Nov 2026)** — the API migration (`docs/roadmap-api.md`) has
> completed phases A–C: the domain lives in an installable `core/` package,
> user data is separated from the shared catalogue, the API serves reads,
> writes, long-running tasks and ingestion, and agents talk to it over HTTP.
> Phase D (new `web/` front + in-app chat with the CV agent) is next; until
> then the Streamlit tracker remains the human interface.

---

## Architecture

```
                          ┌────────────────────────────────────────────┐
  Scrapers (pure fns) ───▶│  POST /v1/ingest/batch   (scope: ingest)   │
  boards · ATS · sites    │                                            │
                          │            api/  (FastAPI, :8601)          │
  Streamlit tracker ─────▶│  /v1/jobs, /v1/companies   shared catalogue│
  (human UI, today)       │  /v1/me/...                user space      │
                          │  /v1/me/tasks  (+ SSE events)  task queue  │
  cv_agent & agents ─────▶│                                            │
  (scoped tokens, no DB)  └──────────────────┬─────────────────────────┘
                                             │  core.storage (only DB path)
  future web/ front ──────▶ same origin      ▼
                                      SQLite  data/jobs.db  (WAL)
                                             ▲
  worker (core.worker) ──── polls `tasks` ───┘  runs scrape / extract / score
  agent  (core.main)   ──── cron, nightly pipeline (ingests via the API)
```

Design principles (see `.specify/memory/constitution.md` and
`docs/roadmap-api.md`):

- **Shared catalogue vs. user space.** Ingestion, jobs, extraction and
  company facts are user-independent. Scoring, tracking, applications,
  company relationships and contacts are a *user's view* on the catalogue and
  are keyed by `user_id` (one user today; the seam for multi-user exists).
- **Modular monolith.** One API service, one router per domain (`catalogue`,
  `me`, `tasks`, `ingest`), one SQLite database, `core.storage` as the single
  DB access path (guarded by tests).
- **No LLM call inside an HTTP request.** Anything slow (scrape, extract,
  score, single-job score) is a row in `tasks`, executed by the `worker`
  container, with progress streamed over SSE.
- **Scrapers are independent by contract, not by deployment.** They are pure
  functions: they fetch the ingestion plan (`GET /v1/ingest/plan`) and post
  batches to `/v1/ingest/batch` under the `platform` identity. Ingestion scope
  is a platform setting, never derived from a user profile; the PM-title gate
  is a deterministic Tier-0 step of scoring.
- **Agents are API clients.** The cv_agent reads jobs and writes its
  application documents through the API with a scoped token; it may not
  import `core.storage`, `sqlite3` or the DB path (boundary enforced by tests).
- **Tailnet-only, scoped tokens.** The API is reachable only over Tailscale;
  tokens are hashed at rest, bound to a user and carry explicit scopes. The
  prerequisites for any future public exposure are listed in
  `docs/api-exposure.md`.

### Repository layout

```
job_agent/
├── api/                       # FastAPI service (spec 041+)
│   ├── main.py                # App factory, /health, error envelope, routers
│   ├── deps.py                # DB + user resolution, scope checks
│   ├── routers/               # catalogue · me · tasks · ingest
│   ├── schemas.py / serializers.py
│   ├── middleware.py          # request-size limit (64 KB write bodies)
│   ├── write_log.py / logging_config.py   # write audit + failed-auth logging
│   └── openapi.json           # committed contract (checked by tests)
│
├── core/                      # Installable domain package (spec 034)
│   ├── storage.py             # SQLite persistence + migrations — sole DB access path
│   ├── paths.py               # All data paths (JOB_AGENT_DATA_DIR); refuses an empty prod DB
│   ├── main.py                # Pipeline orchestrator (cron entry point)
│   ├── scrape.py              # Broad + --monitored-only scrape → ingest via API
│   ├── score.py / scorer.py   # Extraction + LLM evaluation, Tier-0 rules
│   ├── llm.py                 # Only module that knows the LLM provider (DeepSeek)
│   ├── tasks.py / worker.py   # Task queue + worker loop
│   ├── title_gate.py, filters.py, models.py, profiles.py, geo_ch.py
│   ├── ats_detection.py, company_researcher.py, job_actions.py, prepare.py
│   ├── profile_generator.py, context_tuner.py, preference_report.py
│   ├── notifier.py, export_jobs.py, seed.py
│   ├── cv_agent/              # LangGraph CV / cover-letter agent (API client)
│   └── scrapers/
│       ├── base.py            # BaseScraper — no JobStorage, pure fetch functions
│       ├── boards/            # Wide-net boards (LinkedIn, Indeed, Web3Career, Jobup, …)
│       ├── ats/               # Company-keyed ATS adapters (Ashby, Lever, Workday, …)
│       ├── greenhouse.py      # Greenhouse (broad crypto boards + monitored path)
│       └── company_sites/     # Bespoke per-company scrapers
│
├── tracker.py                 # Streamlit entry point (to be replaced by web/)
├── tracker_views/             # Jobs, job detail, companies, contacts, dashboard,
│                              # reports, preferences, onboarding, settings
├── scripts/                   # Ops: api_token, backup_db, staging.sh, deploy.sh, parity checks
├── specs/                     # SpecKit specs (spec.md / plan.md / tasks.md per feature)
├── docs/                      # roadmap-api, api-exposure, infrastructure, restore procedure
└── tests/                     # pytest — incl. architecture guards (DB access, LLM provider,
                               # cv_agent boundary, scraper purity, no subprocess in tracker)
```

---

## The API

Base URL in production: `https://<host>.<tailnet>.ts.net:8443/api` (Tailscale
`serve` strips `/api` and forwards to `127.0.0.1:8601`). Interactive docs at
`/api/docs`. All routes except `/health` are under `/v1` and require a bearer
token.

| Area | Routes | Scope |
| --- | --- | --- |
| Health | `GET /health` (version = image git SHA, purge staleness warnings) | none |
| Catalogue | `GET /v1/jobs`, `GET /v1/jobs/{id}`, `POST /v1/jobs`, `GET /v1/companies/{id}` | `read:catalogue` |
| Catalogue writes | `PATCH /v1/companies/{id}` (company facts) | `write:catalogue` |
| User reads | `GET /v1/me`, `/feed`, `/jobs/{id}`, `/stats`, `/dashboard`, `/applications`, `/companies[/{id}]`, `/contacts[/{id}]` | `read:me` |
| User writes | job status, application, lifecycle events (add / edit / delete), company relationship, contacts, interactions, active profile, profile upsert | `write:me` |
| Tasks | `POST/GET /v1/me/tasks`, `GET /v1/me/tasks/{id}`, `GET …/{id}/events` (SSE), `POST …/{id}/cancel` — types `scrape`, `scrape_monitored`, `extract`, `score`, `score_job` | `tasks:run` / `tasks:pipeline` |
| Ingestion | `GET /v1/ingest/plan`, `POST /v1/ingest/batch`, `POST /v1/ingest/events` | `ingest` |

Errors use a single envelope `{"error": "<code>", "message": "…"}` with no
stack traces or internal paths. A resource that isn't the caller's returns
404, never 403 (no existence oracle).

Tokens are managed only from the CLI — the API never mints or revokes them:

```bash
python -m scripts.api_token create --name tracker --scopes read:catalogue,read:me,write:me,tasks:run
python -m scripts.api_token create --name ingest --scopes ingest --user platform
python -m scripts.api_token list
python -m scripts.api_token revoke <id>
```

The raw token is printed once; only its SHA-256 hash is stored.

---

## How the pipeline works

```
Scrape (wide net) ──▶ /v1/ingest/batch ──▶ Extract (structured fields) ──▶ Score (per user profile) ──▶ Feed
Monitored-only scrape (ATS adapters) ──┘
```

1. **Scrape** — board scrapers cast a wide net with no relevance filtering;
   monitored-company ATS adapters pull *all* open roles of the companies you
   target. With `ingest_via_api` on (production default since spec 045),
   results are posted to the API rather than written directly.
2. **Extract** — profile-independent extraction: work mode, geo zone, company
   size, sector, country, contract type, language.
3. **Score** — per profile: deterministic Tier-0 rules (PM-title gate, company
   blacklist, country / sector / language exclusions, soft penalties), then an
   LLM evaluation (1–10 score + reasoning) via DeepSeek. A job from a monitored
   company gets a strong positive signal in the scoring context, not a score
   override.
4. **Triage** — scored jobs appear in the user feed (tracker today, `web/`
   tomorrow); status changes, lifecycle events and CRM updates go through
   `/v1/me/*`.

All scoring and filtering decisions come from one **unified search profile**
stored in the DB (`profiles.py` is a one-time bootstrap seed only), edited from
the Settings page or generated during onboarding.

---

## Data model (SQLite, `data/jobs.db`)

| Table | Space | Purpose |
| --- | --- | --- |
| `jobs` | catalogue | Raw + extracted postings, one row per job |
| `companies`, `company_status_history` | catalogue | Company registry, enrichment, ATS provider / identifier |
| `runs` | catalogue | Pipeline run log (`full`, `monitored_only`; platform runs) |
| `users` | user | Users (one today) |
| `search_profiles` | user | Profile criteria + `scoring_context`, owned by a user |
| `job_scores` | user | Score per `(job_id, profile_id)` |
| `job_tracking`, `status_history` | user | Pipeline status and its history |
| `job_applications` | user | Application lifecycle (events, dates, documents) |
| `user_companies` | user | Monitored flag, relationship status, notes |
| `contacts`, `interactions` | user | Private CRM — contacts are per user |
| `tasks`, `task_events` | system | Task queue + progress events (SSE) |
| `api_tokens` | system | Hashed, scoped, user-bound tokens with optional expiry |
| `config`, `migrations` | system | Key-value settings and flags; idempotent schema migrations |

All schema changes go through `core/storage.py`'s migration chain
(expand / contract: add and backfill first, remove old columns in a separate
step).

---

## Targeted company monitoring

Mark specific companies as **monitored**: each has a resolved ATS
(`ats_provider` + `ats_identifier`, e.g. `greenhouse` / `fireblocks`) and a
per-user `monitored` flag in `user_companies`.

- **Add a company** — paste its careers URL in Settings → "Monitored
  Companies"; `core/ats_detection.py` resolves the provider from the hostname
  (or one page fetch for vanity domains), with manual entry as a fallback.
- **Toggle monitoring** — one toggle per company (Companies list, company
  detail, Settings), with three states: not monitorable / monitorable but
  scraper disabled / monitorable and active.
- **Run it** — `python -m core.scrape --monitored-only`, or a
  `scrape_monitored` task from the tracker.
- **Title gate** — non-PM roles from monitored companies are marked
  `filtered_non_product` before any LLM call.

---

## Getting started

### 1. Prerequisites

- Python 3.11+
- A [DeepSeek](https://platform.deepseek.com) API key for extraction and
  scoring — optional if you only want scraping
- Docker + Docker Compose — optional, for server deployment

### 2. Clone and install

```bash
git clone https://github.com/jceyrac/job-agent.git
cd job-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # core is an installable package
cp .env.example .env             # then set DEEPSEEK_API_KEY
```

| Service | Variable | Required? | Notes |
| --- | --- | --- | --- |
| [DeepSeek](https://platform.deepseek.com) | `DEEPSEEK_API_KEY` | ✅ for scoring | Sole LLM backend, used only through `core/llm.py`. |
| Gmail SMTP | `GMAIL_FROM` + `GMAIL_APP_PASSWORD` | Optional | Daily email digest. |
| Any email | `NOTIFY_TO` | Optional | Digest recipient. |
| [Joplin](https://joplinapp.org) | `JOPLIN_TOKEN` | Optional | Note export via Web Clipper. |
| [RapidAPI](https://rapidapi.com) | `X_RAPIDAPI_KEY` | Optional | Wellfound scraper (10 req/month free). |

Runtime configuration: `JOB_AGENT_DATA_DIR` (data directory),
`JOB_AGENT_REQUIRE_DB=1` (refuse to start on a missing DB, set in production),
`JOB_AGENT_API_URL` (API base URL for the tracker, worker, scrapers and agents).

### 3. Run locally

```bash
uvicorn api.main:app --port 8601     # API
python -m core.worker                # task worker
streamlit run tracker.py             # tracker UI (onboarding wizard on first launch)
python -m core.main                  # one full pipeline run
```

Individual steps:

```bash
python -m core.scrape                     # broad board scrape
python -m core.scrape --monitored-only    # company-keyed monitoring scrape
python -m core.score --extract            # extraction only
python -m core.score --profile unified_jc [--rescore] [--limit N]
python -m core.score --mock --profile unified_jc
```

---

## Deployment

`docker-compose.yml` defines four services sharing the `job_data` volume:

| Service | Command | Role |
| --- | --- | --- |
| `api` | `uvicorn api.main:app --port 8601` | The API, bound to `127.0.0.1:8601`, exposed on the tailnet via `tailscale serve` (`:8443/api`) |
| `worker` | `python -m core.worker` | Executes queued tasks |
| `tracker` | `streamlit run tracker.py` | Streamlit UI on `127.0.0.1:8501` |
| `agent` | `python -m core.main` | Nightly pipeline, cron-triggered (`profiles: [manual]`) |

Inside the compose network the API is reached as `http://api:8601`. Port 443
on the host stays with nginx; staging runs on its own port.

Release flow: development on the Mac → CI must be green for the exact SHA
(spec 044c) → staging on the server against a copy of the latest backup
(`scripts/staging.sh`) → merge to `main` → `scripts/deploy.sh` on the server.
A DB backup is taken before any schema-touching deployment, and none is
scheduled at month end (status batch updates).

---

## Roadmap

From `docs/roadmap-api.md`:

| Phase | Steps | Status |
| --- | --- | --- |
| A — Foundations | 0 safety net · 1a–1d location independence, installable project, `core/` move, dead files · 2 single DB access path, Groq removal | ✅ |
| B — Data model | 3 user / catalogue separation · 4 platform ingestion scope | ✅ |
| C — API | 5 read · 6 write + task queue · 6b agents via API · 7 ingestion via API | ✅ (Oct 2026) |
| D — Front & chat | 8 `web/` front, page by page, mobile-first · 9 CV-agent chat (`cv-sessions`, SSE) | 🔜 next |
| — Contraction | 10 drop legacy columns, retire Streamlit | later |

---

## Export CSV — formulaire 716.007

`core/export_jobs.py` génère un CSV compatible avec le formulaire mensuel de
recherches d'emploi exigé par l'ORP (Office Régional de Placement).

```bash
python -m core.export_jobs                          # mois courant, statut=applied
python -m core.export_jobs --month 2026-05          # mois spécifique
python -m core.export_jobs --from 2026-05-01 --to 2026-05-31
python -m core.export_jobs --statuses applied rejected archived
```

**Output** : `data/jobs_YYYY-MM.csv` en `utf-8-sig` (BOM), ouvrable tel quel
dans Excel et LibreOffice Calc. **Colonnes** (6) : Date | Entreprise / Adresse
| Description du poste | Status | URL | ID.

La colonne `Status` exporte le statut DB brut (`new`, `queued`, `ready`,
`applied`, `rejected`, `archived`, `expired`). Dans l'onglet Reports, ces
statuts sont regroupés en « en suspens » (`applied`/`queued`/`ready`/`new`) et
« négatif » (`rejected`/`archived`/`expired`).

---

## Development

- **Spec-first** — every feature is a SpecKit spec in `specs/NNN-*/`
  (`spec.md` → `plan.md` → `tasks.md` → implementation). Any UI change needs an
  approved visual mockup first.
- **Constitution** — `.specify/memory/constitution.md` (v2.x) governs
  architecture: wide-net scrapers, scorer as sole fit judgment, DB as sole
  truth, single DB access path, tailnet-only API, surgical changes.
- **Tests** — `pytest` (in-memory SQLite) plus architecture guards; `ruff`
  with always-bug rules only. CI runs on every push and gates staging, merge
  and deploy.
- `CLAUDE.md` — runtime instructions for Claude Code.

---

## License

MIT
