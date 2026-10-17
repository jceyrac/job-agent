# Tasks: Task Queue, Worker and Live Progress (spec 043)

**Input**: `specs/043-task-queue/` — `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/tasks-api.md`, `quickstart.md`

**Tests**: requested by the spec (FR-001 isolation, FR-002/003 worker lifecycle, FR-008/FR-010 guards, US3 SSE, SC-002 parity). Written first (TDD).

**Organization**: by user story; each story is independently testable and releasable.

---

## Phase 1: Setup

- [ ] T001 Confirm baseline: on branch `043-task-queue`, `.venv` active, `python -m pytest tests/ -q` green (record the passing count as the pre-change baseline).

---

## Phase 2: Foundational (blocks all stories)

*The shared substrate every story builds on: the `tasks`/`task_events` schema, the task-type
allow-list + command map + lock, and the scope/schema plumbing. No story can start until this is green.*

- [ ] T002 Add `tasks` + `task_events` tables to `SCHEMA` in `core/storage.py` (idempotent `CREATE TABLE IF NOT EXISTS`, per `data-model.md`) and a `_migrate_tasks(conn)` that records the `tasks` migration name (the `_migrate_api_tokens` pattern) and registers it in `_init_db`.
- [ ] T003 [P] Add `tasks:run` and `tasks:pipeline` to `API_TOKEN_SCOPES` in `core/storage.py` (line 65).
- [ ] T004 [P] Create `core/tasks.py` (new): `TASK_TYPES`, `TASK_COMMANDS` (`type`+`params` → the exact `[sys.executable, "-u", "-m", …]` argv per `research.md` R2), `TASK_TIMEOUTS`, and `pipeline_lock()` (exclusive `fcntl.flock` on `core.paths.data_path(".pipeline.lock")`, writing/clearing `config["pipeline.lock_holder"]`).
- [ ] T005 [P] Add `TaskType` enum, `TaskCreate`, `Task`, `TaskEvent` models in `api/schemas.py` (`extra="forbid"`), and `tasks_run = require_scope("tasks:run")` / `tasks_pipeline = require_scope("tasks:pipeline")` in `api/deps.py`.
- [ ] T006 [P] Update `scripts/api_token.py` `--help`/docstring/`--scopes` help to name the two new scopes (cosmetic — validation already reads `API_TOKEN_SCOPES`).

**Checkpoint**: migration creates `tasks`/`task_events`; a `tasks:run` token mints; `core.tasks` imports and the type→argv map is importable.

---

## Phase 3: User Story 1 — A queue and a single worker run long work (P1) 🎯 MVP

**Goal**: `tasks`/`task_events` backed by `JobStorage`, and one worker container that claims and
runs one task at a time as today's module command, streaming output to events.

**Independent Test**: enqueue → worker claims → command runs → status/exit/duration/output tail
recorded; a second identical enqueue returns the existing task (no duplicate).

### Tests (write first — FAIL before implementation)

- [ ] T007 [P] [US1] Storage tests in `tests/test_tasks.py`: `enqueue_task` + dedup (partial unique index returns the existing active row, no new one); `claim_next_task` is atomic and oldest-first; `append_task_events`/`get_task_events` with `after_seq`; `finish_task`/`mark_cancelled`/`request_cancel`/`is_cancel_requested`/`recover_interrupted`; `prune_task_history` (events older than 7 days and finished rows older than 30 days removed); user-scoping isolation (`get_task`/`list_tasks` hide a foreign `user_id`'s task); `score` for a foreign profile → `ProfileNotFoundError`.

### Implementation

- [ ] T008 [US1] Implement the `JobStorage` task methods in `core/storage.py` (`enqueue_task`, `get_task`, `list_tasks`, `get_active_task`, `claim_next_task`, `append_task_events`, `get_task_events`, `finish_task`, `mark_cancelled`, `request_cancel`, `is_cancel_requested`, `recover_interrupted`, `prune_task_history`, `get_worker_heartbeat`/`set_worker_heartbeat`, `get_pipeline_lock_holder`/`set_pipeline_lock_holder`) — per `data-model.md`.
- [ ] T009 [US1] Create `core/worker.py` (new): single-consumer loop — startup `recover_interrupted()`; `pipeline_lock()` **then** `claim_next_task()` (`started_at` + the `TASK_TIMEOUTS` deadline begin at claim, after the lock is held; release the lock immediately when no task is queued); run the mapped command as `subprocess.Popen(stdout=PIPE, stderr=STDOUT)` streaming lines into `append_task_events` (batched); `finish_task`; per-type timeout (SIGTERM→SIGKILL → `failed: timeout`); cancel (SIGTERM→SIGKILL → `cancelled`); graceful SIGTERM; heartbeat (`set_worker_heartbeat`) at startup and every ~15 s.
- [ ] T010 [P] [US1] Worker tests in `tests/test_worker.py`: the exact argv per type; `recover_interrupted` flips `running`→`failed:interrupted`; timeout → `error="timeout"`; cancel → `cancel_requested` then `cancelled` (fake subprocess); a task queued while the lock is held stays `queued` and then runs with its full timeout allowance; an idle worker never holds the lock between polls.
- [ ] T011 [US1] Add the `worker` service to `docker-compose.yml` (same image, `command: python -m core.worker`, `job_data` mount, env as `agent`, `restart: unless-stopped`, json-file rotation); build + `up -d worker` it in `scripts/deploy.sh`.
- [ ] T012 [US1] Bound event storage in `append_task_events` (keep the last 4000 events per active task) and a worker idle-tick prune of finished tasks' events after 7 days and finished task rows after 30 days.

**Checkpoint**: `pytest tests/test_tasks.py tests/test_worker.py -q` green; a manual worker run completes a cheap `extract` task with events.

---

## Phase 4: User Story 2 — The pipeline never runs twice at the same time (P1)

**Goal**: cron (`core.main`) and the worker share one `flock`, so a button press during the
07:00/11:00/16:00 run waits instead of running in parallel.

**Independent Test**: with `core.main` holding the lock, a queued task stays `queued` and starts
after release; with a task running, `core.main` waits (bounded) and logs.

### Tests

- [ ] T013 [P] [US2] Lock tests in `tests/test_worker.py`: `pipeline_lock()` records/clears `config["pipeline.lock_holder"]`; a second acquirer blocks and then proceeds.

### Implementation

- [ ] T014 [US2] Wrap each subprocess step in `core/main.py` (monitored scrape, broad scrape, extraction, scoring) in `pipeline_lock()` with a bounded wait + log line (holder label `"cron"`).

**Checkpoint**: `pytest tests/test_worker.py -q` green; `core.main` + a task serialize on the lock (verified on staging in US5).

---

## Phase 5: User Story 3 — Request and follow tasks through the API (P1)

**Goal**: `POST/GET/cancel` tasks and follow progress live over SSE, with least privilege.

**Independent Test**: `POST /v1/me/tasks` returns `202` and never runs the work; `GET …/events`
streams lines as SSE and closes with a final status; cancel works on queued and running.

### Tests (write first)

- [ ] T015 [P] [US3] Contract + scope tests in `tests/test_tasks_api.py`: enqueue `202` + dedup returns the existing task; `extract`/`score` need `tasks:run`, `scrape`/`scrape_monitored` need `tasks:run`+`tasks:pipeline` (403 otherwise); `score` for a foreign profile → 404; `GET`/`GET {id}` user-scoping (foreign → 404); SSE stream yields `id:`/`data:` lines, a ≤15 s heartbeat comment, and a final `status` event; JSON fallback paginates by `after_seq`; cancel on queued (→`cancelled`), running (→`cancel_requested`), terminal (→`409`).

### Implementation

- [ ] T016 [US3] Create `api/routers/tasks.py` (new) with `POST /tasks` (202), `GET /tasks`, `GET /tasks/{id}`, `GET /tasks/{id}/events` (SSE `StreamingResponse` + JSON fallback), `POST /tasks/{id}/cancel` per `contracts/tasks-api.md`; register it in `api/main.py` under `/v1/me`.
- [ ] T017 [US3] Extend `tests/test_api_guard.py`: add the task methods the API calls to the allow-list, the worker-only methods to `FORBIDDEN_METHODS`, and a new test that `api/` imports neither `subprocess` nor the pipeline entry points (`core.scrape`, `core.score`, `core.main`, `core.worker`).
- [ ] T018 [US3] Regenerate `api/openapi.json` (deliberately) and make `tests/test_api_openapi.py` green (new routes appear with `HTTPBearer`).

**Checkpoint**: `pytest tests/test_tasks_api.py tests/test_api_guard.py tests/test_api_openapi.py -q` green; SSE streamed over the test client.

---

## Phase 6: User Story 4 — The tracker's buttons use the queue (P1, former step 2b)

**Goal**: the 7 launch buttons enqueue through `JobStorage` and render progress from
`tasks`/`task_events` — no blocking, survives reload, no duplicates, no `subprocess`/`threading`.

**Independent Test**: each button enqueues and renders the queue progress; a reload resumes; a
second press shows the existing task; `tracker_views/` imports no `subprocess`/`threading`.

### Tests (write first)

- [ ] T019 [P] [US4] Guard test `tests/test_tracker_no_subprocess.py`: `tracker_views/` contains no `subprocess` or `threading` import.

### Implementation

- [ ] T020 [US4] Create `tracker_views/task_progress.py` (new): `render_task_progress(db, type, params)` — dedup/resume via `get_active_task`; three queued states ("The scheduled pipeline (07:00, 11:00, 16:00) is running — your task starts when it finishes." when `pipeline.lock_holder == "cron"`; "waiting for worker" when the heartbeat is stale; "queued · waiting for "<other task label>" to finish" when a different task is `running` ahead); live tail from `get_task_events`; terminal messages (✅/❌ unchanged; ⏹ from `queued` = "⏹ <Task> cancelled", from `running` = "⏹ <Task> stopped — results saved before the stop are kept.").
- [ ] T021 [US4] Rework `tracker_views/jobs.py`: the 3 launch buttons (Run scrape, Run scoring, Re-extract) enqueue via `db.enqueue_task(…, requested_by="tracker")` and render `render_task_progress`; remove the `bg_process`/`bg_output` session-state machinery and the blocking `subprocess.run(…, timeout=600)`.
- [ ] T022 [US4] Rework `tracker_views/settings.py`: the 4 launch buttons (scrape, monitoring, scoring, re-extract) enqueue and render; remove the `Popen`/helper-thread machinery.
- [ ] T023 [US4] Constitution PATCH (v2.0.3 → v2.0.4): remove the resolved known violation "subprocess lancés par le tracker" and update the Sync Impact Report in `.specify/memory/constitution.md`; update the `CLAUDE.md` API section (new scopes + `/v1/me/tasks` routes).

**Checkpoint**: `pytest tests/test_tracker_no_subprocess.py -q` green; all 7 buttons work through the queue locally (quickstart §5).

---

## Phase 7: User Story 5 — Released through the standard flow (P1)

**Goal**: staging runs a worker; deploy ships the worker; SHA/parity/health/lock verified.

- [ ] T024 [US5] Update `scripts/staging.sh`: start a staging worker container (`job-agent-staging-worker` on the staging volume) in `up`, and remove it in `down` (extend the leftover checks).
- [ ] T025 [US5] Update `scripts/health_check.py` to report worker heartbeat age (read `config["worker.heartbeat"]`, gate on a threshold).
- [ ] T026 [US5] Staging round-trip (`quickstart.md` §6) — **STOP before this step**:
  1. Stage the **exact SHA** (`scripts/staging.sh up <full-sha>`, never the branch name); confirm `/api/health` version == staged SHA and the staging worker is up.
  2. Parity vs the prod image byte-identical **before** any task runs.
  3. Each tracker button → a task completing with the same effect as today (score run → same new scores as a direct `core.score` on another copy).
  4. SSE checked with `curl -N` through the tailnet (`…:8443/api/v1/me/tasks/{id}/events`) — events arrive live, no buffering.
  5. Cancel on a **queued** and on a **running** task.
  6. Lock exercised: run `core.main` in staging while a task is queued → the task waits, then runs.
  7. `scripts/staging.sh down` removes tracker, API **and worker**.
- [ ] T027 [US5] ff-only merge onto `main` — **STOP before this step** (`git merge --ff-only <full-sha>`; refuse non-ff).
- [ ] T028 [US5] Deploy — **STOP before this step; deploy outside 07:00/11:00/16:00**:
  `./scripts/deploy.sh` → `worker` container up; `/api/health` version == staged SHA; a cheap prod task (small `score`/`extract`) completes; the next cron run `success` with no overlapping pipeline run.

---

## Phase 8: Polish & validation

- [ ] T029 Run `python -m pytest tests/ -q` (full suite green — 582 baseline + new task tests).
- [ ] T030 Run `quickstart.md` §1–§5 locally; confirm each behaviour (dedup, resume, SSE, scopes, guards).

---

## Dependencies & Execution Order

- **Phase 1 → 2**: Setup, then Foundational (T002–T006). T002/T003 gate every story (schema + scopes).
- **Phase 3 (US1)**: after Foundational; the worker (T009) needs the type-map (T004) and storage methods (T008).
- **Phase 4 (US2)**: after Foundational; touches `core/main.py` only (the lock context manager from T004).
- **Phase 5 (US3)**: after Foundational (schema + scopes); independent of US1/US2's worker code.
- **Phase 6 (US4)**: after Foundational + US1 (needs `enqueue_task`/`get_active_task`/events).
- **Phase 7 (US5)**: after US1–US4; **GATED** — STOP before T026 (staging), T027 (merge), T028 (deploy).
- **Phase 8**: after everything.

### Parallel opportunities

- T002/T003 (storage schema + scopes) and T004/T005/T006 (different files) are parallel within Foundational.
- T007 (storage tests) and T010 (worker tests) are different files → parallel.
- US2 (core/main.py) and US3 (api/) touch disjoint areas → parallel after Foundational.

## Implementation Strategy

**MVP** = Foundational + US1 (schema + worker runs a task) — deliverable and independently testable. Then US2 (lock), US3 (API), US4 (tracker) in priority order, then the release flow (US5). US2–US4 are small and could be merged incrementally; the release flow is strictly gated.

## Notes

- `core/storage.py` is touched **only** at the schema + `API_TOKEN_SCOPES` + the new task methods (FR-001) — no change to existing methods.
- `core/main.py` is touched **only** to wrap its subprocess steps in the shared lock (FR-004).
- The worker (`core/worker.py`) is not in `api/`, so it is not bound by the API allow-list guard.
- The tracker keeps calling `JobStorage` directly (non-goal: tracker calling the API).
- Commit after each phase; stop at any checkpoint to validate independently.
