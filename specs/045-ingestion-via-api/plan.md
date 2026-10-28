# Implementation Plan: Ingestion through the API (roadmap step 7)

**Branch**: `045-ingestion-via-api` | **Date**: 2026-10-28 | **Spec**: `spec.md`

**Input**: Feature specification from `/specs/045-ingestion-via-api/spec.md`

## Summary

Move ingestion off the direct `JobStorage` path and behind the API. Scrapers become
pure functions (targets in, postings out) with no storage and no profile read; the
scrape orchestrator fetches a run plan from `GET /v1/ingest/plan`, runs the scrapers,
and submits collected postings to `POST /v1/ingest/batch`, which deduplicates, upserts
companies and saves jobs server-side in short `BEGIN IMMEDIATE` transactions. A config
flag `ingest_via_api` (default off) keeps today's direct path byte-for-byte until a
record/replay harness proves the two paths identical — a synthetic-fixture identity
test plus a real-scrape staging replay (~600 postings) that is the gate for switching
the flag on (Constitution XI).

## Technical Context

**Language/Version**: Python 3.11 (union types, dataclasses, f-strings)

**Primary Dependencies**: FastAPI + uvicorn + pydantic (already allowed for `api/`),
`python-jobspy` (existing scraper dep), stdlib. No new dependency.

**Storage**: SQLite WAL via `JobStorage` (`data/jobs.db`), read + write only through
`JobStorage` methods; `BEGIN IMMEDIATE` for any read-then-write transaction.

**Testing**: pytest, ruff `E9/F63/F7/F82`, in-memory DB fixtures,
`TestClient` for API contract tests, the offline socket fixture.

**Target Platform**: Linux server (verva) — Docker Compose (`api`, `worker`, `agent`,
`tracker`), Tailscale-only API under `/api`.

**Project Type**: Monorepo — Python pipeline (`core/`) + FastAPI (`api/`) + Streamlit
(`tracker/`).

**Performance Goals**: One broad scrape ≈ 600 postings with descriptions; the
orchestrator sends ≤ ~100 postings per request (≤ 5 MB), and `ingest_batch` commits in
~50-posting `BEGIN IMMEDIATE` sub-chunks; never a write lock held across the run; total
run time comparable to today.

**Constraints**: No LLM or long work in a handler; no SQL in `api/`; scoped tokens;
single user today (multi-user seam exists); surgical diffs; app stays operational after
every step.

**Scale/Scope**: 25+ scrapers, ~160 MB DB, one active user, one platform principal.

## Constitution Check

*GATE: passes before Phase 0; re-checked after design — still passes.*

| Principle | Verdict | Note |
|-----------|---------|------|
| I — Filet large | ✅ PASS | Closes the remaining gap: LinkedIn/Indeed `search_locations` and Greenhouse `greenhouse_boards` move into the platform ingestion scope (Q2), initialised once from current profile values; the spec-040 guard extends to `effective_search_locations`/`greenhouse_boards`. |
| V — Modif chirurgicale | ✅ PASS | Non-goals explicit; only `users.is_service` + the scope-JSON extension; direct path untouched while flag is off. |
| VII — Sécurité | ✅ PASS | Non-human platform principal with ingest-only token; token via `.env`, never logged; API tailnet-only; least privilege. |
| VIII — Scrapers par modèle | ✅ PASS | Scrapers become pure functions; targets injected by the orchestrator; "à terme no DB" now realised for the ingestion path. |
| X — Catalogue vs espace utilisateur | ✅ PASS | Dedup moves server-side to the union of engaged jobs; no profile read in the orchestrator; no user-space data on catalogue entities. |
| XI — Migration progressive | ✅ PASS | `ingest_via_api` flag; synthetic + real-scrape record/replay identity proof (US4-2); flag-off rollback; staging before prod. |
| IX — Scoring optionnel | ✅ PASS | Batch writes unscored jobs; extraction/scoring/purge unchanged. |

No violations to justify — Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/045-ingestion-via-api/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── ingest-api.md
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code (repository root)

The feature touches these existing paths (no new top-level dirs):

```text
core/
├── scrapers/                 # become pure functions (no storage/profile)
│   ├── base.py               # drop storage; inject targets/enabled state
│   ├── boards/linkedin.py    # locations from injected plan
│   ├── boards/indeed.py      # locations from injected plan
│   ├── greenhouse.py         # boards from injected plan
│   └── ats/{lever,workable}.py  # slugs from injected plan
├── scrape.py                 # orchestrator: plan+batch when flag on; direct when off
└── storage.py                # + ingest_batch(); scope fields; users.is_service
api/
├── routers/ingest.py         # GET /v1/ingest/plan, POST /batch, POST /events
├── schemas.py                # plan/batch/event models
├── deps.py                   # ingest scope already exists — reused
└── middleware.py             # per-route body limit for /ingest/batch
scripts/
├── ingestion_scope.py        # --set-locations / --set-boards
└── api_token.py              # allow --user platform (ingest scope)
tests/
├── test_api_ingest.py        # plan/batch/events contract (spec 044 file, extended)
├── test_scraper_purity.py    # guard: no storage/profile under core/scrapers/
├── test_ingest_identity.py   # record/replay identity
└── test_isolation.py         # platform principal isolation (US5)
```

**Structure Decision**: extend the existing monorepo in place — no new top-level
structure. `core/` holds the domain + storage, `api/` the ingest router, `scripts/`
the prose path, `tests/` the guard/contract/identity suites.

## Complexity Tracking

None — no constitution violations.
