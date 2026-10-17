# Research — Task Queue, Worker and Live Progress (spec 043)

Phase 0 output. Every open design point is resolved below with a decision, rationale, and
rejected alternatives. Storage-method and module references were verified against
`core/storage.py`, `core/main.py`, `core/scrape.py`, `core/score.py`, `api/`, `scripts/staging.sh`
and `docker-compose.yml` at `64ce8d6` (the 042 closing commit).

---

## R1 — Task schema + idempotent migration + dedup

**Decision**: two tables added to `SCHEMA` (idempotent `CREATE TABLE IF NOT EXISTS`) plus a
`_migrate_tasks(conn)` that records a `tasks` migration name (the `_migrate_api_tokens` pattern).
`tasks.id` is `TEXT PRIMARY KEY` = `uuid4().hex` (matches `api_tokens.id`); `params` is stored
as **canonical JSON** (sorted keys, no whitespace) so identical requests produce byte-identical
params. Dedup is enforced by a **partial unique index**:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_active_dedup
    ON tasks (user_id, type, params) WHERE status IN ('queued','running');
```

`enqueue_task` does `INSERT` and, on the unique-constraint violation, returns the existing
active task (the single-row guarantee the spec's US1-4 / US3-1 require).

**Rationale**: a partial unique index is the only race-free dedup — a check-then-insert has a
window where two callers enqueue the same task. Canonical JSON params make the index key
meaningful (`score` on two different profiles → two keys; identical `score` → one). The
`status IN ('queued','running')` predicate lets the index ignore finished tasks so a *new* run
of the same type+params can be enqueued after the previous one finished.

**Alternatives rejected**:
- Integer autoincrement `id` — fine for ordering, but a TEXT id is already the house style for
  tokens and keeps the API surface consistent.
- Dedup via a `SELECT … WHERE status IN ('queued','running')` check before insert — racy under
  WAL; the index closes it.

---

## R2 — Task type → command map + maximum durations

**Decision**: a new `core/tasks.py` holds the allow-list and the exact argv the worker runs
(verified against the tracker's current `_launch_bg`/`Popen` calls and `core/scrape.py`):

| `type` | argv (`[sys.executable, "-u", "-m", …]`) | max duration |
|---|---|---|
| `scrape` | `core.scrape` | 60 min |
| `scrape_monitored` | `core.scrape --monitored-only` | 45 min |
| `extract` | `core.score --extract` | 45 min |
| `score` | `core.score --profile <profile_id>` | 60 min |

`-u` is retained so each line lands in `task_events` unbuffered. Durations are module constants
(`TASK_TIMEOUTS`), not config — retries/scheduling/priorities are non-goals; a per-type ceiling
is all FR-002 requires. `core.main` is **not** in the allow-list (its subprocess steps are cron's,
not a task).

**Rationale**: the spec (Q1) is explicit — "same module command used today". `core.scrape` (broad)
already runs scrape→extract→score, and `--monitored-only` runs monitored scrape→extract→score, so
the four types cover every pipeline effect without a fifth "pipeline" type.

**Alternatives rejected**: in-process `import core.scrape; scrape.main()` — no line-by-line output
capture, no clean cancellation, a failing task could take the worker down (rejected by the spec).

---

## R3 — Worker loop (single consumer) + atomic claim

**Decision**: `core/worker.py` is a single-consumer loop over `JobStorage`:

1. **Startup recovery**: `recover_interrupted()` marks every `running` task `failed`
   (`error="interrupted"`, `finished_at=now`) — US1-6, no auto-retry.
2. **Lock**: acquire the shared pipeline `flock` (R4), recording the holder in `config`.
3. **Claim**: `claim_next_task()` does one atomic `UPDATE … SET status='running',
   started_at=?, cancel_requested=0 WHERE id = (SELECT id FROM tasks WHERE status='queued'
   ORDER BY created_at, id LIMIT 1) RETURNING …` on a single `_conn()` transaction (WAL +
   `busy_timeout=5000` already set). `started_at` and the per-type `TASK_TIMEOUTS` deadline
   begin here, after the lock is held. Exactly one worker claims at a time. **If nothing is
   claimed, the worker releases the lock immediately and sleeps to the next poll** — an idle
   worker never holds the lock between polls, so a task stays `queued` (and the tracker shows
   "The scheduled pipeline … is running") while cron holds the lock.
4. **Run**: `subprocess.Popen(argv, stdout=PIPE, stderr=STDOUT, text=False)`; a reader loop
   decodes lines, appends them to `task_events` in **small batches** (R6), and — between reads
   — checks `is_cancel_requested()`.
5. **Finish**: `finish_task(task_id, status, exit_code, error)`; release the lock.
6. **Timeout**: an outer deadline per `TASK_TIMEOUTS[type]` counted from claim time; on expiry →
   SIGTERM, grace, SIGKILL, `finish_task(… failed, error="timeout")`.
7. **Cancel (running)**: on `cancel_requested` → SIGTERM, grace, SIGKILL, `finish_task(…
   cancelled)`.
8. **SIGTERM on the worker itself**: let the current subprocess finish (bounded) or terminate it
   cleanly, then exit; on restart, step 1 recovers any `running` row.
9. **Heartbeat**: write `config["worker.heartbeat"]` = ISO-UTC now, once at startup and every
   ~15 s while alive.

**Rationale**: `RETURNING` (SQLite ≥ 3.35; python:3.11-slim ships newer) gives the claim +
row fetch in one statement, avoiding a SELECT-then-UPDATE race. Recovery-at-startup makes the
"worker restarts mid-task" case deterministic and recorded. Acquiring the lock **before**
claiming means the timeout clock only starts once the task actually runs (lock-wait never eats
the budget), and a task queued behind cron stays `queued` instead of timing out.

**Alternatives rejected**: a `SELECT … FOR UPDATE`-style loop — SQLite has no row locks; the
single `UPDATE … RETURNING` is the atomic primitive.

---

## R4 — Shared pipeline lock (`flock` in the data volume)

**Decision**: `core/tasks.py` exposes `pipeline_lock()` — a context manager taking an exclusive
`fcntl.flock` on `core.paths.data_path(".pipeline.lock")` (`/app/data/.pipeline.lock` in Docker,
`data/.pipeline.lock` in dev). On acquire it writes `config["pipeline.lock_holder"] = label`
(`"worker"` for the worker, `"cron"` for `core.main`); on release it clears the key. The worker
takes it **before claiming and around each task's command** (a task waits `queued` behind cron's
run, and the worker releases the lock immediately when nothing is queued); `core.main` takes it
around **each subprocess step**
(monitored scrape, broad scrape, extraction, scoring) — the same granularity "around every
pipeline command" FR-004 specifies. `core.main` uses a **bounded wait** (e.g. block up to
`tasks.cron_lock_wait_seconds`, then log and proceed/abort per step policy — the spec says
"bounded wait, logged").

**Rationale**: a lock file in the shared `job_data` volume is visible to both the `worker` and
`agent`/cron containers (both mount it). Recording the holder in `config` is the deterministic
signal the tracker/API read to render "waiting for the scheduled pipeline" (US2-2) vs "waiting
for the worker" — flock itself can't report *who* holds it. Bounded wait keeps cron from
hanging indefinitely behind a stuck worker.

**Alternatives rejected**:
- The host's existing `flock /tmp/job-agent-cron.lock` — host-scoped, not shared with the worker
  container, and only serialises cron (the exact gap US2 closes).
- `pg_advisory_lock`/Redis — out of scope; no new dependency, SQLite is the single source of truth.

---

## R5 — SSE endpoint shape + resumability + no buffering

**Decision**: `GET /v1/me/tasks/{id}/events` with `Accept: text/event-stream` returns a
`StreamingResponse(media_type="text/event-stream")` whose generator:

- opens with the task's current state; for `after_seq = int(Last-Event-ID header) or 0`,
  yields one `event:` per `task_events` row with `id: <seq>` and `data: <line>`;
- when no new line for >15 s, emits a comment `: ping` (heartbeat — the spec's ≤15 s rule);
- on task end (`succeeded|failed|cancelled`) emits a final `event: status` with `data: <status>`
  and returns;
- sets `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`.

The **same route without** `text/event-stream` returns the lines as JSON (pagination by
`after_seq`). The generator only *reads* `task_events` (polling every ~0.5–1 s) — it never runs
pipeline work, satisfying Constitution "no long work in a handler" (a stream is a relay, not
work).

**Rationale**: SSE is the spec'd transport; `Last-Event-ID` gives resumability for clients that
fall behind (US3-3, edge "clients that fall behind resume"). `tailscale serve` is a TCP/TLS
proxy, not an HTTP buffering proxy, so events pass through unbuffered — but `X-Accel-Buffering:
no` is a cheap belt-and-braces header and the staging check (`curl -N`) proves it (US3-6).

**Alternatives rejected**: WebSocket — heavier, not needed for one-way progress; polling-only —
the spec explicitly requires SSE + JSON fallback.

---

## R6 — Bounded event storage + batched append

**Decision**: `append_task_events(task_id, lines)` inserts a batch in one transaction with
sequential `seq` (worker holds the next `seq` in memory). Bounded by (a) keeping only the **last
4000 events** per *running/queued* task — a trimmed `DELETE` of old seqs beyond the window — and
(b) a retention rule, run by the worker's idle tick: finished tasks' events are pruned after
**7 days**, and finished task rows themselves after **30 days**. The `task_events` primary key
`(task_id, seq)` makes the trim and the `after_seq` page cheap.

**Rationale**: chatty scrapers can emit tens of thousands of lines; unbounded growth is the
spec's US1-7 concern. Batching (one txn per chunk, not per line) keeps the tracker/API light
(edge "SQLite contention"). A window + retention satisfies "bounded" without a second table.

**Alternatives rejected**: one txn per line — pathological under WAL; unbounded append — the
spec forbids it.

---

## R7 — Scopes: `tasks:run` vs `tasks:pipeline`

**Decision**: `API_TOKEN_SCOPES` gains `tasks:run` and `tasks:pipeline`. Enforce:

| task type | required scopes |
|---|---|
| `extract`, `score` | `tasks:run` |
| `scrape`, `scrape_monitored` | `tasks:run` **and** `tasks:pipeline` |

`score.params.profile_id` is additionally ownership-checked (`_require_profile` →
`ProfileNotFoundError` → 404) — a foreign profile is indistinguishable from missing
(Constitution VII, no existence oracle). Read access to tasks requires `read:me` (the routes
live under `/v1/me`). `scripts/api_token.py` help text is updated; the OpenAPI snapshot is
regenerated deliberately.

**Rationale**: this reads the clarify answer (Q3) literally — "`tasks:pipeline` *additionally*
required" = on top of `tasks:run` for platform-level work. `tasks:run` alone is the low-privilege
"you may run tasks that only touch your own space" scope; `tasks:pipeline` is the extra grant for
catalogue-wide scrape work (least privilege, VII).

**Alternatives rejected**: `tasks:pipeline` alone sufficing for scrape — reads "additionally" as
an alternative, which weakens the least-privilege split the two-scope answer implies.

---

## R8 — Tracker integration (dedup/resume/feedback) + guard

**Decision**: a new `tracker_views/task_progress.py` exports `render_task_progress(db, type,
params)` used by both `jobs.py` and `settings.py`. Each launch button: build the stable
`(type, params)` key (e.g. `score` + active profile; `scrape`/`scrape_monitored`/`extract` with
empty params) → `db.enqueue_task(type, params, requested_by="tracker")` → re-render. The renderer:

- looks up `get_active_task(type, params)` (the dedup row) on every render — **this is the
  resume-after-reload behaviour** (the task identity comes from the page state, not session
  state, so a closed tab loses nothing);
- `queued` + `pipeline.lock_holder == "cron"` → *"The scheduled pipeline (07:00, 11:00, 16:00)
  is running — your task starts when it finishes."*;
- `queued` + stale worker heartbeat (worker down) → "waiting for worker";
- `queued` + fresh heartbeat + a different task `running` → *"queued · waiting for "<other task
  label>" to finish"*;
- `running` → "⏳ {label} — running · {elapsed}" + live tail from `get_task_events`;
- `succeeded` → ✅ (unchanged); `failed` → ❌ (unchanged);
- `cancelled` from `queued` → "⏹ {label} cancelled"; from `running` → "⏹ {label} stopped —
  results saved before the stop are kept." — tail in "Last run output".

The old `bg_process`/`bg_output` session-state machinery and the blocking `subprocess.run(…
timeout=600)` in both files are **removed** (FR-009/FR-010). A guard test
`tests/test_tracker_no_subprocess.py` fails if `tracker_views/` imports `subprocess` or
`threading`; `test_api_guard.py` gains a test that `api/` never imports `subprocess` nor the
pipeline entry points (`core.scrape`, `core.score`, `core.main`, `core.worker`).

**Rationale**: the tracker keeps using `JobStorage` directly (non-goal "tracker calling the API").
A single shared renderer prevents the two pages drifting; removing the session-state process
handle is what makes reload-survival and dedup possible.

**Alternatives rejected**: a `st.session_state` task-id stash — loses the run on reload, exactly
the problem US4-2 solves.
