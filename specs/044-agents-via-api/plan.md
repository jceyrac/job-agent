# Implementation Plan: Agents through the API — the cv_agent stops touching the database (step 6b)

**Branch**: `044-agents-via-api` | **Date**: 2026-10-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/044-agents-via-api/spec.md`

## Summary

Roadmap step 6b. The cv_agent — which runs on the Mac — stops importing
`core.storage` / `JobStorage` / `core.scorer` / `core.paths.DB_PATH` and becomes a
pure API client: it reads the job, the profile and the score, submits pasted or URL
postings to the shared catalogue, requests a single-job `score_job` task (extract +
score) run by the spec-043 worker, records the application, and sets the job status —
all over the tailnet API with a scoped token. Everything the agent produces lands in
the production DB instead of the Mac's DEV DB.

The server side adds three things, all additive and reusing existing storage/worker
machinery: a new `ingest` scope, a new `score_job` task type (a `--job` mode of
`core.score`), and a `PUT /v1/me/jobs/{id}/application` route that calls the existing
`save_application`. No new DB table, no new scoring logic, no LLM in a request handler.

## Technical Context

**Language/Version**: Python 3.11 (union types, f-strings, dataclasses).

**Primary Dependencies**: FastAPI + pydantic (`api/`), LangGraph (`core/cv_agent/`),
`httpx` for the agent HTTP client (already a runtime dep, `httpx>=0.27.0`). No new
dependency.

**Storage**: SQLite via `JobStorage` only (server side). The agent has no DB access;
its LangGraph checkpointer (`data/cv_agent_checkpoints.sqlite`) stays local on the Mac.

**Testing**: pytest. API side via FastAPI `TestClient` (in-memory DB); agent side
against the same TestClient with a mocked LLM (FR-009). Guard tests: the API allow-list
(`tests/test_api_guard.py`) and the agent boundary (`tests/test_cv_agent_boundary.py`).

**Target Platform**: server = Linux/Docker (verva) for API + worker; the agent =
macOS (MacBook) CLI.

**Project Type**: monorepo — FastAPI service + LangGraph CLI client.

**Performance Goals**: `POST /v1/jobs` and `PUT …/application` are synchronous writes
(no LLM, <1 s); `score_job` runs off-request in the worker (timeout 10 min).

**Constraints**: no LLM call in a request handler; no DB access from the agent;
scoring stays server-side (Constitution IV, VII, X). Token never logged.

**Scale/Scope**: one user, one profile; a handful of agent runs per day.

## Constitution Check

*GATE: pass — no violations. Re-checked after design (below).*

- **I (scorer = seul juge)** — `score_job` runs the existing `extract_one` +
  `score_one` path server-side; no client writes a score. `POST /v1/jobs` writes
  only the raw posting (`save_unscored`), never extracted/scored fields. ✅
- **IV (structure déterministe)** — no new structured field; scoring unchanged
  (Tier 0 deterministic + Tier 1 LLM). ✅
- **V (chirurgical)** — minimal diffs; the only `core/storage.py` changes are the
  `ingest` scope value and `score_job` enqueue validation. `core/scorer.py` is
  untouched. ✅
- **VII (moindre privilège)** — new `ingest` scope limits job submission; the
  agent token is `read:me, write:me, tasks:run, ingest`; token read from env/file,
  never logged or committed. ✅
- **X (catalogue / espace utilisateur)** — `POST /v1/jobs` → shared catalogue
  (`ingest`); application + status → user space (`write:me`). ✅
- **XI (migration progressive)** — additive on the server; the agent is a single
  client that switches from `JobStorage` to the API in one step, no parallel
  system. The tracker / DEV path is unchanged. ✅
- **Architecture Constraints** — "agents are API clients" is now realised; long
  work is in the worker, not the request. ✅

## Project Structure

### Documentation (this feature)

```text
specs/044-agents-via-api/
├── plan.md              # this file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── agents-api.md    # ingest + application + score_job contract
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
api/
├── routers/
│   ├── catalogue.py     # + POST /jobs (ingest) — save_unscored if new
│   ├── me.py            # + PUT /jobs/{job_id}/application (write:me)
│   └── tasks.py         # unchanged — score_job rides the generic enqueue
├── schemas.py           # + JobCreate, JobCreated, ApplicationUpdate, ScoreJobParams
├── deps.py              # + ingest = require_scope("ingest")
└── main.py              # unchanged (routers already registered)

core/
├── tasks.py             # + score_job in TASK_TYPES / TASK_TIMEOUTS / command_for
├── score.py             # + --job <id> (single-job extract-if-needed + score)
├── storage.py           # + ingest scope; + score_job enqueue validation
└── cv_agent/
    ├── api_client.py    # NEW — HTTP client (base URL, token, timeouts, errors)
    ├── nodes.py         # JobStorage/scorer → api_client
    ├── cli.py           # JobStorage → api_client; profile resolution via API
    └── (graph.py / state.py unchanged in shape)

scripts/api_token.py     # + ingest in --scopes help (cosmetic)

tests/
├── test_api_guard.py    # allow-list: + save_unscored, save_application
├── test_cv_agent_boundary.py  # tighten allow-list + name-level forbids
├── test_api_contract.py / test_api_auth.py  # + ingest/application/score_job cases
├── test_cv_agent_*.py   # re-point at the FastAPI TestClient
└── test_api_openapi.py  # snapshot regenerated
```

**Structure Decision**: server additions live in the existing `api/` routers and
`core/` modules (single source of truth); the agent's only new module is
`core/cv_agent/api_client.py`. No new top-level package.

## Complexity Tracking

None — no constitution violation to justify.
