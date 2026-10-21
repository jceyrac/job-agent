# Contract — Agents through the API (spec 044)

The cv_agent's contract with the server. Scopes are the token's least-privilege
grant; the agent carries `read:me`, `write:me`, `tasks:run`, `ingest`. All paths
are under the tailnet HTTPS `/api` base (`root_path`); JSON `extra="forbid"`.

## 1. Submit a posting — `POST /v1/jobs`

- **Scope**: `ingest`.
- **Request** (`JobCreate`):
  ```json
  {"title": "Senior PM", "company": "Acme", "url": null,
   "location": "Zürich, CH", "description": "…"}
  ```
  `title` + `company` required when `url` is empty; `description` size-bounded.
- **Behaviour**: compute `job_id` exactly as `JobPosting.id` (title/company when no
  URL, else URL). If the job already exists → return it unchanged, **never
  overwrite**. If new → `save_unscored` (raw posting only, no extraction, no LLM).
- **Response** `200` (`JobCreated`):
  ```json
  {"job_id": "<20-hex>", "created": true}
  ```
- **Errors**: `401` no/unknown/revoked/expired token; `403` missing `ingest`;
  `422` validation (e.g. title/company missing without URL); `413` >64 KB.

## 2. Single-job scoring task — `score_job`

- **Enqueue**: `POST /v1/me/tasks` `{"type": "score_job", "params":
  {"job_id": "<id>", "profile_id": "<pid>"}}` (spec 043 surface).
- **Scope**: `tasks:run` (no `tasks:pipeline`).
- **Command**: `python -u -m core.score --job <job_id> --profile <profile_id>` —
  extract-if-needed, then score for that profile (Tier 0 then Tier 1). No new logic.
- **Timeout**: 10 min. **Dedup**: an identical active task returns the existing one.
- **Errors**: `404` unknown job or a profile not owned by the user; `403` missing
  `tasks:run`; `422` missing params.
- **Follow**: `GET /v1/me/tasks/{id}` (poll) or `GET …/events` (SSE, spec 043), then
  read the result with `GET /v1/me/jobs/{job_id}`.

## 3. Record the application — `PUT /v1/me/jobs/{job_id}/application`

- **Scope**: `write:me`.
- **Request** (`ApplicationUpdate`): `{"analysis": "…", "cover_letter": "…"}`.
- **Behaviour**: `save_application(job_id, analysis, cover_letter)` — byte-identical
  to the tracker's write. Returns the stored `Application`.
- **Errors**: `404` job not visible to the user; `403` missing `write:me`; `422`
  validation; `413` >64 KB.

## 4. Reads the agent relies on (existing, unchanged)

- `GET /v1/me` — identity + active profile + `criteria` (`read:me`).
- `GET /v1/me/jobs/{job_id}` — job + user-scoped scores + lifecycle + application
  (`read:me`).
- `PUT /v1/me/jobs/{job_id}/status` — `archived` (skip) / `ready` (approval)
  (`write:me`, spec 042).

## 5. Agent environment contract

- `JOB_AGENT_API_URL` — base URL (default: production tailnet API; staging for tests).
- `JOB_AGENT_API_TOKEN` — bearer token (or a local credentials file, mode `0600`),
  never logged, never committed.
- `CV_OUTPUT_DIR` — local output folder (replaces `db.get_config("cv.output_dir")`).
- On unreachable API / missing scope / missing profile: the agent stops **before any
  LLM spend** with an explicit message and a non-zero exit code.
