# Feature Specification: Agents through the API — the cv_agent stops touching the database (roadmap step 6b)

**Feature Branch**: `044-agents-via-api`

**Created**: 2026-10-21

**Status**: Clarified 2026-10-21 — ready for `/speckit.plan`

**Input**: Roadmap `docs/roadmap-api.md`, step 6b ("Agents via l'API", prerequisite of step 9 — the CV chat). Constitution: Architecture Constraints (agents are API clients; no long work in an HTTP request), principle IV (the scorer is the sole fit judge), VII (least privilege, scoped tokens), X (shared catalogue vs user space), XI. "The cv_agent, which runs on the Mac, reads and writes only through the API. Its results — pasted jobs, their scores, the application records — land in the production database and show up in the tracker, instead of the Mac's DEV database."

---

## Clarifications

### Session 2026-10-21

- Q1: Who extracts and scores a pasted or URL job? → A: **The server.** The agent submits the posting to the catalogue through the API, then requests a single-job **task** (extraction + scoring for that job and the user's profile) run by the worker of spec 043, and waits for it. No client ever writes a score; the scorer stays the sole judge, server-side.
- Q2: How strict is the agent boundary? → A: **No database access from the agent**, rather than "only `core.llm` and `core.paths`". Forbidden in `core/cv_agent/`: `core.storage`, `JobStorage`, `sqlite3`, the DB path (`core.paths.DB_PATH`), and `core.scorer` (scoring is server-side now). Allowed: pure modules without DB access — `core.llm`, `core.paths` (CV pipeline and output locations only), `core.models`, `core.geo_ch`, and profile **parsing** from `core.profiles` (never `load_active_profile(db)`). This supersedes the roadmap wording for step 6b.
- Q3: Which right is needed to submit a posting? → A: **A new scope `ingest`**, limited to submitting job postings to the shared catalogue; step 7 (batch ingestion by scrapers) will reuse it. The agent's token carries `read:me`, `write:me`, `tasks:run`, `ingest`.

---

## Context (verified against source at `4561801`, 2026-10-21)

### What the cv_agent does with the database today (`core/cv_agent/`)

| Agent action | Today (`JobStorage` / core) | API today |
|---|---|---|
| `--job <id>`: read the job and its score | `get_job_for_prepare`, `get_score_result` (`nodes.py:242, 250`) | ✅ `GET /v1/me/jobs/{id}` (job, scores, lifecycle, events, application) |
| Read the active profile (criteria) | `load_active_profile(db)`, `get_profile` (`nodes.py:108–128`, `cli.py:64–67`) | ✅ `GET /v1/me` (profiles with `criteria`) |
| Seed the profile if missing | `upsert_profile` (`nodes.py:126`) | not the agent's job — dropped (error instead) |
| `--paste` / `--url`: add the posting to the catalogue, extract and score it | `save_unscored`, `extract_job_fields` (LLM), `evaluate_for_profile` (LLM), `save_scored` (`nodes.py:193–222`) | ❌ missing (submission + single-job scoring task) |
| Analysis gate "skip" → archive the job | `set_status(…, "archived")` (`nodes.py:437`) | ✅ `PUT /v1/me/jobs/{id}/status` |
| After approval: record the application | `save_application(job_id, analysis, cover_letter)` (`nodes.py:724`) | ❌ missing |
| After approval: job → `ready` | `set_status(…, "ready")` (`nodes.py:726`) | ✅ `PUT /v1/me/jobs/{id}/status` |
| Output folder for CVs | `get_config("cv.output_dir")` (`nodes.py:583`) | n/a — a Mac setting, not shared data |

Imports from `core` today: `core.storage` (×2), `core.scorer`, `core.profiles` (×2, incl. `load_active_profile`), `core.paths` (`DB_PATH`, `DATA_DIR`, `CV_PIPELINE_DIR`), `core.models`, `core.geo_ch`, `core.llm`.

### Stays local on the Mac (unchanged)

Master CV (`.cv_pipeline/cv_data_master.json`), rendering (docx-js → PDF), lint, review bundle, Nextcloud publishing, the LangGraph checkpointer (`data/cv_agent_checkpoints.sqlite`, thread id = job id).

### Server-side building blocks available

API v1 read/write (specs 041–042), scoped tokens, task queue + worker + SSE (spec 043, task types `scrape`, `scrape_monitored`, `extract`, `score` — all batch), `core.score` CLI (`--profile`, `--extract`, `--rescore`, `--limit`; no single-job option yet).

### Known limitation being removed

Spec 036: "application records written by the cv_agent on the Mac stay in the local DEV DB and are not visible in the live tracker (status still updated manually in prod); resolved by roadmap step 6b."

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Submit a posting and get it scored by the server (Priority: P1)

As the cv_agent (or any client with the `ingest` scope), I want to submit a pasted or fetched posting to the shared catalogue and have it extracted and scored for my profile by the worker, so that the same scorer judges every job and no client writes scores.

**Independent Test**: Submit a posting through the API, request a single-job scoring task, wait for it: the job exists in the catalogue with the same id the agent computes today (`JobPosting.id`), its extracted fields and its score for the profile are identical to what `extract_job_fields` + `evaluate_for_profile` + `save_scored` produce on a copy of the same DB (LLM mocked in tests).

**Acceptance Scenarios**:

1. **Given** `POST /v1/jobs` (scope `ingest`) `{title, company, url?, location?, description}`, **Then** the API computes the job id exactly as `JobPosting.id` (title and company required when there is no URL — spec 036 rule), saves it with `save_unscored` if new, and returns `{job_id, created: true|false}`; an existing job is returned unchanged (`created: false`), never overwritten.
2. **Given** a new task type `score_job` `{job_id, profile_id}` (scope `tasks:run`, profile must belong to the user), **Then** the worker runs `python -u -m core.score --job <job_id> --profile <profile_id>`: extraction if the job is not extracted yet, then scoring for that profile, using exactly the existing scoring path (Tier 0 then Tier 1); no new scoring logic.
3. **Given** an identical `score_job` already queued or running, **Then** the existing task is returned (spec 043 dedup).
4. **Given** the task's events, **Then** the client can wait on SSE or poll `GET /v1/me/tasks/{id}`, then read the result with `GET /v1/me/jobs/{job_id}`.
5. **Given** `score_job` for another user's profile or an unknown job, **Then** `404` at enqueue time.

---

### User Story 2 — Record the application through the API (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `PUT /v1/me/jobs/{job_id}/application` (scope `write:me`) `{analysis, cover_letter}`, **Then** it calls `save_application(job_id, analysis, cover_letter)` with the same effect as today; the response is the stored application.
2. **Given** a job not visible to the user, **Then** `404`.
3. **Given** a contract test, **Then** the row written through the API equals the row written by `save_application` on a copy of the same DB.

---

### User Story 3 — The cv_agent is an API client (Priority: P1)

As the user, I want the cv_agent to work exactly as today from the Mac (CLI and the single-conversation review loop of spec 036), but against the API, so that everything it produces lands in production.

**Acceptance Scenarios**:

1. **Given** `JOB_AGENT_API_URL` (default: the production API on the tailnet; staging possible) and a token read from the environment or a local credentials file outside the repo (`JOB_AGENT_API_TOKEN`, never printed, never committed), **Then** every former `JobStorage` use in `core/cv_agent/` goes through a small API client module.
2. **Given** `--job <id>`, **Then** the job, its score for the profile and the profile criteria come from `GET /v1/me/jobs/{id}` and `GET /v1/me`.
3. **Given** `--paste` or a URL, **Then** the agent fetches/reads the posting locally as today, submits it (`POST /v1/jobs`), enqueues `score_job`, waits with a bounded timeout (clear message on timeout or failure), then continues with the scored job.
4. **Given** the analysis gate "skip", **Then** `PUT /v1/me/jobs/{id}/status` → `archived` with the same note as today.
5. **Given** approval, **Then** `PUT /v1/me/jobs/{id}/application`, then `PUT /v1/me/jobs/{id}/status` → `ready` with the same note as today; the application and status are visible in the production tracker.
6. **Given** the active profile does not exist on the server, **Then** the agent stops with a clear error (no seeding from the agent).
7. **Given** the output folder, **Then** it comes from a local setting (`CV_OUTPUT_DIR`, default unchanged) instead of the DB config.
8. **Given** the API is unreachable or the token lacks a scope, **Then** the agent stops before any LLM spend with an explicit message; nothing is written to a local DB.
9. **Given** the spec-036 flows (`--auto`, `--resume <thread_id> --approve|--reject`, `review.json`), **Then** they behave as today; thread id = job id is unchanged.

---

### User Story 4 — The boundary is enforced (Priority: P1)

**Acceptance Scenarios**:

1. **Given** the spec-036 boundary guard, **Then** it is tightened: `core/cv_agent/` must not import `core.storage`, `JobStorage`, `sqlite3`, `core.scorer`, nor reference `core.paths.DB_PATH`; allowed core imports are `core.llm`, `core.paths` (non-DB locations), `core.models`, `core.geo_ch`, and profile parsing from `core.profiles` (not `load_active_profile`). Negative fixture for each forbidden form.
2. **Given** the API guard (specs 041–043), **Then** the two new routes are in its allow-list and `POST /v1/jobs` may call only `save_unscored` (no scoring, no extraction in the request).

---

### User Story 5 — Validated on staging, then used for a real application (Priority: P1)

**Acceptance Scenarios**:

1. **Given** staging on the exact SHA (tracker + API + worker), and a staging token `read:me,write:me,tasks:run,ingest` used from the Mac with `JOB_AGENT_API_URL` pointing at the staging API, **Then**: a `--job` run produces a CV and, after `--approve`, the application and `ready` status appear in the staging tracker; a `--paste --auto` run submits the posting, the `score_job` task completes, the job appears scored in the staging tracker, and the flow continues to review; a "skip" archives the job in the staging tracker.
2. **Given** the deploy, **Then** standard checks (SHA via `/api/health`, worker heartbeat, tracker, next cron run); the production token for the agent is created by the user and stored on the Mac outside the repo.
3. **Given** the next real application, **Then** the user runs the agent against production and sees the application in the production tracker without manual status update.

---

### Edge Cases

- Pasting a posting that already exists in the catalogue (same id) → `created: false`; if it is already scored for the profile, the agent may skip the scoring task.
- Long scoring wait (queue busy, or cron holding the pipeline lock) → the agent shows the task state ("waiting for the scheduled pipeline") and waits up to a bounded timeout, then stops cleanly with the task id so the run can be resumed later.
- `score_job` fails (LLM error) → the agent stops with the task's error; no partial application is recorded.
- Network loss mid-run after approval → `PUT application` and `PUT status` are idempotent; re-running `--resume --approve` completes them.
- The Mac's DEV DB is no longer used by the agent; the tracker in dev mode still uses it (unchanged).

---

## Requirements *(mandatory)*

### Functional Requirements

**Server side**

- **FR-001**: `POST /v1/jobs` (scope `ingest`): id computed as `JobPosting.id`, `save_unscored` if new, `{job_id, created}`; validation (title/company required without URL, description size bound), no LLM call.
- **FR-002**: New scope `ingest` (token CLI, OpenAPI snapshot).
- **FR-003**: New task type `score_job` `{job_id, profile_id}` → `python -u -m core.score --job <id> --profile <id>` (new `--job` option of `core.score`, reusing the existing extraction and scoring functions unchanged), timeout 10 min, dedup per spec 043, ownership checked at enqueue.
- **FR-004**: `PUT /v1/me/jobs/{job_id}/application` (scope `write:me`) → `save_application`, contract-tested.
- **FR-005**: API guard allow-list and isolation suite extended to the new routes and task type.

**Agent side**

- **FR-006**: `core/cv_agent/api_client.py` (HTTP, base URL from `JOB_AGENT_API_URL`, token from `JOB_AGENT_API_TOKEN` or a local credentials file with `0600` permissions; timeouts; explicit errors on 401/403/404/5xx; the token never logged).
- **FR-007**: Every `JobStorage` use in `core/cv_agent/` replaced as in US3; profile seeding removed; `cv.output_dir` → `CV_OUTPUT_DIR`.
- **FR-008**: Boundary guard tightened as in US4.
- **FR-009**: Agent tests run against a FastAPI `TestClient` (in-memory DB) for the API side and a mocked LLM; no test touches a real server.

**Docs and release**

- **FR-010**: `CLAUDE.md` (cv_agent commands, environment variables), the cv_agent usage notes, and `docs/roadmap-api.md` (step 6b wording updated per Q2) updated; spec 036's DEV-DB limitation marked resolved.
- **FR-011**: Standard release flow with the US5 checks; staging uses the branch `staging.sh`.

### Non-goals

- The CV chat, LangGraph sessions driven by the API, SSE for the graph (step 9).
- Other agents (company researcher, monitoring agent) — the same pattern will follow later.
- Batch ingestion by scrapers (step 7, which will reuse the `ingest` scope).
- Running the cv_agent on verva.
- Changes to rendering, lint, review bundle, Nextcloud publishing or the master CV.

---

## Success Criteria *(mandatory)*

- **SC-001**: `core/cv_agent/` has no DB access (guard test green, failing on each forbidden import).
- **SC-002**: A pasted posting submitted and scored through the API ends up with the same id, extracted fields and score as the current local path (contract test, LLM mocked).
- **SC-003**: Staging: `--job`, `--paste --auto` and "skip" flows work end to end from the Mac, and their effects are visible in the staging tracker.
- **SC-004**: Production: the next real application made with the agent appears in the production tracker with status `ready`, without manual update.
