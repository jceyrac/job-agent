# Data Model — Agents through the API (spec 044)

No new table, column or migration. The change is **additive values** on the
existing schema plus three API request/response models.

## Reused entities (unchanged schema)

| Entity | Notes |
|---|---|
| `tasks` / `task_events` | spec 043. A new `type` value `score_job` rides the same schema, dedup (`idx_tasks_active_dedup` on `(user_id, type, params)`), lifecycle (`queued → running → succeeded/failed/cancelled`) and event stream. |
| `api_tokens.scopes` | new value `ingest` (added to `API_TOKEN_SCOPES`). |
| `jobs` | written by `save_unscored` (raw posting only) — shared catalogue, no user. |
| `job_applications` | written by `save_application` — keyed `(user_id, job_id)`. |

## New token scope

- `ingest` — submit job postings to the shared catalogue (`POST /v1/jobs`). Not
  `write:catalogue` (company facts) and not `tasks:run`. Reused by step 7.

## New task type

- `score_job` — params `{job_id: str, profile_id: str}`.
  - Command: `python -u -m core.score --job <job_id> --profile <profile_id>`.
  - Timeout: `600` s (10 min).
  - Scope: `tasks:run` (no `tasks:pipeline` — it is a user-space score, not a
    catalogue-wide scrape).
  - Validation at enqueue: `profile_id` must belong to the token user (404
    otherwise); `job_id` must exist (404 otherwise); identical active `(type,
    params)` → the existing task is returned (dedup).

## New API models (`api/schemas.py`, `extra="forbid"`)

- `JobCreate` — request for `POST /v1/jobs`:
  `title: str`, `company: str`, `url: str | None`, `location: str | None`,
  `description: str | None`. `title` + `company` required when `url` is empty
  (spec 036 rule); `description` has a size bound (matches the tracker's write).
- `JobCreated` — response `{job_id: str, created: bool}`.
- `ScoreJobParams` — `{job_id: str, profile_id: str}` (the `score_job` task params).
- `ApplicationUpdate` — request for `PUT /v1/me/jobs/{job_id}/application`:
  `analysis: str`, `cover_letter: str`. Response reuses the existing `Application`.

## State transitions

- `score_job`: `queued` → `running` (worker claims) → `succeeded` (score written for
  the profile) / `failed` (LLM or load error) / `cancelled` (user cancels).
- Job status via `PUT /v1/me/jobs/{id}/status` is unchanged (spec 042 lifecycle).

## Ownership & visibility

- `score_job` profile ownership is checked at enqueue (`get_profile` is user-scoped).
- `POST /v1/jobs` targets the shared catalogue (no user seam).
- `PUT …/application` targets the token user's space (404 for a foreign/absent job).
