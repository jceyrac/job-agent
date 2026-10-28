# Feature Specification: Ingestion through the API — scrapers stop touching the database (roadmap step 7)

**Feature Branch**: `045-ingestion-via-api`

**Created**: 2026-10-27

**Status**: Clarified — ready for `/speckit.plan` (Q1–Q4 resolved, see Clarifications)

**Input**: Roadmap `docs/roadmap-api.md`, step 7 ("Ingestion via API", kept by decision 2026-10-21). Constitution: I (wide net; the ingestion scope is a platform parameter, never derived from a profile), VIII (scrapers know only their output contract; "à terme" no DB access except through the ingestion endpoint), X (shared catalogue vs user space), XI (progressive migration, flags, day-one identical), VII (least privilege). Lessons recorded after the 2026-10-27 hotfix: `BEGIN IMMEDIATE` for any read-then-write transaction; housekeeping never blocks ingestion. "Scrapers become pure functions (targets in, postings out). The scrape orchestrator gets its plan from the API and sends what it collected to the API, which is the only writer of the catalogue for ingestion. A flag keeps the current direct path until the new one is proven identical."

---

## Context (verified against source at `fde33a3`, 2026-10-27)

### Database access during a scrape today

**Scrapers** (`core/scrapers/**`, `BaseScraper(storage, targets)`):

| Scraper | DB access | Nature |
|---|---|---|
| `base.py` | `get_config("<source>.enabled")` in `is_enabled()`; `set_config(... .enabled = false, .disabled_reason)` in `disable()` (auto-disable after repeated timeouts / IP block) | platform config |
| `boards/linkedin.py:47` | `load_active_profile(self._storage).effective_search_locations()` → search locations | **profile-derived ingestion parameter** |
| `boards/indeed.py:62` | same → Indeed countries | **profile-derived ingestion parameter** |
| `greenhouse.py:139–152` | `get_ingestion_scope()["title_families"]`; broad path: `load_active_profile(...).greenhouse_boards`, else `get_greenhouse_boards(db)` | scope (ok) + **profile-derived board list** |
| `ats/lever.py:61`, `ats/workable.py:47` | `get_lever_slugs(db)`, `get_workable_slugs(db)` (company slugs) | catalogue reads |

**Orchestrator** (`core/scrape.py`): `JobStorage(DB_PATH)`; `get_ingestion_scope`, `get_freshness_days`, `get_config("monitoring.ats.<provider>.enabled")`, `get_monitored_companies` (reads); `dedupe_against_db` → `get_engaged_job_keys` + `touch_many`; `upsert_company`, `save_unscored`, `log_run`, `get_stats` (writes / summary).

### Two findings that go beyond moving calls

1. **Constitution I gap left by spec 040.** LinkedIn and Indeed take their **search locations** from the active profile, and the broad Greenhouse path takes its **board list** from the profile. Spec 040's guard only banned `job_titles`, `title_exclude` and `scrape_remote_or_hybrid`. What the catalogue collects still depends on one user's profile.
2. **Ingestion dedup reads the user space.** `dedupe_against_db` drops scraped postings whose normalised (title, company) matches a job the user has engaged with (`applied`, `ready`, `queued`, `archived`) and touches that job's `last_seen`. That is a user-space read deciding what enters the shared catalogue (Constitution X).

### What exists to build on

API v1 (specs 041–044): scoped tokens, `ingest` scope and `POST /v1/jobs` (single posting, spec 044), task queue + worker, central logging, staging with tracker + API + worker, `BEGIN IMMEDIATE` guidance. Scraping runs in the `agent` container (cron, `core.main`) and in the `worker` (tasks `scrape`, `scrape_monitored`), both on the compose network where the API is `http://api:8601`.

### Out of this step

Extraction, scoring, purge and the combined `runs` row of `core.main` keep their direct `JobStorage` access (non-goal).

---

## Clarifications

### Session 2026-10-28

- Q: Engaged-job dedup — union of all users' engaged jobs vs whole catalogue? → A: Union of all users' engaged jobs; keep today's behaviour exactly, server-side (day-one identical).
- Q: Search locations + Greenhouse boards — platform scope or profile-derived? → A: Move into the platform ingestion scope, initialised once from the current profile values.
- Q: Who owns the ingestion token? → A: A non-human platform principal holding ingest-only tokens.
- Q: Scope of "through the API"? → A: Scraping and ingestion only; extraction, scoring, purge and core.main's combined run record stay direct.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Scrapers are pure functions (Priority: P1)

As the maintainer, I want every scraper to receive everything it needs from the orchestrator (search terms, locations, board/company targets, enabled state) and return postings, with no `JobStorage` and no profile read, so that scrapers can run anywhere and the catalogue never depends on a user.

**Independent Test**: A guard test fails if any module under `core/scrapers/` imports `core.storage`, `JobStorage`, `load_active_profile`, or calls `get_config`/`set_config`; each scraper is unit-tested by passing targets and a fake HTTP layer.

**Acceptance Scenarios**:

1. **Given** `BaseScraper`, **Then** it no longer takes `storage`; `is_enabled()` is decided by the orchestrator from the plan, and auto-disable is reported back as an event the orchestrator sends to the API (US3).
2. **Given** LinkedIn/Indeed, **Then** search locations come from the platform ingestion scope, not the profile.
3. **Given** Greenhouse/Lever/Workable, **Then** their board/slug lists come from the plan (monitored targets or catalogue lists), not from storage or a profile.

---

### User Story 2 — The orchestrator gets its plan from the API (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `GET /v1/ingest/plan` (scope `ingest`), **Then** it returns everything a run needs: title families, exclude words, remote/hybrid option, search locations, freshness window, enabled sources and per-provider monitoring gates, the monitored targets (union of companies watched by any user, with ATS provider/identifier), and the catalogue board/slug lists.
2. **Given** the plan, **Then** no profile is read and no user-space data is exposed except the union of all users' engaged jobs, used for dedup.

---

### User Story 3 — The API is the single writer for ingestion (Priority: P1)

As the operator, I want collected postings sent in batches to the API, which deduplicates, upserts companies, saves new jobs and touches existing ones in short `BEGIN IMMEDIATE` transactions, so that ingestion has one writer and one set of rules.

**Acceptance Scenarios**:

1. **Given** `POST /v1/ingest/batch` (scope `ingest`) with up to N postings (body limit raised for this route, e.g. 5 MB; chunked by the orchestrator), **Then** for each posting the server applies, in order: id computation (`JobPosting.id`), the engaged-job dedup (union of all users' engaged jobs) with `last_seen` touch, company upsert, `save_unscored` if new / `last_seen` touch if existing; it returns per-batch counts (`new`, `existing`, `dropped_engaged`) and per-posting outcomes.
2. **Given** a batch, **Then** it is processed in short transactions (`BEGIN IMMEDIATE`, one per chunk), never holding the write lock across the whole run; a failing posting is reported without failing the batch.
3. **Given** `POST /v1/ingest/events` (scope `ingest`), **Then** source auto-disable (`<source>.enabled=false`, reason) and scrape-only run records (`log_run` for `scrape` / `scrape_monitored` runs) are written by the API.
4. **Given** the same batch sent twice, **Then** the second call creates nothing new (idempotent).

---

### User Story 4 — Switch with a flag, prove identity, keep a way back (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `ingest_via_api` (config key, default `off` at deploy), **Then** `off` keeps today's direct path unchanged and `on` uses plan + batch; switching needs no deploy.
2. **Given** a record/replay harness, **When** a real scrape (broad + monitored, ~600 postings) is captured once on staging to a fixture (postings only, no DB), **Then** replaying it through the direct path on DB copy A and through the API on DB copy B gives identical results: same job ids and fields, same company rows, same `last_seen` touches, same dropped-engaged set, same counts — this real-scrape replay is the gate for switching the flag on (a synthetic fixture alone is not sufficient).
3. **Given** staging, **Then** `core.main` (or a monitored-only run) with `INGEST_VIA_API=on` completes, the worker's `scrape` task works through the API, and the tracker shows the same new jobs as the replay predicted.
4. **Given** production, **Then** the flag is switched `on` after deploy, the next scheduled runs are `success` with usual volumes, and switching back to `off` is the rollback.

---

### User Story 5 — A platform identity for ingestion (Priority: P1)

**Acceptance Scenarios**:

1. **Given** the ingestion token, **Then** it carries `ingest` only, belongs to a non-human platform principal, and is provided to the `agent` and `worker` containers through the environment (`.env`, never the repo); the API base URL inside the compose network is `http://api:8601`.
2. **Given** the API is unreachable or the token invalid, **When** `ingest_via_api=on`, **Then** the scrape fails loudly (clear error, non-zero exit, visible in the cron log and health check) — no silent fallback to the direct path.

---

### Edge Cases

- Very large broad scrape (~600 postings, descriptions included): chunked batches, bounded body size, per-chunk transactions; total time comparable to today.
- A posting with missing title/company and no URL: rejected per `JobPosting.id` rules (spec 036), reported, rest of batch continues.
- Auto-disable during a run (IP block): reported via `/ingest/events`; the source is skipped on the next plan.
- Concurrent worker heartbeat and API writes: short `BEGIN IMMEDIATE` transactions, no read→write upgrade (hotfix lesson).
- Cron run while the API container restarts (deploy): with the flag on, the run fails loudly and the next run catches up; deploys stay outside cron windows.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Scrapers take no storage and read no profile; targets and enabled state are injected; guard test (US1).
- **FR-002**: Platform ingestion scope extended with `search_locations` (LinkedIn/Indeed) and `greenhouse_boards`, initialised **once** from the current profile values so day one is identical; `scripts/ingestion_scope.py` extended; the spec-040 guard extended to `effective_search_locations` and `greenhouse_boards`.
- **FR-003**: `GET /v1/ingest/plan`, `POST /v1/ingest/batch`, `POST /v1/ingest/events` (scope `ingest`), contract-tested against the direct path.
- **FR-004**: Server-side ingestion logic reuses the existing functions (`dedupe_against_db` semantics against the union of all users' engaged jobs, `upsert_company`, `save_unscored`, `touch_many`, `log_run`) — no new rules; short `BEGIN IMMEDIATE` transactions.
- **FR-005**: `ingest_via_api` flag; direct path untouched while `off`.
- **FR-006**: Record/replay harness and an identity test (US4-2), plus a real-scrape record/replay on staging (broad + monitored, ~600 postings) replayed through the direct path (DB copy A) and the API path (DB copy B) with identical results — the gate for switching `ingest_via_api` on. A synthetic fixture alone is not sufficient.
- **FR-007**: Non-human platform principal and its ingest-only token; compose env for `agent` and `worker`; no token in logs.
- **FR-008**: Isolation suite and API guard extended; OpenAPI snapshot updated.
- **FR-009**: Constitution PATCH: record that the Principle I gap (profile-derived search locations / Greenhouse boards) is now closed and that Principle VIII's "à terme no DB access" is realised for the ingestion path; `CLAUDE.md`, `docs/infrastructure.md`, roadmap updated.
- **FR-010**: Standard release flow: validate the **exact SHA** on staging (`scripts/staging.sh up <full-sha>`, never the branch name); STOP before merge for explicit go; deploy with `ingest_via_api=off`; post-deploy checks; STOP before switching; switch `on` in prod; next two scheduled runs `success` with usual volumes; rollback = switch back to `off`. Deploys outside 07:00/11:00/16:00 UTC.

### Non-goals

- Extraction, scoring, purge and `core.main`'s combined run record through the API.
- Changing what is collected (scope widening is a separate product decision).
- New sources or scraper changes beyond removing DB/profile access.
- Removing the direct path (contraction, step 10).

---

## Success Criteria *(mandatory)*

- **SC-001**: No module under `core/scrapers/` touches storage or a profile (guard green, failing on each forbidden form).
- **SC-002**: Replay identity: direct path vs API path on copies of the same DB give identical results.
- **SC-003**: Staging run with the flag on completes; worker scrape tasks work through the API.
- **SC-004**: Production with the flag on: next scheduled runs `success`, usual volumes; flag off restores the old path.

---

## Decisions (resolved 2026-10-28)

- **Q1 — Engaged-job dedup** → union of all users' engaged jobs (today's behaviour, server-side).
- **Q2 — Search locations + Greenhouse boards** → platform ingestion scope, initialised once from current profile values.
- **Q3 — Ingestion token owner** → non-human platform principal, ingest-only tokens.
- **Q4 — "Through the API" scope** → scraping and ingestion only; extraction/scoring/purge/combined run record stay direct.
