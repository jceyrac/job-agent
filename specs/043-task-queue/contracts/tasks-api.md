# Task-route contracts (spec 043)

All routes under `/v1/me/tasks`, authenticated bearer, user from the token (never a parameter).
Scopes per the map in `data-model.md`. Errors follow the existing `_ERROR_CODES` shape
(`{"error": "<code>", "message": "…"}`). `POST …/cancel` is a write and logs one write-log line
(token id, user id, route, task id, outcome — never the body).

## `POST /v1/me/tasks` → `202`

- **Body**: `TaskCreate { type: TaskType, params: dict }` (`extra="forbid"`).
- **Validation**: `type` in the allow-list; `params` shape per type (`score` requires
  `profile_id`); `score` → `_require_profile` ownership check.
- **Scopes**: `extract`/`score` → `tasks:run`; `scrape`/`scrape_monitored` → `tasks:run` +
  `tasks:pipeline`.
- **Effect**: `enqueue_task(type, params, requested_by=token_id)` — never runs the work.
  Returns the created task, or the **existing** active task when dedup fires.
- **Response**: `202` → `Task` (full row).
- **Errors**: `403` insufficient scope · `404` `score` for a foreign profile · `422` bad
  type/params.

## `GET /v1/me/tasks` → `200`

- **Query**: `status?` (`queued|running|succeeded|failed|cancelled`), `type?`, `limit` (1–500,
  default 100), `offset` (≥0).
- **Scope**: `read:me`.
- **Response**: `{ items: [Task…], total, limit, offset }` — user's tasks only, `created_at DESC`.

## `GET /v1/me/tasks/{id}` → `200`

- **Scope**: `read:me`.
- **Response**: `Task`. A foreign/absent id → `404` (no existence oracle).

## `GET /v1/me/tasks/{id}/events` → `200` (SSE) or `200` (JSON)

- **Scope**: `read:me`. Foreign/absent id → `404`.
- **SSE** (`Accept: text/event-stream`): `text/event-stream` stream —
  - `event: line`, `id: <seq>`, `data: <line>` for each event with `seq > Last-Event-ID`
    (default 0);
  - a comment `: ping` when idle >15 s (heartbeat);
  - a final `event: status`, `data: <status>` when the task reaches a terminal status, then close.
  - Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`, `Connection: keep-alive`.
- **JSON** (no SSE `Accept`): `{ items: [{seq, ts, line}…], after_seq, task_status }` paginated
  by `after_seq` (query, default 0; `limit` ≤ 2000).

## `POST /v1/me/tasks/{id}/cancel` → `200`

- **Scope**: `tasks:run`.
- **Effect**: `queued` → `mark_cancelled` (status `cancelled`); `running` → `request_cancel`
  (sets `cancel_requested`; the worker terminates and marks `cancelled`); terminal status →
  `409` (already finished).
- **Response**: `200` → `Task`.
- **Errors**: `404` foreign/absent id · `409` already terminal · `403` insufficient scope.

## Task JSON shape (`Task`, `extra="forbid"`)

```json
{
  "id": "…", "user_id": "…", "type": "score", "params": {"profile_id": "…"},
  "status": "running", "requested_by": "tracker", "cancel_requested": false,
  "created_at": "…", "started_at": "…", "finished_at": null, "exit_code": null, "error": null
}
```
