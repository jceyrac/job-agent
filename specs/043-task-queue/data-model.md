# Data model — Task Queue, Worker and Live Progress (spec 043)

Two new persisted entities (`tasks`, `task_events`) keyed to the requester (`user_id`). No change
to existing tables; the additions are idempotent (`SCHEMA` `CREATE TABLE IF NOT EXISTS`) plus a
recorded `tasks` migration name.

## Tables

### `tasks`

| Column | Type | Notes |
|---|---|---|
| `id` | `TEXT PRIMARY KEY` | `uuid4().hex` |
| `user_id` | `TEXT NOT NULL` | requester (owner); read/claim/cancel are user-scoped |
| `type` | `TEXT NOT NULL` | `scrape` \| `scrape_monitored` \| `extract` \| `score` (allow-list) |
| `params` | `TEXT NOT NULL` | canonical JSON (sorted keys): `{}` for scrape/scrape_monitored/extract; `{"profile_id": "<id>"}` for score |
| `status` | `TEXT NOT NULL DEFAULT 'queued'` | `queued` \| `running` \| `succeeded` \| `failed` \| `cancelled` |
| `requested_by` | `TEXT` | `"tracker"` or an API token id (provenance) |
| `cancel_requested` | `INTEGER NOT NULL DEFAULT 0` | set by `POST …/cancel` while `running` |
| `created_at` | `TEXT NOT NULL` | ISO-UTC |
| `started_at` | `TEXT` | set on claim |
| `finished_at` | `TEXT` | set on finish |
| `exit_code` | `INTEGER` | subprocess return code (finished only) |
| `error` | `TEXT` | `timeout` \| `interrupted` \| command output tail (failed only) |

Indexes: `idx_tasks_user (user_id, created_at DESC)`; `idx_tasks_status (status, created_at)`;
**partial unique** `idx_tasks_active_dedup (user_id, type, params) WHERE status IN
('queued','running')` — the dedup guarantee.

### `task_events`

| Column | Type | Notes |
|---|---|---|
| `task_id` | `TEXT NOT NULL` | FK → `tasks(id)` `ON DELETE CASCADE` |
| `seq` | `INTEGER NOT NULL` | monotonic per task, `1..` |
| `ts` | `TEXT NOT NULL` | ISO-UTC |
| `line` | `TEXT NOT NULL` | one stdout/stderr line |

Primary key `(task_id, seq)`. Bounded: last 4000 lines per active task; finished tasks' events
pruned after 7 days and finished `tasks` rows after 30 days (see research R6).

## State machine

```
             enqueue             claim (atomic UPDATE)         subprocess exits 0
  (none) ───────────────► queued ──────────────────────► running ────────────────────► succeeded
                             │                                │  exits non-zero
                             │                                ├────────────────────────────────► failed (exit_code, tail)
                             │                                │  timeout (SIGTERM→SIGKILL)
                             │                                ├────────────────────────────────► failed (error="timeout")
                             │                                │  cancel_requested (SIGTERM→SIGKILL)
                             │                                └────────────────────────────────► cancelled
                             │  cancel (queued)
                             └──────────────────────────────────────────────────────────────────► cancelled
  running at worker startup (crash) ────────────────────────────────────────────────────────────► failed (error="interrupted")
```

The worker acquires the pipeline lock **before** claiming (research R3): a task stays `queued`
while cron holds the lock, and `started_at` + the per-type timeout deadline are set at claim time.

## Scopes

| Scope | Grants | Task types |
|---|---|---|
| `tasks:run` | enqueue/read/cancel user tasks | `extract`, `score` |
| `tasks:pipeline` | **additional** grant for platform-level work | `scrape`, `scrape_monitored` (require `tasks:run` **and** `tasks:pipeline`) |

`read:me` gates task reads (the routes are under `/v1/me`). `API_TOKEN_SCOPES` gains the two
scopes; a token may combine them.

## JobStorage methods (new)

| Method | Purpose |
|---|---|
| `enqueue_task(type, params, requested_by=None) -> dict` | validate type/params; INSERT; on dedup conflict return the existing active task |
| `get_task(task_id) -> dict \| None` | user-scoped (foreign/absent → `None`) |
| `list_tasks(status=None, type=None, limit, offset) -> (rows, total)` | user-scoped, `created_at DESC` |
| `get_active_task(type, params) -> dict \| None` | the dedup row for a `(type, params)` key (tracker resume) |
| `claim_next_task() -> dict \| None` | atomic claim of the oldest `queued` task |
| `append_task_events(task_id, lines) -> None` | batched insert, sequential `seq` |
| `get_task_events(task_id, after_seq=0, limit=None) -> list` | `seq > after_seq`, ascending |
| `finish_task(task_id, status, exit_code=None, error=None) -> None` | set `finished_at`, status, exit/error |
| `mark_cancelled(task_id) -> None` | `queued` → `cancelled` |
| `request_cancel(task_id) -> None` | set `cancel_requested=1` (running) |
| `is_cancel_requested(task_id) -> bool` | polled by the worker |
| `recover_interrupted() -> None` | `running` → `failed` (`error="interrupted"`) at worker startup |
| `get_worker_heartbeat()/set_worker_heartbeat()` | `config["worker.heartbeat"]` ISO-UTC |
| `get_pipeline_lock_holder()/set_pipeline_lock_holder(label)` | `config["pipeline.lock_holder"]` = `worker` \| `cron`, cleared on release |
| `prune_task_history(event_days=7, task_days=30)` | idle-tick cleanup: drop finished tasks' events older than `event_days` and finished task rows older than `task_days` |

`score` enqueue checks `_require_profile(profile_id)` → `ProfileNotFoundError` → API 404.
