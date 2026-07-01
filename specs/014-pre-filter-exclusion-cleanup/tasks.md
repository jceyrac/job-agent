# Tasks: Pre-filter exclusion cleanup (don't store non-PM jobs in DB)

**Input**: Design documents from `/specs/014-pre-filter-exclusion-cleanup/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: Not requested — no storage/model changes, so existing test suite is unaffected.

**Organization**: Single user story — this is a minimal, focused change in one file.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify prerequisites exist — no new project initialization needed.

- [x] T001 Verify `title_gate.py` is importable and `is_product_management_title()` passes its regression guard by running `python -c "from title_gate import is_product_management_title; print('OK')"`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Confirm the gate function handles edge cases correctly before integrating.

- [x] T002 Verify edge cases

**Checkpoint**: Gate function confirmed working. Ready for implementation.

---

## Phase 3: User Story 1 — PM title gate at scrape time (Priority: P1) 🎯 MVP

**Goal**: Non-PM jobs are discarded before DB write during broad scrape. Monitored company jobs
(in `_run_monitored_only()`) bypass the gate automatically since it's only added to `_run_broad_scrape()`.

**Independent Test**: Run `python scrape.py` and verify:
1. Non-PM titles produce "skipped (non-PM title)" output lines
2. PM titles pass through and are saved to DB
3. A summary line at the end shows the total count of skipped jobs

### Implementation for User Story 1

- [x] T003 [US1] Add `from title_gate import is_product_management_title` to imports in `scrape.py` (after the existing `from filters import JobFilterEngine` line)
- [x] T004 [US1] Add title gate check + counter in `_run_broad_scrape()` at `scrape.py:347` — inside the save loop, before `db.save_unscored()`
- [x] T005 [US1] Initialize `total_excluded_title = 0` near the other counters (`total_fetched`, `total_new`, `total_excluded_date`) at `scrape.py:315-317`
- [x] T006 [US1] Add summary print for title gate after the existing `total_excluded_date` summary

**Checkpoint**: Broad scrape filters non-PM titles. Monitored-only scrape is untouched.

---

## Phase 4: Polish & Validation

**Purpose**: Verify all acceptance criteria from spec.md.

- [x] T007 Run acceptance validation per quickstart.md: `python -c "import scrape"` passes cleanly
- [x] T008 Verify `_run_monitored_only()` is unchanged — `is_product_management_title` appears only at line 14 (import) and line 350 (`_run_broad_scrape()`)
- [x] T009 Verify no schema/storage changes — `git diff -- storage.py models.py` is empty

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1
- **User Story 1 (Phase 3)**: Depends on Phase 2
- **Polish (Phase 4)**: Depends on Phase 3

### Within User Story 1

- T003 (import) → T004 (gate logic, depends on import) → T005 (counter init) → T006 (summary print)
- T003 must be first; T004/T005/T006 are sequential within the same function

### Parallel Opportunities

- T001 and T002 are independent and could run in parallel (both are just Python one-liners)
- T008 and T009 in Phase 4 can run in parallel (different concerns)

---

## Implementation Strategy

### MVP (Single Change)

This entire feature is one logical change. Execute all tasks sequentially:

1. T001–T002: Verify prerequisites (30 seconds)
2. T003–T006: Implement the gate in `scrape.py` (5 minutes)
3. T007–T009: Validate (2 minutes)

### Notes

- The existing test suite (`python -m pytest tests/`) should still pass — this change touches only `scrape.py`, not `storage.py` or `models.py`
- No new dependencies, no new files, no DB migration
- Per constitution Principle V: surgical edit in one file only
