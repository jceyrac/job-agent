# Implementation Plan: API v1 — read-only (roadmap step 5 — Phase C)

**Branch**: `041-api-read` | **Date**: 2026-10-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/041-api-read/spec.md`

## Summary

Roadmap step 5 opens Phase C: the first brick of the API architecture. A new
**read-only** FastAPI service (`api/`) sits on top of `core`, exposing the
Phase-B data model over HTTP, reachable **only** through the tailnet (never
public), authenticated with **scoped bearer tokens** bound to a user. The
Streamlit tracker is not touched — it keeps reading `JobStorage` directly.

Two axes of work, both governed by Constitution VII/X/XI:

- **Read endpoints** — one router per domain (`catalogue`, `me`), each endpoint
  a thin serialisation layer over an existing read-only `JobStorage` method.
  `read:catalogue` returns only shared-catalogue fields; `read:me` returns the
  token user's space (the user comes from the token, never from a parameter).
- **Auth** — a new user-space table `api_tokens` (SHA-256 hashes only, never the
  raw token), a CLI to create/list/revoke tokens, a Bearer-auth dependency that
  resolves a token to a user + scopes, and `401`/`403`/`404` semantics with no
  token material in any log.

The service is additive: no migration touches existing tables (only the new
`api_tokens` table, harmless to the previous image), no write endpoint, no task
queue, no SSE (those are step 6, which also absorbs step 2b). Rollback = stop
and remove the `api` service.

## Technical Context

**Language/Version**: Python 3.11 (`X | Y` unions, dataclasses, f-strings).

**Primary Dependencies**: `fastapi` + `uvicorn` (new; authorised for `api/` by
the Constitution Architecture Constraints). `pydantic` ships with FastAPI.
Versions pinned to releases ≥ 14 days old (FR-002).

**Storage**: SQLite WAL via `JobStorage` (`data/jobs.db`, named volume `job_data`
in prod). The API adds **no SQL** — every read goes through an existing
`JobStorage` method; the only new persistence is the `api_tokens` table plus its
`JobStorage` accessors (create/list/revoke/resolve/touch), added as an idempotent
migration.

**Testing**: `pytest`. New contract tests (FastAPI `TestClient` + in-memory DB)
assert each endpoint's payload equals its `JobStorage` source; auth tests prove
`401`/`403`/`404`/scope/expiry/no-log; a guard test (source scan, as spec 040)
fails on any `api/` call outside the read allow-list, any `sqlite3` import, or an
unbound `JobStorage`; an OpenAPI snapshot test commits `api/openapi.json` and
fails on drift. `tests/test_isolation.py` is extended to the API (two users, two
tokens).

**Target Platform**: Linux server (prod `verva`, Docker Compose) + Mac dev.

**Project Type**: web-service (FastAPI) added to the existing monolith (CLI
pipeline + Streamlit tracker, mid-migration to the API architecture).

**Performance Goals**: none new — read-only endpoints over unchanged queries;
pagination contract (`limit` default 100, max 500; `offset`). One `JobStorage`
per request, no long transactions (WAL, concurrent with the cron writer).

**Constraints**: no SQL in `api/`; no LLM/long work in a request handler; tailnet
only; scoped tokens with least privilege; tokens and `Authorization` headers
never logged; `root_path="/api"` and forwarded headers trusted only from the
loopback proxy.

**Scale/Scope**: single user today; ~3 700 jobs; read-only v1 (the full US3 table).

## Constitution Check

*GATE: passed. Re-checked after Phase 1 design — still passed.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Filet large, filtrage unique | ✅ n/a | No ingestion change; the scope/title logic from step 4 is untouched. |
| II — Deux chemins | ✅ | Service + schema = code path; token create/revoke and scope values = prose path (`scripts/api_token.py`, DB, no deploy). |
| III — Couture `user_id` | ✅ | Token → `user_id` → `JobStorage(db_path, user_id=…)`; `profile_id` stays the scoring key (unchanged). |
| IV — Structure déterministe | ✅ | No LLM; serialisation is deterministic (ISO-8601 dates, stable enum strings). |
| V — Modification chirurgicale | ✅ | Additive only; tracker untouched; non-goals explicit; new deps justified by the spec. |
| VI — Validation empirique | ✅ | Contract + isolation + guard suites; staging parity (API figures == tracker figures) before merge. |
| VII — Sécurité d'abord | ✅ | Tailnet-only exposure; scoped bearer tokens; SHA-256 hashes, raw token printed once; no token/`Authorization` in logs. **Central principle.** |
| VIII — Scrapers par modèle | ✅ n/a | No scraper change. |
| IX — Scoring optionnel | ✅ n/a | API reads scores that exist; no score is computed by the API. |
| X — Catalogue / espace utilisateur | ✅ | `read:catalogue` returns only shared fields (contract-tested); `read:me` is bound to the token user; other users' resources are `404` (no existence disclosure). |
| XI — Migration progressive | ✅ | Tracker still reads `JobStorage` directly; `api_tokens` is expand/contract (harmless to the previous image); backup before deploy; staging parity; rollback = stop/remove the `api` service. |

## Project Structure

### Documentation (this feature)

```text
specs/041-api-read/
├── plan.md              # This file
├── research.md          # Phase 0 output — decisions + source verification
├── data-model.md        # Phase 1 output — api_tokens + request/response shapes
├── quickstart.md        # Phase 1 output — validation runbook
├── contracts/           # Phase 1 output — accessor + CLI + endpoint + auth contracts
│   ├── api_tokens.md
│   ├── endpoints_catalogue.md
│   ├── endpoints_me.md
│   ├── auth.md
│   └── api_token_cli.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
api/
├── __init__.py
├── main.py              # app factory; root_path="/api"; mount routers; /health
├── deps.py              # Bearer-auth dependency (token → user_id + scopes); get_db
├── schemas.py           # pydantic response models (one per endpoint payload)
├── serializers.py       # JobStorage dict/dataclass → pydantic model
├── routers/
│   ├── __init__.py
│   ├── catalogue.py     # /v1/jobs, /v1/jobs/{job_id}, /v1/companies/{company_id}
│   └── me.py            # /v1/me, /v1/me/feed, /v1/me/jobs/{id}, /v1/me/stats,
│                        #   /v1/me/dashboard, /v1/me/applications,
│                        #   /v1/me/companies, /v1/me/companies/{id},
│                        #   /v1/me/contacts, /v1/me/contacts/{id}
└── openapi.json         # committed snapshot (generated by the snapshot test)
core/
└── storage.py           # + _migrate_api_tokens() + api-token accessors (in scope:
                         #   schema + persistence live here; API adds no SQL)
scripts/
└── api_token.py         # FR-004 — create / list / revoke (prints raw token once)
tests/
├── test_api_contract.py # US3 — payload == JobStorage source, per endpoint
├── test_api_auth.py     # US2 — 401/403/404, scopes, expiry, no-log
├── test_api_guard.py    # FR-009 — read allow-list, no sqlite3, bound JobStorage
├── test_api_openapi.py  # FR-008 — OpenAPI snapshot
└── test_isolation.py    # FR-010 — extended: two users/tokens, no cross-read
docker-compose.yml       # + api service (127.0.0.1:8601:8601, job_data, REQUIRE_DB=1)
requirements.txt         # + fastapi, uvicorn
pyproject.toml           # + api package
scripts/deploy.sh        # + build/restart api (after seed)
scripts/staging.sh       # + api on 127.0.0.1:8602, tailnet https 8443
scripts/health_check.py  # + GET /health of the API
docs/infrastructure.md   # tailnet HTTPS /api exposure (prereqs, command, status, undo)
docs/api-exposure.md     # FR-017 — prerequisites before any public exposure
CLAUDE.md                # commands, infrastructure, API section
```

**Structure Decision**: single existing project — the feature adds one top-level
package `api/`, one script `scripts/api_token.py`, a schema migration + accessors
in `core/storage.py`, and test files. No new top-level directories beyond `api/`.
Routers are one per domain (`catalogue`, `me`) per the Constitution "monolithe
modulaire" constraint; `/health` lives on the app (not a domain).

## Complexity Tracking

No Constitution violations — all principles pass (see above). The only new
dependency (FastAPI + uvicorn) is explicitly authorised by the Constitution for
`api/`; versions are pinned ≥ 14 days old per FR-002.
