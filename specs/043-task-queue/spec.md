# Feature Specification: Task Queue, Worker and Live Progress (roadmap step 6, part 2 of 2 — includes former step 2b)

**Feature Branch**: `043-task-queue`

**Created**: 2026-10-17

**Status**: Clarified 2026-10-17 — ready for `/speckit.plan`

**Input**: Roadmap `docs/roadmap-api.md`, step 6 part 2 (decision 2026-10-17), which absorbs step 2b. Constitution: Architecture Constraints ("Tâches longues : aucun appel LLM ni traitement de plus de quelques secondes dans une requête HTTP. Batch → table `tasks` + worker ; conversationnel → session pilotant le graphe LangGraph, progression en SSE"), VII (least privilege), X, XI; known violation "subprocess lancés par le tracker" (to be resolved here). "Long-running work (scrape, extraction, scoring) is requested, not executed, by clients: a task is queued, a single worker runs it, and anyone with the right scope can follow its progress live. The tracker's buttons stop spawning processes inside the web container."

---

## Clarifications

### Session 2026-10-17

- Q1: How does the worker run a task? → A: **As a subprocess of the same module command used today** (`python -u -m core.scrape`, `… --monitored-only`, `python -u -m core.score --extract`, `python -u -m core.score --profile <id>`). Identical effects, line-by-line output capture, clean cancellation; a failing task cannot take the worker down.
- Q2: Cron and the queue? → A: **Keep the host cron unchanged** and add the **shared pipeline lock** taken by both `core.main` and the worker. Moving the cron onto the queue is a later, separate change.
- Q3: Who may trigger a scrape? → A: **Two scopes.** `tasks:run` for `extract` and `score` on the user's own profiles; `tasks:pipeline` additionally required for `scrape` and `scrape_monitored`. The tracker (operator) enqueues every type through `JobStorage`.

---

## Context (verified against source at `aadc9f1`, 2026-10-17)

### How long work runs today

| Where | What | Problem |
|---|---|---|
| `tracker_views/jobs.py:119, 123` | `subprocess.Popen([python, -u, -m, core.scrape])`, `… core.score --profile <active>` — output kept in `st.session_state`, page polls with `st.rerun()` | Process lives in the **tracker container**, tied to a browser session: closing the tab loses the output; a second tab can start a second run |
| `tracker_views/jobs.py:134`, `settings.py:812` | `subprocess.run([python, -m, core.score, --extract], timeout=600)` | **Blocks the Streamlit page up to 10 minutes** |
| `tracker_views/settings.py:200, 215, 230` | `Popen` for scrape, scrape `--monitored-only`, score — output read by a helper thread | Same as above |
| `core/main.py:77–113` | Nightly pipeline (cron `0 7,11,16 * * *` on the host, `flock /tmp/job-agent-cron.lock` → `docker compose --profile manual run --rm agent`) runs monitored scrape → broad scrape → extract → score via `subprocess.run` | The host `flock` only serialises cron runs; **a tracker button can start a scrape or a scoring run while the cron pipeline is running** (duplicate LLM calls, concurrent writers) |

### What exists to build on

- API v1 read + write (specs 041, 042): scoped bearer tokens, user from the token, `HTTPBearer`, central `api.*` logging, log rotation, HTTPS on the tailnet under `/api`, staging runs tracker + API.
- Every DB access goes through `JobStorage` (spec 037); user space keyed by `user_id` (spec 039).
- The four pipeline commands are already invoked as modules (`python -m core.scrape [--monitored-only]`, `python -m core.score --extract`, `python -m core.score --profile <id>`).

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — A queue and a single worker run long work (Priority: P1)

As the operator, I want long-running pipeline work to be queued in the database and executed by one dedicated worker container, so that no web process (tracker or API) runs it, runs never overlap, and their outcome is recorded.

**Independent Test**: Enqueue a task; the worker claims it, runs the same command the tracker runs today, records status, exit code, duration and the tail of the output; a second identical request while the first is queued or running returns the existing task instead of a new one.

**Acceptance Scenarios**:

1. **Given** the schema, **Then** a `tasks` table exists (id, user_id, type, params JSON, status `queued|running|succeeded|failed|cancelled`, created_at, started_at, finished_at, exit_code, error, requested_by token/source) and a `task_events` table (task_id, seq, ts, line) for progress lines, created by an idempotent migration.
2. **Given** task types (allow-list): `scrape` (broad), `scrape_monitored`, `extract`, `score` (param: `profile_id`, must belong to the user), **Then** each maps to exactly the module command used today (Q1).
3. **Given** the worker (new compose service `worker`, same image, `python -m core.worker`, `restart: unless-stopped`, mounts `job_data`, log rotation), **Then** it claims one queued task at a time atomically, runs it, streams each output line into `task_events`, and records the result.
4. **Given** an identical task (`type` + `params`) already `queued` or `running`, **When** a new one is requested, **Then** the existing task is returned (no duplicate run).
5. **Given** a task exceeding its type's maximum duration, **Then** the worker terminates it and marks it `failed` (`error = "timeout"`).
6. **Given** the worker restarts while a task is `running`, **Then** on start-up that task is marked `failed` (`error = "interrupted"`); it is not re-run automatically.
7. **Given** `task_events`, **Then** stored output is bounded (last N lines per task, retention of finished tasks — see plan), so the table cannot grow without limit.

---

### User Story 2 — The pipeline never runs twice at the same time (Priority: P1)

As the operator, I want the cron pipeline and the worker to share one lock, so that a button press during the 07:00/11:00/16:00 run waits instead of running in parallel.

**Acceptance Scenarios**:

1. **Given** a lock file in the data volume (`/app/data/.pipeline.lock`, `flock`), **Then** `core.main` (cron) and the worker take it around every pipeline command (Q2).
2. **Given** the cron pipeline is running, **When** a task is queued, **Then** it stays `queued` (with a visible "waiting for the scheduled pipeline" note) and starts after the lock is released.
3. **Given** a task is running, **When** cron starts, **Then** cron waits for the lock (bounded wait, logged), and the run still records `success`/`error` as today.

---

### User Story 3 — Request and follow tasks through the API (Priority: P1)

As a client (future front, agents, scripts), I want to request a task and follow its progress live, with least privilege.

**Acceptance Scenarios**:

1. **Given** `POST /v1/me/tasks` `{type, params}`, **Then** it validates type and params, enqueues (or returns the identical active task), and answers `202` with the task — it never runs the work in the request.
2. **Given** `GET /v1/me/tasks` (filters `status`, `type`; pagination) and `GET /v1/me/tasks/{id}`, **Then** they return the user's tasks only (another user's task → `404`).
3. **Given** `GET /v1/me/tasks/{id}/events` with `Accept: text/event-stream`, **Then** the API streams progress lines as Server-Sent Events (one event per line, `id` = seq), supports resuming with `Last-Event-ID`, sends a heartbeat comment at least every 15 s, and closes with a final `status` event when the task ends. Without `text/event-stream`, the same route returns the lines as JSON (pagination by `after_seq`).
4. **Given** `POST /v1/me/tasks/{id}/cancel`, **Then** a `queued` task becomes `cancelled`; a `running` task is terminated (SIGTERM, then SIGKILL after a grace period) and marked `cancelled`.
5. **Given** scopes, **Then** `tasks:run` allows `extract` and `score` for the user's own profiles; `tasks:pipeline` is additionally required for `scrape` and `scrape_monitored` (platform-level work, Q3); read access to tasks requires `read:me`.
6. **Given** the SSE stream through `tailscale serve` (HTTPS proxy), **Then** events arrive incrementally (no proxy buffering — verified on staging with `curl -N`).

---

### User Story 4 — The tracker's buttons use the queue (Priority: P1, former step 2b)

As the user, I want the tracker's Fetch / Score / Extract / Scrape buttons to work as today, but through the queue, so they never block the page, survive a closed tab, and never collide with the cron run.

**Acceptance Scenarios**:

1. **Given** each of the 7 tracker launch sites (Jobs: scrape, score, re-extract; Settings: scrape, monitored-only scrape, score, re-extract), **Then** the button enqueues the corresponding task through `JobStorage` (the tracker still does not call the API) and shows the same feedback as today: label, elapsed time, live output tail, final success/error message.
2. **Given** a running task, **When** I close the tab and reopen the page, **Then** the page finds the active task and resumes showing its progress.
3. **Given** a task already queued or running, **When** I press the same button, **Then** the page shows the existing task instead of starting another.
4. **Given** the change, **Then** `tracker_views/` contains no `subprocess` / `threading` launch (guard test), and the constitution's known violation "subprocess lancés par le tracker" is removed (PATCH).
5. **Given** the Re-extract buttons, **Then** they no longer block the page for up to 10 minutes.

---

### User Story 5 — Released through the standard flow (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `scripts/staging.sh`, **Then** it also starts a staging worker on the staging volume and `down` removes it (lesson from spec 041: staging must run every new service).
2. **Given** staging on the exact SHA, **Then**: parity vs prod image identical; each tracker button produces a task that completes with the same effect as today (e.g. score run → same new scores as a direct `core.score` on another copy); an API task is followed with `curl -N` over the tailnet and its events arrive live; cancel works on a queued and on a running task; the lock is exercised (run `core.main` in staging while a task is queued → the task waits, then runs).
3. **Given** the deploy, **Then** SHA = staged SHA (`/api/health`), `worker` container up, tracker buttons work in prod on a cheap task (`score` on a small backlog or `extract`), the next cron run `success`, no overlap logged.

---

### Edge Cases

- Worker down: tasks stay `queued`; the tracker and `GET /tasks/{id}` show "waiting for worker" after a threshold; the health check reports the worker (heartbeat timestamp written by the worker).
- A task's command exits non-zero: `failed` with exit code and the output tail; no automatic retry.
- Very chatty output (scrape logs): bounded storage per task; SSE clients that fall behind resume with `Last-Event-ID`.
- `score` for a profile that is not the user's → `404` at enqueue time.
- Clock and ordering: `seq` is monotonic per task; events are never reordered.
- SQLite contention: the worker writes events in small batches (not one transaction per line) to stay light on the tracker and API.

---

## Requirements *(mandatory)*

### Functional Requirements

**Queue and worker (US1, US2)**

- **FR-001**: `tasks` and `task_events` tables (idempotent migration); `JobStorage` methods to enqueue (with dedup), claim atomically, append events (batched), finish, cancel, list/get (user-scoped, isolation-tested).
- **FR-002**: Task type allow-list mapping to the existing module commands (Q1), per-type maximum duration, bounded event storage and retention for finished tasks.
- **FR-003**: `core.worker`: single-consumer loop, claim → lock → run → stream → finish; start-up recovery (`running` → `failed: interrupted`); heartbeat for health; SIGTERM handling (finishes or fails the current task cleanly).
- **FR-004**: Shared pipeline lock (`flock` on a file in the data volume) taken by the worker and by `core.main` around each pipeline command (Q2).
- **FR-005**: Compose service `worker` (same image, `job_data`, env as `agent`, `restart: unless-stopped`, json-file rotation); `deploy.sh` builds and restarts it; `health_check.py` reports worker heartbeat age.

**API (US3)**

- **FR-006**: `POST /v1/me/tasks` (202), `GET /v1/me/tasks`, `GET /v1/me/tasks/{id}`, `GET /v1/me/tasks/{id}/events` (SSE + JSON fallback), `POST /v1/me/tasks/{id}/cancel`.
- **FR-007**: New scopes `tasks:run`, `tasks:pipeline` (Q3); token CLI updated; OpenAPI snapshot updated deliberately.
- **FR-008**: No pipeline work in any request handler (guard test: `api/` never imports `subprocess` nor the pipeline entry points); isolation suite extended to tasks.

**Tracker (US4)**

- **FR-009**: The 7 launch sites enqueue through `JobStorage` and render progress from `tasks`/`task_events`; same labels and messages; resume after reload; dedup visible.
- **FR-010**: Guard test: no `subprocess`/`threading` launch in `tracker_views/`; constitution PATCH removing the known violation; `CLAUDE.md` updated.

**Release (US5)**

- **FR-011**: `staging.sh` runs a staging worker; standard release flow with the US5-2 checks.

### Non-goals

- The cv_agent as a task, LangGraph sessions over SSE (step 6b / step 9).
- Moving the cron pipeline onto the queue (it shares the lock; see Q2).
- Several workers, priorities, scheduled tasks, automatic retries.
- Tracker calling the API (it keeps using `JobStorage`).
- New pipeline behaviour: tasks run exactly today's commands.

---

## Success Criteria *(mandatory)*

- **SC-001**: No web process runs pipeline work: guard tests on `api/` and `tracker_views/` green.
- **SC-002**: Each task type produces the same effect as the current command on two copies of the same DB.
- **SC-003**: Staging: SSE events arrive live through the tailnet proxy; cancel works (queued and running); a task queued during `core.main` waits for the lock.
- **SC-004**: Tracker: all 7 buttons work through the queue, survive a reload, never block the page; duplicates are not started.
- **SC-005**: Prod: worker heartbeat in the health check; next cron run `success`; no overlapping pipeline run.

---

## Questions answered in clarify (2026-10-17) — kept for the record

- **Q1** — subprocess of the existing module commands vs in-process calls → subprocess.
- **Q2** — cron unchanged + shared lock vs cron enqueuing tasks → cron unchanged + shared lock.
- **Q3** — one scope vs two scopes → two scopes (`tasks:run`, `tasks:pipeline`).
