# Research: Monitored Companies Filter in Tracker

**Feature**: 012-monitored-companies-filter-in-tracker
**Date**: 2026-06-27

## Unknowns Resolution

No unknowns — all technical decisions are resolved in the spec:

1. **Filter location**: Python-side (not SQL). Spec explicitly says "No SQL change — filtering stays in Python per the existing pattern."
2. **Field availability**: `j.monitored_company_id` exists in `jobs` table (confirmed via schema inspection at `storage.py:917-921`), but is not currently selected in `get_all_for_tracker()`.
3. **UI placement**: Below existing Source multiselect, under a `st.divider()`, per spec.
4. **Widget type**: `st.selectbox` (not multiselect) — mutually exclusive choice.

## Decision: Add `monitored_company_id` to SELECT query

- **Rationale**: The field is already in the DB but not returned by `get_all_for_tracker()`. Adding it costs nothing and enables the filter.
- **Alternatives considered**: Filter in SQL via a new query parameter — rejected because spec mandates Python-side filtering to match existing pattern.

## Decision: Filter in `apply_filters()` in `shared.py`

- **Rationale**: All existing filters live in `shared.py:apply_filters()`. Consistency with existing pattern.
- **Alternatives considered**: Filter inline in `jobs.py` — rejected because it breaks the established pattern and would require signature tracking logic to be duplicated.
