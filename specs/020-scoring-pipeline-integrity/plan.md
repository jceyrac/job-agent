# Implementation Plan: Scoring Pipeline Integrity

**Branch**: `020-scoring-pipeline-integrity` | **Date**: 2026-07-07 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/020-scoring-pipeline-integrity/spec.md`

## Summary

Fix three root-cause bugs discovered during live investigation: (1) `profiles.py`'s
`load_active_profile()` silently backfills the `scoring_context` from code into
an existing DB profile row, causing silent divergence between `profiles.py` and
the DB; (2) `storage.py::get_jobs_for_scoring()` applies a fragile SQL
substring-based location exclusion (`exclude_location_contains`) that silently
drops legitimate candidates before they reach the structured Tier-0 geography
check — a redundant, cruder duplicate of logic already implemented correctly in
`scorer.py::evaluate_for_profile()`; (3) the "Unscored" UI metric on the Jobs
and Settings pages is `total_jobs - scored_distinct`, a naive count ignoring all
pre-filters (status, freshness, title, blacklisted companies), so it never
matches what "Run scoring" actually attempts.

Additionally, apply two minor hardening fixes: ensure background process
completion messages survive the immediate `st.rerun()` so users can see them,
and wrap the Re-extract button's `subprocess.run(timeout=600)` in a
`TimeoutExpired` handler.

All three investigation items from the spec are resolved; see [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: Streamlit (UI), sqlite3 (storage), subprocess (background processes)
**Storage**: SQLite WAL mode (`data/jobs.db`) via `JobStorage` class
**Testing**: pytest, `tests/test_storage.py` (162 unit tests, in-memory DB)
**Target Platform**: macOS dev + Docker/Linux production
**Project Type**: CLI pipeline + Streamlit web UI
**Performance Goals**: Unscored count computed on each page render (candidate pool is ~100-600 jobs — direct `len(get_jobs_for_scoring())` is O(ms))
**Constraints**: No new dependencies, surgical diffs only, preserve all existing scoring logic
**Scale/Scope**: ~600 jobs, 1 active profile, changes touch 4 files

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Rationale |
|-----------|--------|-----------|
| **I. Filet large** — scraper = wide net, scorer = all filtering | ✅ | Goal 2 removes the SQL location pre-filter, restoring the single-filter-point principle. The SQL location exclusion was an undocumented third filtering layer with no constitutional basis. |
| **II. Deux chemins** — prose vs code | ✅ | All changes are code-path fixes. No prose changes in scope. Profile edits go through Settings UI (prose path, unchanged). |
| **III. Profil unifié unique** | ✅ | No new profiles. Existing `unified_jc` profile unchanged semantically. |
| **IV. Structure déterministe** | ✅ | Tier-0 geography check uses deterministic structured fields (company_country, geo_zone). Removing the SQL substring filter eliminates non-deterministic behavior (substring false positives like "usa" in "Lausanne"). |
| **V. Modification chirurgicale** | ✅ | 5 explicit non-goals. Changes touch only the lines causing bugs — no refactoring. |
| **VI. Validation empirique** | ✅ | FELFEL regression test in acceptance criteria. Testing plan with Live DB snapshot. |
| **VII. Sécurité d'abord** | ✅ | No new network egress, no new secrets. DB snapshot script is read-only. |
| **VIII. Scrapers par modèle d'acquisition** | N/A | No scraper changes. |
| **IX. Scoring optionnel** | ✅ | No change to pipeline independence. Scoring still optional. |

**Gate: PASS** — No violations.

## Project Structure

### Documentation (this feature)

```text
specs/020-scoring-pipeline-integrity/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 — investigation results
├── data-model.md        # Phase 1 — schema notes (no changes)
├── quickstart.md        # Phase 1 — validation guide
└── tasks.md             # Phase 2 — /speckit-tasks output
```

### Source Code (repository root)

```text
profiles.py              # Goal 1: remove scoring_context backfill
storage.py               # Goal 2: remove SQL location pre-filter
score.py                 # Goal 2: stop passing location_contains pre-filter keys
tracker_views/jobs.py    # Goals 3, 4: fix Unscored metric, hardening
tracker_views/settings.py # Goals 2, 3, 4: UI field, Unscored metric, hardening
scripts/
└── sync_live_db.sh      # Goal 5: new — Live→Dev DB snapshot
tests/
└── test_storage.py      # Update if get_jobs_for_scoring changes behavior
```

**Structure Decision**: Single-project layout. Changes are surgical — each file touched only at the specific lines causing the bugs.

## Complexity Tracking

No violations to justify.

---

## Phase 0 — Research Summary

All three investigation items from the spec are resolved. Full details in [research.md](research.md).

1. **`job_helpers.py`** — No per-job scoring bug. `_run_score()` calls `score_one()` which bypasses batch pre-filter entirely; this is by design for single-job manual scoring. No `location_contains`/`exclude_location_contains` references in the file.

2. **Extraction/scoring ordering** — Extraction runs first (Phase 1), then the DB is re-read to pick up freshly extracted fields (Phase 2). Unextracted jobs reaching Tier-0 with `company_country="unknown"` pass through safely (Tier-0 treats unknown as "no restriction" for on-site/hybrid, and falls back to `geo_zone` for remote). No guard needed.

3. **Other consumers of pre_filter keys** — Only `score.py` merges `location_keywords` into `effective_pre_filter["location_contains"]`, and `settings.py` reads/writes `exclude_location_contains` in the Profile Editor. `profile_generator.py` also writes these keys but is a standalone tool. No scraper-side query builder depends on them. After removal from `get_jobs_for_scoring()`, the settings UI field should be relabeled as "legacy — no longer used by scoring."

---

## Phase 1 — Design

### Changes by Goal

#### Goal 1: Remove profile-sync leak (`profiles.py`)

**Current code** (lines 174-180):
```python
# Backfill scoring_context from the seed profile when the stored row
# predates the preference-model v3 field (onboarding gate depends on it).
if not profile.scoring_context.strip():
    seed = ALL_PROFILES.get(pid, ACTIVE_PROFILE)
    if seed and seed.scoring_context.strip():
        profile.scoring_context = seed.scoring_context
        db.upsert_profile(profile)
```

**Change**: Delete lines 174-180. This is the ONLY write-path into an existing DB profile row from code. The first-run seed path (lines 168-172, `if not row → upsert`) stays — that's the legitimate one-time bootstrap.

**Also**: Add a docstring/comment above `UNIFIED_JC` stating it's a one-time bootstrap seed with no runtime effect after first DB seed.

#### Goal 2: Remove SQL location pre-filter (`storage.py`, `score.py`, `settings.py`)

**storage.py** — Delete lines 1342-1362 (the `location_contains` and `exclude_location_contains` clause-building blocks). Title clauses (134-1374) stay.

**score.py** — Remove lines 354-358 (the `location_keywords` → `location_contains` merge). The `location_keywords` field remains on the profile for display/record-keeping but is no longer injected into `get_jobs_for_scoring()`.

**settings.py** — Relabel the `exclude_location_contains` textarea (line 642) with "(legacy — no longer used by scoring)" and update its help text. Keep the save logic (line 677) to preserve the DB value for record-keeping, but add a note that it no longer affects scoring.

#### Goal 3: Fix "Unscored" UI metric (`jobs.py`, `settings.py`)

Replace naive `total_jobs - scored_distinct` with `len(db.get_jobs_for_scoring(active_profile_id))`.

**jobs.py** (lines 50-55): Replace the raw SQL with a call to `get_jobs_for_scoring()`.
**settings.py** (lines 106-112): Same replacement.

#### Goal 4: Minor hardening (`jobs.py`, `settings.py`)

**Message flash** — The completion path (lines 82-107 in jobs.py, similar in settings.py) calls `st.success()`/`st.error()` then immediately `st.rerun()`. Restructure: store the message in `st.session_state`, `st.rerun()`, then display and clear it on the next pass.

**Re-extract timeout** — Wrap `subprocess.run(timeout=600)` in both files with `try/except subprocess.TimeoutExpired`.

#### Goal 5: Live→Dev DB snapshot (`scripts/sync_live_db.sh`)

New script. See [quickstart.md](quickstart.md) for usage.

### Data Model

No schema changes. No new fields. Existing `pre_filter` JSON blob in `search_profiles.criteria` continues to store `exclude_location_contains` — it just stops being read by scoring. See [data-model.md](data-model.md).

### Interface Contracts

No external interfaces. Internal: `get_jobs_for_scoring(pre_filter)` no longer recognizes `location_contains` or `exclude_location_contains` keys — they are silently ignored if passed.
