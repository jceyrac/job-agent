# Implementation Plan: Residence & Relocation Model

**Branch**: `022-residence-relocation-model` | **Date**: 2026-07-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/022-residence-relocation-model/spec.md`

## Summary

Replace the single-residence Swiss geography model with **Engagement resolution**:
a deterministic Tier-0 function that, for each job, computes the set of feasible
residences from `profile.relocation`, each with a personal cost. The cheapest
feasible residence determines whether the job passes Tier-0; its cost is
forwarded as prose to the LLM scorer. A new `contract_geo` field adds a
contract-type × residence-zone legality gate. Two migration columns
(`relocation_cost`, `residence_base`) are added to `job_scores` following the
`comp_flag` precedent.

Deletes `_geography_for_mode()` — the single-point replacement is
`_resolve_engagement()`. All per-mode country allowlists, geo-zone fallbacks,
and hybrid/on-site commute checks are subsumed.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: Streamlit (UI), sqlite3 (DB), dataclasses (profile model)
**Storage**: SQLite WAL, `search_profiles.criteria` JSON blob, `job_scores` table migration
**Testing**: pytest (`tests/test_storage.py` 146 tests, `tests/test_jobspy_helpers.py` 6 tests, `tests/test_scorer_parsing.py` 15 pre-existing failures)
**Target Platform**: Dev Mac → Docker/Linux production
**Project Type**: CLI pipeline + Streamlit UI
**Performance Goals**: Engagement resolution is O(residences × 1) per job — ~5 dict lookups; no API calls
**Constraints**: No new dependencies, no new DB table, no scraper changes, no deletion of deprecated fields
**Scale/Scope**: 6 files touched, 2 new schema columns, 1 function deleted, 1 function added

## Constitution Check

*GATE: Must pass before Phase 0 research.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Filet large** | ✅ | Scorer-side only — no scraper changes |
| **II. Deux chemins** | ✅ | Code change; `scoring_context` prose unchanged |
| **IV. Structure déterministe** | ✅ | Relocation feasibility, contract geography, salary floor are deterministic rules over already-extracted fields |
| **V. Chirurgical** | ✅ | One function deleted (`_geography_for_mode`), one added (`_resolve_engagement`); deprecated fields inert; digest unchanged by decision |
| **VI. Validation empirique** | ✅ | Four new mock cases; FELFEL band preserved |
| **IX. Scoring optionnel** | ✅ | No change to pipeline independence |

**Gate: PASS** — No violations.

## Project Structure

### Documentation (this feature)

```text
specs/022-residence-relocation-model/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 — includes live-code verification notes
├── data-model.md        # Phase 1 — schema changes
├── quickstart.md        # Phase 1 — validation guide
└── tasks.md             # Phase 2 — /speckit-tasks output
```

### Source Code (repository root)

```text
profiles.py              # SearchProfile: +relocation, +contract_geo; round-trip; synthesis
scorer.py                # _resolve_engagement, -_geography_for_mode, Tier-0 rewrite, comp_flag home
storage.py               # job_scores: +relocation_cost, +residence_base; migration script
score.py                 # MOCK_JOBS: +4 cases
tracker_views/settings.py # Residence & relocation section in profile editor
tracker_views/jobs.py    # Card badge for non-home residence
```

## Complexity Tracking

No violations to justify.

---

## Phase 0 — Research

No NEEDS CLARIFICATION markers — the spec was grounded against live source (Fable review, 2026-07-09). All claims marked ✅ were verified. See [research.md](research.md) for the live-code verification notes.

Key confirmations:
- `comp_flag` migration pattern (Phase 8, lines 498-502) provides the exact template
- `save_scored()` (lines 1504-1533) shows the column list + upsert pattern
- `_evaluation_result()` threads `comp_flag` through correctly
- `_run_mock()` (lines 145-215) shows the extract→evaluate→assert-band pattern
- `from_criteria()` (lines 107-160) shows synthesis from legacy criteria dicts
- `UNIFIED_JC` work_mode_geography provides the remote countries list for seed

## Phase 1 — Design

See [data-model.md](data-model.md) for schema details and [quickstart.md](quickstart.md) for validation.
