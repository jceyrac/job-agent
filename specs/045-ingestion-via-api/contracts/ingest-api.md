# Contract — Ingestion API (spec 045)

New router `api/routers/ingest.py`, prefix `/v1/ingest`. All endpoints require an
`ingest`-scoped bearer token (the platform principal's token). Errors use the shared
envelope `{error, message}` (401/403/404/413/422 as in `api/main.py`).

## GET /v1/ingest/plan

- **Scope**: `ingest`.
- **Response 200**: the plan object in `data-model.md` — everything a run needs, no
  profile, no user-space data.
- **Contract test**: field-for-field against the direct path's reads; `extra="forbid"`
  on the response model.

## POST /v1/ingest/batch

- **Scope**: `ingest`.
- **Body**: a list of postings (`JobCreate` shape + optional `monitored_company_id`),
  max 5 MB (route-specific limit); the orchestrator sends ≤ ~100 postings per request.
- **Per posting, in order**: id computation (`JobPosting.id`), engaged-job dedup against
  the union of all users' engaged jobs (with `last_seen` touch), company upsert,
  `save_unscored` if new / `last_seen` touch if existing.
- **Transactions**: `ingest_batch` commits in short `BEGIN IMMEDIATE` sub-chunks of
  ~50 postings; a failing posting is reported without failing the batch.
- **Response 200**: `{new, existing, dropped_engaged, outcomes[]}`.
- **Idempotency**: the same batch twice creates nothing new (deterministic id).
- **Guarantee**: no LLM, no subprocess, no SQL in the handler — all DB access via
  `JobStorage.ingest_batch`.

## POST /v1/ingest/events

- **Scope**: `ingest`.
- **Body**: either a source auto-disable (`{type: "source_disabled", source, reason}` →
  `scraper.<slug>.enabled=false` + `disabled_reason`) or a scrape-only run record
  (`{type: "run", jobs_scraped, duration_seconds, run_type}` → `log_run` with
  `status="scraped"`).
- **Response 200**: `{ok: true}`.

## Auth & principal

- The platform principal `platform` owns the token; `create_api_token(..., user_id=
  "platform")` via `scripts/api_token create --user platform --scopes ingest`.
- No token value in logs (existing `write_log` logs token id, not value).
