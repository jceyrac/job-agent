# Tasks: Monitored Companies Filter in Tracker

**Feature**: 012-monitored-companies-filter-in-tracker
**Date**: 2026-06-27

## Phase 1: Storage — Expose monitored_company_id

- [ ] T001 Add `j.monitored_company_id` to SELECT in `get_all_for_tracker()` at `storage.py:3086`

## Phase 2: Filter logic — apply_filters()

- [ ] T002 [P] Add `source_type_filter: str = "All"` parameter to `apply_filters()` signature in `tracker_views/shared.py:265`
- [ ] T003 [P] Add source_type filtering block in `apply_filters()` body in `tracker_views/shared.py` (after existing source_filter, before status_filter)

## Phase 3: UI — Sidebar widget in Jobs tab

- [ ] T004 Add `st.divider()` and `st.selectbox` for Source type below existing Source multiselect in `tracker_views/jobs.py:218`
- [ ] T005 Pass `source_type` to `apply_filters()` call in `tracker_views/jobs.py:226`
- [ ] T006 Add `source_type` to filter signature tuple in `tracker_views/jobs.py:245` for page reset

## Dependencies

```text
T001 ──► T002, T003 ──► T004, T005, T006
```

T001 first (data must be available), then T002/T003 in parallel (filter logic), then T004-T006 (UI wiring).

## Validation

Follow `quickstart.md` scenarios: default "All", "Monitored companies", "Job boards only", filter composition, regression.
