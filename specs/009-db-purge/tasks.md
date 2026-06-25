# Tasks: Automatic DB Purge (stale jobs cleanup)

**Input**: Design documents from `specs/009-db-purge/`
**Prerequisites**: plan.md ✅, spec.md ✅, clarification ✅

**Tests**: Unit tests for new storage methods (per project convention: always update tests with code changes)

**Organization**: Single feature — no user story breakdown. Tasks grouped by component.

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

---

## Phase 1: Storage methods

**Purpose**: Core DB logic — `purge_stale_jobs()` and `count_purgeable_jobs()`

- [ ] T001 Add `purge_stale_jobs(retention_days: int = 30) -> int` method to `JobStorage` in `storage.py` — transactional DELETE across status_history, job_scores, job_tracking, jobs
- [ ] T002 Add `count_purgeable_jobs(retention_days: int = 30) -> int` method to `JobStorage` in `storage.py` — same subquery as T001 but SELECT COUNT(*) only

---

## Phase 2: Pipeline integration

**Purpose**: Call purge at the start of each pipeline run

- [ ] T003 Add purge call at start of `main()` in `main.py` — read `purge_retention_days` config, call `db.purge_stale_jobs()`, log result

---

## Phase 3: Settings widget

**Purpose**: Retention configuration + live preview in tracker UI

- [ ] T004 Add retention number_input + live count preview + save button in `tracker_views/settings.py` (Pipeline/Scraper section)

---

## Phase 4: Tests

**Purpose**: Verify both new methods work correctly (in-memory DB)

- [ ] T005 [P] Add `test_purge_stale_jobs_removes_untouched_old_jobs` in `tests/test_storage.py`
- [ ] T006 [P] Add `test_purge_stale_jobs_preserves_jobs_with_notes` in `tests/test_storage.py`
- [ ] T007 [P] Add `test_purge_stale_jobs_preserves_saved_applied_rejected` in `tests/test_storage.py`
- [ ] T008 [P] Add `test_count_purgeable_jobs_matches_purge_count` in `tests/test_storage.py`
- [ ] T009 [P] Add `test_purge_stale_jobs_transactional_rollback` in `tests/test_storage.py`

---

## Phase 5: Verify

**Purpose**: Run against dev DB and validate

- [ ] T010 Run `python main.py` on dev DB, verify purge log appears and count matches `count_purgeable_jobs(30)`
- [ ] T011 Open tracker Settings page, verify retention widget shows live count, change value, save, verify persisted

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Storage)**: No dependencies — can start immediately
- **Phase 2 (main.py)**: Depends on T001 (needs `purge_stale_jobs()`)
- **Phase 3 (Settings)**: Depends on T002 (needs `count_purgeable_jobs()`)
- **Phase 4 (Tests)**: Depends on T001, T002 (needs both methods)
- **Phase 5 (Verify)**: Depends on all preceding phases

### Parallel Opportunities

- T001 + T002 can be implemented in sequence (same file, same section)
- T005 → T009 can all run in parallel (different test functions, same file)
- T003 and T004 can run in parallel (different files, different concerns)

---

## Parallel Example

```bash
# After Phase 1 is done, launch Phase 2 + 3 together:
Task: "Add purge call in main.py"
Task: "Add retention widget in settings.py"

# Phase 4: all tests in parallel:
Task: "test_purge_stale_jobs_removes_untouched_old_jobs"
Task: "test_purge_stale_jobs_preserves_jobs_with_notes"
Task: "test_purge_stale_jobs_preserves_saved_applied_rejected"
Task: "test_count_purgeable_jobs_matches_purge_count"
Task: "test_purge_stale_jobs_transactional_rollback"
```

---

## Implementation Strategy

1. Complete Phase 1: Both storage methods
2. Complete Phase 2: main.py integration
3. Complete Phase 3: Settings widget
4. Complete Phase 4: All tests (target: 140 → 145 passing)
5. **STOP and VALIDATE**: Run `python main.py` on dev DB, check tracker Settings
6. Commit and push

---

## Notes

- All tasks preserve existing code — no refactors
- No new files — only modifications to `storage.py`, `main.py`, `tracker_views/settings.py`
- Tests use in-memory DB (`JobStorage(":memory:")`) — existing pattern
- `main.py` import is already available: `from storage import JobStorage`
- The dev DB reference metrics (spec): ~2,301 purgeable jobs at retention=30, ~2,810 with retention=0
