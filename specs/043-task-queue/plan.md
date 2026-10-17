# Implementation Plan: Task Queue, Worker and Live Progress

**Branch**: `043-task-queue` | **Date**: 2026-10-17 | **Spec**: `specs/043-task-queue/spec.md`

**Input**: Feature specification from `specs/043-task-queue/spec.md` (roadmap step 6, part 2 of 2 — absorbs former step 2b).

## Summary

Replace every background subprocess the tracker spawns today with a **database-backed task
queue + one worker container**, and expose the queue over the API with live Server-Sent
Events (SSE). A task is a `tasks` row (`type` + `params`) that the worker claims and runs as
the *same module command the tracker runs today* (`python -u -m core.scrape`, `… --monitored-only`,
`core.score --extract`, `core.score --profile <id>`), streaming each output line into
`task_events`. The tracker's 7 launch buttons enqueue through `JobStorage` and render progress
from `tasks`/`task_events`, so a run survives a reload/closed tab and duplicates are not
started. The cron pipeline (`core.main`) and the worker share one `flock` on a data-volume
lock file, so a button press during the 07:00/11:00/16:00 run waits instead of overlapping.

This is the spec that **resolves the constitution's known violation "subprocess lancés par le
tracker"** (FR-010 PATCH): after it ships, `tracker_views/` contains no `subprocess`/`threading`
launch and `api/` never imports the pipeline entry points.

## Technical Context

**Language/Version**: Python 3.11 (`X | Y` unions, dataclasses).

**Primary Dependencies**: FastAPI + uvicorn + pydantic (already used by `api/`) for the routes
and the SSE `StreamingResponse`; the worker uses `subprocess` + `fcntl.flock` (stdlib, both
already used in the codebase). **No new dependency.**

**Storage**: SQLite (WAL) exclusively via `JobStorage`. Two new tables `tasks` + `task_events`
added to `SCHEMA` (idempotent `CREATE TABLE IF NOT EXISTS`) plus a recorded `tasks` migration
name (same pattern as `_migrate_api_tokens`). New `JobStorage` methods: `enqueue_task`,
`get_task`, `list_tasks`, `get_active_task`, `claim_next_task`, `append_task_events`,
`get_task_events`, `finish_task`, `mark_cancelled`, `request_cancel`, `is_cancel_requested`,
`recover_interrupted`, `get_worker_heartbeat`/`set_worker_heartbeat`,
`get_pipeline_lock_holder`/`set_pipeline_lock_holder`. No raw `sqlite3` outside `core/storage.py`.

**Testing**: `pytest` (in-memory DB for storage; `TestClient` for API). New `tests/test_tasks.py`
(storage: enqueue/dedup/claim/events/cancel/isolation), `tests/test_tasks_api.py` (routes + SSE
stream shape), `tests/test_worker.py` (command map + recover/timeout/cancel), and guard tests:
extend `tests/test_api_guard.py` (no `subprocess`/pipeline imports in `api/`), new
`tests/test_tracker_no_subprocess.py` (no `subprocess`/`threading` in `tracker_views/`).

**Target Platform**: Linux server (verva, Docker Compose). New always-on `worker` service
(same image, `python -m core.worker`); staging gains a worker via `scripts/staging.sh`.

**Project Type**: monorepo `core/` domain + FastAPI `api/` + Streamlit `tracker_views/`.

**Performance Goals**: worker claims one task at a time and streams events in small batches
(not one txn/line); SSE heartbeat ≤ 15 s; a task's effect is byte-identical to today's command
(SC-002). No concurrency target beyond single-worker serialisation.

**Constraints**: no LLM/long work in any HTTP handler (the worker does the work; SSE only
relays). `extra="forbid"` models. 64 KB write cap (already enforced by middleware). Lifecycle
of the tracker switch is a code cutover (rollback = redeploy the previous SHA — the schema is
additive and the old image ignores `tasks`/`task_events`).

**Scale/Scope**: single user today; one worker; 4 task types; ~7 tracker launch sites; events
bounded to last N lines + retention of finished tasks.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design — no violations.*

- **I — Filet large / point de filtrage unique**: tasks run today's commands verbatim; no
  ingestion-scope derivation, no fit judgement. ✓
- **II — Deux chemins**: queue + worker is code path; no prose payload mixed in. ✓
- **III — `user_id` seam**: `tasks.user_id` = the requester; `score.params.profile_id` ownership
  checked (`_require_profile` → `ProfileNotFoundError` → 404). ✓
- **IV — Structure déterministe**: `type`/`status`/`params` are deterministic strings; no LLM
  in the control flow. ✓
- **V — Chirurgical**: new files (`core/tasks.py`, `core/worker.py`, `api/routers/tasks.py`,
  `tracker_views/task_progress.py`); `core/storage.py` and `core/main.py` are touched **only**
  where FR-001/FR-004 explicitly require (schema + methods; lock). No opportunistic refactor. ✓
- **VI — Validation empirique**: staging parity + SC-002 (same effect on two DB copies) +
  FELFEL/Lausanne regression cases unchanged (tasks don't alter scoring). ✓
- **VII — Sécurité / moindre privilège**: two new scopes (`tasks:run`, `tasks:pipeline`);
  `scrape`/`scrape_monitored` additionally require `tasks:pipeline`; token CLI updated; worker
  output contains no secrets (it only relays existing command stdout). ✓
- **VIII — Scrapers par modèle**: untouched — the worker invokes `core.scrape` as-is. ✓
- **IX — Scoring optionnel**: extract/score tasks are user-triggered; nothing forces a profile. ✓
- **X — Catalogue / espace utilisateur**: the task **record** is user-space (keyed by requester,
  per the spec's `/v1/me/tasks` design); `scrape`/`extract` still write the shared catalogue —
  the task is a log of *who requested* the work, not the catalogue data itself. ✓
- **XI — Migration progressive / rollback**: schema is additive (expand); the worker is a new
  service (can be left `down` to keep tasks queued); the tracker switch removes the old
  subprocess path, so rollback is **redeploy the previous SHA** — the old image reads the same
  DB and ignores the new tables. No config flag: a flag keeping the subprocess path would
  re-introduce the violation FR-010 removes. Documented, not a violation. ✓

## Project Structure

### Documentation (this feature)

```text
specs/043-task-queue/
├── plan.md              # this file
├── research.md          # Phase 0 — design decisions R1–R8
├── data-model.md        # Phase 1 — tasks/task_events schema, scopes, state machine
├── quickstart.md        # Phase 1 — end-to-end validation guide
├── contracts/
│   └── tasks-api.md     # Phase 1 — /v1/me/tasks route contracts
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
core/
├── tasks.py                     # NEW — TASK_TYPES, command map, timeouts, pipeline flock
├── worker.py                    # NEW — single-consumer loop (claim → lock → run → stream → finish)
├── storage.py                   # EDIT — tasks/task_events tables + methods (FR-001)
└── main.py                      # EDIT — wrap each pipeline step in the shared lock (FR-004)
api/
├── routers/tasks.py             # NEW — /v1/me/tasks routes (SSE + JSON)
├── schemas.py                   # EDIT — Task/TaskCreate/TaskEvent models, scopes
├── deps.py                      # EDIT — tasks:run / tasks:pipeline scope deps
└── main.py                      # EDIT — include the tasks router
tracker_views/
├── task_progress.py             # NEW — shared task-progress renderer (jobs.py + settings.py)
├── jobs.py                      # EDIT — 3 launch buttons → enqueue + render
└── settings.py                  # EDIT — 4 launch buttons → enqueue + render
scripts/
├── api_token.py                 # EDIT — scope help text (cosmetic)
├── health_check.py              # EDIT — report worker heartbeat age
├── staging.sh                   # EDIT — start/remove a staging worker
└── deploy.sh                    # EDIT — build + restart the worker service
docker-compose.yml               # EDIT — add the `worker` service
tests/
├── test_tasks.py                # NEW — storage + isolation
├── test_tasks_api.py            # NEW — routes + SSE
├── test_worker.py               # NEW — command map / recover / timeout / cancel
├── test_api_guard.py            # EDIT — forbid subprocess + pipeline imports in api/
└── test_tracker_no_subprocess.py# NEW — guard: no subprocess/threading in tracker_views/
.specify/memory/constitution.md  # EDIT — PATCH: remove the resolved known violation
```

**Structure Decision**: tasks are a **user-space domain** (new router under `/v1/me`), so the
API surface lives in `api/routers/tasks.py` rather than growing `me.py` further; the worker is
a sibling of the pipeline modules in `core/`; the tracker's shared progress UI is a single new
helper so both `jobs.py` and `settings.py` render identically.

## Complexity Tracking

No constitution violations. One documented rollback posture (see Constitution Check XI): the
tracker cutover is a code change with additive schema, so rollback is "redeploy the previous
SHA", not a config flag — a flag that kept the subprocess path would contradict FR-010.
