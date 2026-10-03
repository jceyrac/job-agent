# Implementation Plan: Location Independence (roadmap step 1a)

**Branch**: `032-location-independence` | **Date**: 2026-10-03 | **Spec**: `specs/032-location-independence/spec.md`

**Input**: Feature specification from `/specs/032-location-independence/spec.md`

## Summary

Remove every place where the app depends on *where its files are* or *where it is
launched from*, so that the move of the domain into `core/` (step 1c) can only fail
loudly (import error at startup), never silently. No file is moved.

Three levers:

1. **`paths.py` becomes the single source of truth** for every data/output/config
   location (`PROJECT_ROOT`, `DATA_DIR`, `DB_PATH`, `OUTPUT_DIR`, `ENV_PATH`,
   `data_path()`), with `PROJECT_ROOT` as the *only* value derived from `__file__`.
2. **Prod data location is explicit and guarded** — `docker-compose.yml` sets
   `JOB_AGENT_DATA_DIR=/app/data` and `JOB_AGENT_REQUIRE_DB=1`; `paths.py` raises at
   import (creating nothing) when the guard is on and `jobs.db` is absent.
3. **Scripts launch as modules** — all 14 subprocess launches switch from
   `"<name>.py"` to `-m <module>` with identical arguments and flags.

A guard test (`tests/test_location_independence.py`) fails the build if any of the
removed patterns (`__file__`-derived data path, CWD-relative `data/` literal, or a
`.py` subprocess launch) is reintroduced.

## Technical Context

**Language/Version**: Python 3.11 (stdlib only for this feature)

**Primary Dependencies**: none new — `os`, `subprocess`, `pathlib` (existing);
Streamlit (existing, untouched). No LLM, no FastAPI.

**Storage**: SQLite via `JobStorage` (unchanged — `storage.py` is a non-goal).
The DB *file path* is what changes, and only in `paths.py`.

**Testing**: `pytest`. New `tests/test_location_independence.py` (guard scan) plus the
existing `tests/` suite.

**Target Platform**: Linux server (Docker, `WORKDIR /app`) and macOS dev (repo root
CWD). `-m <module>` relies on the CWD being the repo root, which holds today in both
(container `WORKDIR /app`, dev runs from repo root) — documented as an assumption.

**Project Type**: CLI pipeline + Streamlit web app (single repo).

**Performance Goals**: n/a — the guard is a single `os.path.exists` at import.

**Constraints**: surgical diffs; no file moves/renames; no `pyproject.toml`; DB access
stays via `JobStorage` (this feature adds no new raw `sqlite3`); no change to
`storage.py`/`models.py`/`profiles.py`/`scorer.py`/`llm.py`/`scrapers/`; no change to
the background-task mechanism (`subprocess` + thread) — only the command form.

**Scale/Scope**: 9 location sites + 14 launch sites across 13 files; one new test file;
one `paths.py` extension; `docker-compose.yml` + `CLAUDE.md` edits.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| **I — Filet large** | ✅ Pass | No scraper/scorer/ingestion change; ingestion scope untouched. |
| **V — Chirurgical** | ✅ Pass | Minimal, mechanical edits; non-goals explicit in spec; no new dependency. |
| **VII — Sécurité** | ✅ Pass | No secrets; only path env vars (`JOB_AGENT_DATA_DIR`, `JOB_AGENT_OUTPUT_DIR`, `JOB_AGENT_REQUIRE_DB`) are added. |
| **X — Catalogue/espace user** | ✅ Pass | No schema change; no user/data coupling introduced. |
| **XI — Migration progressive** | ✅ Pass | This *is* a migration step; app stays operational (parity SC-001, drill SC-002, live smoke SC-005). Guard fails loud, never silent. |
| **Stable core** | ✅ Pass (exception) | `main.py`, `scrape.py`, `tracker_views/shared.py`, `score.py`, `notifier.py`, `prepare.py`, `seed.py`, `export_seed.py`, `export_jobs.py` are touched — **permitted** because roadmap step 1a explicitly concerns them (they hold the location/launch sites being fixed). `storage.py`, `models.py`, `profiles.py`, `scorer.py`, `llm.py`, `scrapers/`, `tracker_views/onboarding.py` remain untouched. |
| **DB access via JobStorage** | ✅ Pass | `preferences.py:114` and `scraper_toggle.py:14` keep `JobStorage` (only the path argument changes). No new raw `sqlite3` (existing raw SQL in `export_seed.py` predates this spec and is out of scope). |

No violations → Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/032-location-independence/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── paths.md         # paths.py public API contract
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root — single project, no new directories)

```text
paths.py                       # EXTEND: sole location source of truth + guard
docker-compose.yml             # env: JOB_AGENT_DATA_DIR + JOB_AGENT_REQUIRE_DB
export_jobs.py                 # __file__-relative DB/OUTPUT → paths
seed.py                        # "data/companies.json" → data_path()
export_seed.py                 # "data/companies.json" → data_path()
notifier.py                    # outputs/ → OUTPUT_DIR
score.py                       # outputs/ → OUTPUT_DIR
prepare.py                     # outputs/applications → OUTPUT_DIR/applications
tracker_views/shared.py        # .. / .env → ENV_PATH
tracker_views/preferences.py   # "data/jobs.db" → DB_PATH
tracker_views/jobs.py          # 3 launches → -m
tracker_views/settings.py      # 4 launches → -m
main.py                        # 4 launches → -m
scrape.py                      # 2 launches → -m
monitoring_agent.py            # 1 launch → -m (dev tool)
scripts/scraper_toggle.py      # "/app/data/jobs.db" → DB_PATH
tests/test_location_independence.py   # NEW guard test
```

**Structure Decision**: No restructuring — this is a pre-move hardening step (step 1a).
All edits are in-place; the move to `core/` is step 1c and explicitly out of scope.

## Complexity Tracking

No constitution violations — table intentionally empty.
