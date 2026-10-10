# Tasks: Remove Dead Files (spec 035, roadmap step 1d)

**Input**: Design documents from `specs/035-remove-dead-files/`

**Prerequisites**: `plan.md`, `spec.md` (clarified), `research.md`, `quickstart.md`

**Tests**: No new test code. The existing root-module guard in
`tests/test_installable_project.py` is **modified** (allow-list removed) and acts as the
regression net (FR-002); the full existing suite is the rest of the net.

**Organization**: Grouped by user story — US1 (deletion), US2 (release), both P1, in commit order.

## Format: `[ID] [P?] [Story] Description`

- **[P]** = parallelizable (different files, no dependency on another task)
- **[Story]** = US1 / US2

---

## Phase 1: Setup

**Purpose**: Record the green baseline before any change.

- [X] T001 Run `python -m pytest tests/` and confirm the full suite is green; note the count. Confirm the six target files exist (`ls *.py` shows the five `.py` one-offs plus `tracker.py`; `ls CONTEXT.md`).

---

## Phase 2: User Story 1 — A root that only contains what is used (Priority: P1) 🎯 MVP

**Goal**: The repo root contains only `tracker.py` (plus non-Python project files); the
root-module guard has an empty allow-list and still passes; the suite is green.

**Independent Test**: `ls *.py` lists only `tracker.py`; the guard fails on a planted stray
root `.py`; the full suite is green.

### Implementation (single commit, T007)

- [X] T002 [US1] `git rm` the six files: `migrate_expired_status.py`, `migrate_single_status.py`, `migrate_profile_independent_tracking.py`, `tracker_legacy.py`, `test_wellfound.py`, `CONTEXT.md` (FR-001, FR-005).
- [X] T003 [P] [US1] Update `tests/test_installable_project.py`: remove the one-off allow-list of the five root modules so the root-module guard allows only `tracker.py` (FR-002, SC-001).
- [X] T004 [P] [US1] Update `CLAUDE.md`: remove the two bullets referencing the removed files — the NEVER-modify bullet "Migration files (`migrate_*.py`)" and the Safe-to-modify bullet "`tracker_legacy.py` — legacy reference" (FR-003).
- [X] T005 [P] [US1] Create `docs/history.md` (FR-004, SC-003): for each of the six removed files, a line with name, one-line purpose, the last commit containing it, and the retrieval command `git show <sha>:<path>`.
- [X] T006 [US1] Validate (SC-001/002/003): `ls *.py` → only `tracker.py`; `python -m pytest tests/` green; negative fixture — temporarily add a stray root `.py`, confirm the guard fails, then remove it; `git show` retrieves one removed file using the SHA from `docs/history.md`.
- [X] T007 [US1] Commit US1 on `035-remove-dead-files` (single commit): `chore(spec-035): remove six dead files, drop root-module allow-list, add docs/history.md`.

**Checkpoint**: `python -m pytest tests/` green; root has only `tracker.py`; exactly one commit (T007).

---

## Phase 3: User Story 2 — Released through the standard flow, no behaviour change (Priority: P1)

**Goal**: Validate on staging on the exact SHA, then ff-only merge and deploy; the live
tracker, cron and CV agent behave exactly as before (no runtime code changed).

**Independent Test**: Staging on the candidate SHA passes health + parity + feed look; after
merge + deploy, health and the next scheduled cron are green.

### Implementation (deploy-gated, on verva — see `docs/migration-checklist.md`)

- [X] T008 [US2] `git push` `035-remove-dead-files`; on verva `scripts/staging.sh up <full-sha>`. Confirm the staging tracker on :8502 serves the backup copy; health OK; parity fingerprint identical (candidate vs prod image, same backup copy, same `--as-of`); a quick look at the feed renders. **STOP — hand over for my quick look.**
- [X] T009 [US2] `scripts/staging.sh down` (verify no staging container, image, volume or worktree remains). **STOP — hand over for my go.**
- [X] T010 [US2] After the go: ff-only merge of the SHA onto `main` (`git merge --ff-only <full-sha>`; refuse non-ff).
- [X] T011 [US2] Run the deploy command (`./scripts/deploy.sh` on verva — hand over for me to run); then verify `git -C /opt/job-agent rev-parse HEAD` equals the merged SHA and the health check is OK.
- [X] T012 [US2] Next scheduled cron: the latest `runs` row (`run_type='full'`) has `status='success'` and `ran_at` from last night.
- [X] T013 [US2] Closing commit on `main` recording the release (docs).

**Checkpoint**: deployed SHA = staged SHA; health OK; cron green; tracker fully operational.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no deps (T001 baseline).
- **US1 (Phase 2)**: after Setup; one commit.
- **US2 (Phase 3)**: after US1; deploy-gated on the user's go.

### Within US1

- T002 (`git rm`) first; T003/T004/T005 are parallel (disjoint files) after T002.
- T006 (validate) last, then T007 (commit).

### Parallel Opportunities

```text
T003 (test_installable_project.py) ∥ T004 (CLAUDE.md) ∥ T005 (docs/history.md)  — disjoint files
```

---

## Implementation Strategy

### MVP First (US1 only)

1. T001 baseline → T002 `git rm` → T003–T005 (parallel) → T006 validate → T007 commit.
2. **STOP and VALIDATE**: `ls *.py` = `{tracker.py}`; guard negative fixture fails; suite
   green. This is the whole change — nothing else moves.

### Incremental Delivery

1. US1 → suite green, root clean. **Commit (T007).**
2. US2 → staging on the exact SHA → ff-only merge → deploy → SHA check → cron green. **One release.**

---

## Notes

- **Stable core**: no `core/` module is touched. The two `CLAUDE.md` bullets and the
  allow-list in `tests/test_installable_project.py` are the only non-`git rm` code changes;
  `docs/history.md` is new documentation.
- `git rm` (not `archive/`): git history preserves the files; `docs/history.md` records the
  last commit containing each so it stays retrievable (FR-004, SC-003).
- `prompts/` is historical by declaration and is **not** rewritten (spec non-goal).
- The schema migrations inside `core/storage.py` are **not** removed (spec non-goal).
- Commit T007 ends with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
