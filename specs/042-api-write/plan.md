# Implementation Plan: API v1 — write endpoints for the user space

**Branch**: `042-api-write` | **Date**: 2026-10-17 | **Spec**: `specs/042-api-write/spec.md`

**Input**: Feature specification from `specs/042-api-write/spec.md` (roadmap step 6, part 1 of 2).

## Summary

Expose, over the read-only v1 API (spec 041), every **user-space write** the
tracker performs today — job status + lifecycle events, company relationship
(status/notes/watch), contacts, interactions, profile — with the **same effects**
(identical resulting rows), so the future front and agents can *act*, not just
read. One shared-catalogue write (`PATCH /v1/companies/{id}` — company facts) is
exposed under a distinct `write:catalogue` scope. New bearer scopes `write:me`
and `write:catalogue` are introduced; the tracker is **not touched**. Synchronous
only: no task queue, worker, SSE or scoring triggers (that is spec 043).

## Technical Context

**Language/Version**: Python 3.11 (`X | Y` unions, dataclasses).

**Primary Dependencies**: FastAPI + uvicorn + pydantic (already allowed and used
by `api/`). **No new dependency**: the 64 KB body cap is a small ASGI middleware
in `api/`, not a library.

**Storage**: SQLite (WAL) exclusively via `JobStorage`. No `sqlite3` in `api/`;
one `JobStorage` per request (unbound for auth in `deps.py`, bound to the token's
`user_id` for data).

**Testing**: `pytest`; `tests/test_api_contract.py`, `tests/test_api_guard.py`,
`tests/test_api_auth.py`, `tests/test_isolation.py` are extended for writes.
`fastapi.testclient.TestClient` against a temp DB (`tests/conftest.py::api_env`).

**Target Platform**: Linux server (verva, Docker Compose `api` service) — same
deployment as the read-only API; no new service.

**Project Type**: web-service (FastAPI) over the `core/` domain.

**Performance Goals**: synchronous, sub-second writes; single-user today (no
concurrency/optimistic-locking target — last-write-wins via WAL).

**Constraints**: 64 KB request-body cap (`413`); tailnet-only; no LLM or
long-running work in a handler; structured write log with no body/token material;
`extra="forbid"` on every request model.

**Scale/Scope**: single user; ~11 write routes mirroring `tracker_views/` actions.

## Constitution Check

*GATE: passed before Phase 0. Re-checked after Phase 1 (no change).*

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Filet large / ingestion | ✅ N/A | No ingestion changes; writes never touch scope/titles. |
| II — Prose vs Code | ✅ | Code path; no prose payload. |
| III — `user_id` seam | ✅ | All `write:me` routes keyed by the token's `user_id` via `get_user_db`. |
| IV — Deterministic structure | ✅ | No LLM; every write is a deterministic `JobStorage` call. |
| V — Surgical / non-goals | ✅ | Non-goals explicit in spec (no task queue, no new behaviours, tracker untouched). |
| VI — Empirical validation | ✅ | Staging round-trip + tracker/API parity (US5). |
| VII — Least privilege | ✅ | New scopes `write:me` / `write:catalogue`; read-only tokens → 403. |
| VIII — Scrapers | ✅ N/A | No scraper change. |
| IX — Scoring optional | ✅ N/A | No scoring change; profile edits do not rescore. |
| X — Catalogue vs user space | ✅ | `write:me` = user space only; `write:catalogue` = company facts only (no user relationship fields). |
| XI — Progressive migration | ✅ | Tracker untouched; API is additive; app stays operational. |

**Gates — all pass.** Two items require justification (see Complexity Tracking):
(1) one-line change to `core/storage.py` (`API_TOKEN_SCOPES`); (2) two spec
corrections surfaced during research (job-scoped event endpoints; drop the
contact `409`).

## Project Structure

### Documentation (this feature)

```text
specs/042-api-write/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions (body cap, 404 pre-checks, endpoint shape)
├── data-model.md        # Phase 1 — request/response models + scope map
├── quickstart.md        # Phase 1 — runnable validation scenarios
├── contracts/           # Phase 1 — per-route contract (endpoint ↔ JobStorage method)
│   └── api-write.md
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
api/
├── deps.py              # + write_me / write_catalogue scope deps (require_scope already exists)
├── main.py              # + register the body-size middleware
├── middleware.py        # NEW — RequestSizeLimitMiddleware (64 KB → 413)
├── routers/
│   ├── me.py            # + write routes (US1–US3)
│   └── catalogue.py     # + PATCH /v1/companies/{id} (US4)
├── schemas.py           # + pydantic request models (extra="forbid") + enums
└── serializers.py       # (reuse read serializers for response bodies)

core/
└── storage.py           # + API_TOKEN_SCOPES gains write:me / write:catalogue (one line)

scripts/
└── api_token.py         # + help text mentions write scopes (optional, cosmetic)

tests/
├── test_api_contract.py # + contract tests: API write == direct JobStorage write (rows)
├── test_api_guard.py    # + write allow-list + forbidden pipeline/platform writes
├── test_api_auth.py     # + scope tests (read-only → 403, write:me → 403 on catalogue)
└── test_isolation.py    # + two-user isolation for every write route
```

**Structure Decision**: No new top-level package — writes extend the existing
`api/` routers and add one middleware module. This mirrors the read API's layout
(one router per domain) and keeps the diff local to `api/` + one storage constant.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `core/storage.py` modified (`API_TOKEN_SCOPES` frozenset) | FR-007: token CLI must accept `write:me`/`write:catalogue`; the frozenset is the single source of truth for valid scopes. | Re-deriving scopes in `api/` or `scripts/` would fork the validation and let a token be minted that the storage layer rejects. |
| Event edit/delete routes are job-scoped (`/v1/me/jobs/{job_id}/events/{event_id}`) instead of the spec's flat `/v1/me/events/{event_id}` | Reconciliation (`reconcile_lifecycle_status`) is job-scoped; the tracker always reconciles after an edit/delete, and the flat path carries no `job_id`. | Adding a `get_lifecycle_event(event_id)` read accessor to `core/storage.py` would satisfy the flat path but touches Stable core for one route. |
| Contact `409` on duplicate `linkedin_url` dropped | The underlying `upsert_contact` **merges** (upsert) — it never raises on a duplicate; the tracker's effect is a merge. A 409 would be new behaviour (spec non-goal). | A strict-create pre-check would diverge from "same effects as the tracker" (FR-002). |
