# Implementation Plan: Monitored Companies Filter in Tracker

**Branch**: `012-monitored-companies-filter` | **Date**: 2026-06-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/012-monitored-companies-filter-in-tracker/spec.md`

## Summary

Add a "Source type" selectbox to the Jobs tab sidebar that filters jobs by their origin: monitored companies (direct ATS board scraping, `monitored_company_id IS NOT NULL`) vs. job boards only (`monitored_company_id IS NULL`). Pure Python-side filter — no SQL changes, no schema changes.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: Streamlit (UI), SQLite via JobStorage (data)
**Storage**: SQLite — `data/jobs.db`, `monitored_company_id` column already exists in `jobs` table
**Testing**: pytest in `tests/` — no new tests needed (UI-only change)
**Target Platform**: Streamlit tracker (browser UI)
**Project Type**: web application (Streamlit multi-page)
**Performance Goals**: No change — filter is O(n) on already-loaded job list
**Constraints**: No SQL changes, no new dependencies, filter composes with all existing sidebar filters
**Scale/Scope**: 3 files touched, ~20 lines of code

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Filet large, point de filtrage unique | ✅ PASS | Filter is UI-only, post-hoc; scorer remains sole decision point |
| II. Deux chemins d'amélioration | ✅ PASS | This is a Code change (UI + query), tested before deploy |
| III. Profil unifié unique | ✅ PASS | Filter is profile-independent |
| IV. Structure déterministe | ✅ PASS | Filter uses deterministic `IS NULL`/`IS NOT NULL` |
| V. Modification chirurgicale | ✅ PASS | ~20 lines across 3 files, no refactoring |
| VI. Validation empirique | ✅ PASS | Manual verification in tracker UI |
| VII. Sécurité d'abord | ✅ PASS | No new secrets, no new network egress |
| VIII. Scrapers par modèle d'acquisition | ✅ PASS | Filter aligns with the board vs. company-keyed distinction |
| IX. Scoring couche optionnelle | ✅ PASS | Filter works on unscored jobs |

**Gate**: ✅ ALL PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/012-monitored-companies-filter-in-tracker/
├── spec.md              # Feature specification (already exists)
├── plan.md              # This file
└── tasks.md             # Implementation tasks (next step)
```

### Source Code (changes)

```text
storage.py               # Line ~3085: Add j.monitored_company_id to SELECT in get_all_for_tracker()
tracker_views/jobs.py    # Lines ~218-224: Add source_type selectbox in sidebar, wire to apply_filters
tracker_views/shared.py  # Lines ~265-282: Add source_type_filter param to apply_filters()
```

**Structure Decision**: Existing project structure — no new files or directories.

## Complexity Tracking

No violations — no complexity to justify.

## Implementation Notes

### Change 1: `storage.py` — `get_all_for_tracker()`

Add `j.monitored_company_id` to the SELECT list (line ~3085). Current query selects `j.id, j.company_id, j.title, ...` but omits `monitored_company_id`. Adding it makes the field available in returned dicts for Python-side filtering.

### Change 2: `tracker_views/jobs.py` — sidebar widget

Add a `st.selectbox` below the existing Source multiselect (line 218), under a `st.divider()`:

```python
source_type = st.selectbox(
    "Source type",
    options=["All", "Monitored companies", "Job boards only"],
    index=0,
    key="jobs_source_type",
)
```

Pass `source_type` to `apply_filters()` and include it in the filter signature for page reset.

### Change 3: `tracker_views/shared.py` — `apply_filters()`

Add `source_type_filter: str = "All"` parameter. Apply after existing filters:

```python
if source_type_filter == "Monitored companies":
    result = [j for j in result if j.get("monitored_company_id") is not None]
elif source_type_filter == "Job boards only":
    result = [j for j in result if j.get("monitored_company_id") is None]
```

The filter follows the existing pattern of `j.get()` with safe defaults.
