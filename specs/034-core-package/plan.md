# Implementation Plan: Domain Package `core/` (roadmap step 1c)

**Branch**: `034-core-package` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/034-core-package/spec.md`

## Summary

Move the 27 domain modules plus `scrapers/` and `cv_agent/` into a `core/`
package so the tracker (and later `api/`, `web/`) are clients of one bounded
domain, imported everywhere as `core.<module>` with no shims. Done in two commits:
**Phase A** removes the last `__file__`-derived locations and adds guard tests
that resolve every string-based module reference, so the move can only fail
loudly; **Phase B** is a single mechanical commit — `git mv` + a committed
idempotent rewrite script + non-Python reference updates. The live tracker and
nightly cron stay operational; release goes through the staging flow on the exact
SHA.

## Technical Context

**Language/Version**: Python 3.11 (X | Y unions, f-strings, dataclasses)

**Primary Dependencies**: Streamlit (>=1.40, tracker UI stays at root), LangGraph
(cv_agent), setuptools editable install (`pyproject.toml`). No new dependencies.

**Storage**: SQLite (`data/jobs.db`, WAL) via `JobStorage` — schema untouched.

**Testing**: pytest (`tests/`; `test_storage.py` in-memory; repo-scan guards).

**Target Platform**: dev MacBook Pro M5 Pro (Python 3.11 venv) + HPE ProLiant
Ubuntu server (Docker Compose, loopback + `tailscale serve`).

**Project Type**: Python package refactor within an existing monorepo (pre-API
step of the API migration).

**Performance Goals**: none — no behaviour change; parity fingerprint must be
byte-identical.

**Constraints**: surgical (no opportunistic refactor); no shims; no relative
imports; DB access only via `JobStorage`; no LLM provider named outside
`core/llm.py`; release only through staging on the exact SHA.

**Scale/Scope**: 27 domain modules + 2 packages move; 14 `-m` strings, 5
`importlib.import_module` sites, ~16 `mock.patch` targets, 3 non-Python commands.

## Constitution Check

*GATE: pass before Phase 0 (verified). Re-checked after design — no change.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I · Filet large | ✅ | ingestion scope untouched |
| II · Deux chemins | ✅ | pure code-path change (git → deploy); no prose |
| III · user_id | ✅ | deferred to step 3, not in scope |
| IV · Déterminisme | ✅ | no new LLM or structured-field logic |
| V · Chirurgical | ✅ | planned roadmap refactor with explicit non-goals |
| VI · Validation empirique | ✅ | US3 staging + parity + cron gate |
| VII · Sécurité | ✅ | no new egress, no secrets |
| VIII · Scrapers | ✅ | moved verbatim, taxonomy unchanged |
| IX · Scoring optionnel | ✅ | unchanged |
| X · Catalogue/espace | ✅ | deferred to step 3 |
| XI · Migration progressive | ✅ | governing principle; staging flow, always-operational |
| Monorepo (`core/`) | ✅ | this step *creates* the `core/` package |
| Stable core | ✅ (exception) | `storage.py`/`models.py`/`profiles.py`/`scrape.py`/`scorer.py`/`main.py`/`scrapers/` are touched — sanctioned: step 1c explicitly concerns them (FR-012 updates the list to `core/` paths) |

No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/034-core-package/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── rewrite-imports.md
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

Before → after (only the moved surfaces shown):

```text
# BEFORE
storage.py  models.py  profiles.py  scrape.py  scorer.py  main.py  … (27 modules)
scrapers/{base,__init__}.py  scrapers/boards/  scrapers/ats/  scrapers/company_sites/
cv_agent/{cli,graph,nodes,renderer,state,prompts,nextcloud_publish}.py
tracker.py  tracker_views/  scripts/  tests/   (stay at root)

# AFTER
core/
├── __init__.py
├── storage.py  models.py  profiles.py  scrape.py  scorer.py  main.py  paths.py  … (27 modules)
├── scrapers/  scrapers/boards/  scrapers/ats/  scrapers/company_sites/
└── cv_agent/
tracker.py  tracker_views/  scripts/  tests/   (unchanged location; imports → core.*)
```

**Structure Decision**: the `core/` package exactly as the constitution's Monorepo
constraint names it; the Streamlit surface (`tracker.py`, `tracker_views/`) stays
at the root in this step (non-goal), deferring the `tracker/` move to a later
step to bound the blast radius.

## Complexity Tracking

No constitution violations — the table is intentionally empty.
