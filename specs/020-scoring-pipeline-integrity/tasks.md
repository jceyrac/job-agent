# Tasks: Scoring Pipeline Integrity

**Input**: Design documents from `specs/020-scoring-pipeline-integrity/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Note**: This is a corrective/integrity spec, not a greenfield feature. "User Stories" below map to the spec's 5 Goals. No test tasks — the spec's acceptance criteria are validated via quickstart.md.

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which goal this task belongs to (US1, US2, US3, US4, US5)

---

## Phase 1: Setup (Verification Prerequisites)

**Purpose**: Confirm the investigation findings before making changes

- [ ] T001 [P] Confirm `job_helpers.py` has no `location_contains`/`exclude_location_contains` references (grep verification) — see research.md investigation 1
- [ ] T002 [P] Confirm `score.py` extraction→re-read→evaluation ordering is safe for unextracted jobs reaching Tier-0 — see research.md investigation 2
- [ ] T003 [P] Confirm no additional consumers of `pre_filter` location keys beyond `score.py:354-358` and `settings.py:642-677` — see research.md investigation 3

**Checkpoint**: All three investigation items confirmed. The planned changes are safe.

---

## Phase 2: User Story 1 — Remove SQL Location Pre-Filter (Priority: P1) 🎯

**Goal**: Remove the `location_contains` / `exclude_location_contains` SQL clauses from `get_jobs_for_scoring()`, eliminating the fragile substring-based location pre-filter that silently excluded legitimate candidates (e.g. "Lausanne" matching "usa").

**Independent Test**: Run `python score.py --profile unified_jc` on the synced Live DB snapshot — candidate count > 0 (previously 0 because "usa" matched "Lausanne").

### Implementation for US1

- [ ] T004 [US1] Delete `location_contains` clause block (lines 1342-1352) in `storage.py::get_jobs_for_scoring()`
- [ ] T005 [US1] Delete `exclude_location_contains` clause block (lines 1354-1362) in `storage.py::get_jobs_for_scoring()`
- [ ] T006 [US1] Remove `location_keywords` → `pre_filter.location_contains` merge (lines 354-358) in `score.py`
- [ ] T007 [US1] Relabel `exclude_location_contains` textarea field (line 642) in `tracker_views/settings.py` — label to "pre_filter: exclude_location_contains (legacy — no longer used by scoring)", update help text to note it's preserved for record-keeping only
- [ ] T008 [US1] Run `python -m pytest tests/test_storage.py` — confirm 100% pass after removing SQL clauses

**Checkpoint**: SQL location pre-filter is gone. Tier-0 in `scorer.py` is the sole geography gate. Candidate pool is no longer silently collapsed.

---

## Phase 3: User Story 2 — Remove Profile-Sync Leak (Priority: P1) 🎯

**Goal**: Remove the `scoring_context` backfill code path in `load_active_profile()` — once a profile row exists in the DB, code must never silently overwrite it from `ALL_PROFILES`/`UNIFIED_JC`.

**Independent Test**: Manually edit `scoring_context` in the DB to a test value, restart tracker, confirm it persists (not overwritten by `profiles.py` code). See quickstart.md Step 2.

### Implementation for US2

- [ ] T009 [US2] Delete `scoring_context` backfill block (lines 174-180) in `profiles.py::load_active_profile()`
- [ ] T010 [US2] Add docstring comment above `UNIFIED_JC` (line 191) in `profiles.py` stating: "One-time bootstrap seed. Consulted exactly once when a profile_id has no existing DB row. Once seeded, all further edits go through the Settings UI or direct DB write. Editing this file after first run has no effect on a running instance."

**Checkpoint**: DB is the sole runtime source of truth after first seed. No code path silently overwrites an existing profile row.

---

## Phase 4: User Story 3 — Fix "Unscored" UI Metric (Priority: P2)

**Goal**: Replace the naive `total_jobs - scored_distinct` count with `len(get_jobs_for_scoring(active_profile_id))` so the displayed "Unscored" number matches what "Run scoring" actually attempts.

**Independent Test**: UI metric matches `len(db.get_jobs_for_scoring(profile.id))`. See quickstart.md Step 4.

### Implementation for US3

- [ ] T011 [US3] Replace unscored computation (lines 50-55) in `tracker_views/jobs.py::_render_controls_bar()` — use `len(db.get_jobs_for_scoring(active_profile_id))` instead of `total_jobs - scored_distinct`
- [ ] T012 [US3] Replace unscored computation (lines 106-112) in `tracker_views/settings.py::_render_run_controls()` — same change as T011

**Checkpoint**: Both pages show an "Unscored" count that matches what scoring will actually process.

---

## Phase 5: User Story 4 — Minor Hardening (Priority: P2)

**Goal**: Fix two UI robustness issues: (a) background process success/error messages that flash invisibly because `st.rerun()` follows immediately, and (b) the Re-extract button crashing on >10min runs because `subprocess.run(timeout=600)` lacks a `TimeoutExpired` handler.

**Independent Test**: Manually exercise scrape/score completion and Re-extract button. See quickstart.md Step 6.

### Implementation for US4

- [ ] T013 [US4] Fix message flash in `tracker_views/jobs.py::_render_controls_bar()` (lines 82-107) — store completion message in `st.session_state.bg_result` before clearing process state, then render it on the NEXT pass (after `st.rerun()`) from `st.session_state.bg_result` and clear it
- [ ] T014 [US4] Fix message flash in `tracker_views/settings.py::_render_run_controls()` (lines 157-184) — same approach as T013: `st.session_state.bg_result` for one-render persistence
- [ ] T015 [P] [US4] Wrap Re-extract `subprocess.run(timeout=600)` (lines 126-128) in `tracker_views/jobs.py` with `try/except subprocess.TimeoutExpired` — show `st.error("Re-extract timed out after 10 minutes — it may still be running in the background.")`
- [ ] T016 [P] [US4] Wrap Re-extract `subprocess.run(timeout=600)` (lines 791-793) in `tracker_views/settings.py::_render_stats_actions()` with same `try/except subprocess.TimeoutExpired` as T015

**Checkpoint**: Users see completion messages for one full render. Re-extract gracefully handles timeouts.

---

## Phase 6: User Story 5 — Live→Dev DB Snapshot Script (Priority: P3)

**Goal**: Create a script to pull a read-only copy of the Live DB over Tailscale for safe local testing.

**Independent Test**: Run `bash scripts/sync_live_db.sh`, verify `data/jobs_live_snapshot.db` is created, swap into `data/jobs.db`, launch tracker — no migration errors. See quickstart.md Step 1.

### Implementation for US5

- [ ] T017 [US5] Create `scripts/sync_live_db.sh` — uses `scp`/`rsync` to pull the live SQLite file from verva (`100.74.139.28`) to `data/jobs_live_snapshot.db`, prints manual swap-in instructions, and warns about one-way direction (Live→Dev only)
- [ ] T018 [US5] Verify the script works: `bash scripts/sync_live_db.sh`, confirm `data/jobs_live_snapshot.db` is non-empty, swap in per instructions, launch `streamlit run tracker.py` — confirm no migration errors

**Checkpoint**: Safe, documented path to test against real production data on Dev.

---

## Phase 7: Validation & Cleanup

**Purpose**: End-to-end validation per the spec's testing plan and quickstart.md.

- [ ] T019 Run FELFEL regression — confirm the FELFEL job still scores 6–8 for `unified_jc` after all changes
- [ ] T020 Run quickstart.md end-to-end validation (all 7 steps) — confirm all acceptance criteria from spec.md pass
- [ ] T021 Run `python -m pytest tests/test_storage.py` — confirm 100% pass (no regressions)
- [ ] T022 [P] Review all changes for surgical scope — confirm no opportunistic refactors, no modified files outside the 4 targets + 1 new script

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — verification only
- **Phase 2 (US1 — SQL location pre-filter)**: Depends on Phase 1 confirmation
- **Phase 3 (US2 — Profile sync)**: Independent of Phase 2 — can run in parallel
- **Phase 4 (US3 — UI metric)**: Depends on Phase 2 (uses `get_jobs_for_scoring()` after location clause removal)
- **Phase 5 (US4 — Hardening)**: Independent — touches same UI files but different code paths
- **Phase 6 (US5 — DB script)**: Fully independent — can run at any time
- **Phase 7 (Validation)**: Depends on all implementation phases complete

### Story Dependencies

- **US1 (P1)**: Starts after Phase 1 — no other story dependencies
- **US2 (P1)**: Starts after Phase 1 — fully independent of US1 (touches `profiles.py` only)
- **US3 (P2)**: Depends on US1 completion (uses modified `get_jobs_for_scoring()`)
- **US4 (P2)**: Independent of all stories (touches same files but different logic)
- **US5 (P3)**: Fully independent (new file only)

### Parallel Opportunities

- **Phase 2 + Phase 3**: US1 and US2 can run in parallel (different files, no shared code paths)
- **Phase 5 (T015, T016)**: Both Re-extract timeout fixes can run in parallel
- **US5 (T017)**: Can run at any time, completely independent
- **T019, T020, T021, T022**: All validation tasks can run in parallel

---

## Parallel Example: Core Fixes (US1 + US2)

```bash
# These two phases touch completely different files and can run concurrently:
# US1: storage.py, score.py, settings.py (location clauses)
Task: T004-T008 — Remove SQL location pre-filter

# US2: profiles.py (backfill removal + docstring)
Task: T009-T010 — Remove profile-sync leak
```

---

## Implementation Strategy

### Recommended Order (Solo Developer)

1. **Phase 1 Setup** (T001-T003): Quick confirmation (~2 min)
2. **Phase 3 US2** (T009-T010): One file, 2 edits — fastest win
3. **Phase 2 US1** (T004-T008): Three files, core fix
4. **Phase 4 US3** (T011-T012): Two files, metric fix (depends on US1)
5. **Phase 5 US4** (T013-T016): Two files, hardening
6. **Phase 6 US5** (T017-T018): New script (do anytime)
7. **Phase 7 Validation** (T019-T022): End-to-end verification

### MVP Scope

**MVP = US1 + US2** (Phases 2 + 3). These are the two root-cause fixes that resolve the production bug (0 candidates on scoring run). US3-5 add transparency, robustness, and infrastructure.

### File Edit Summary

| File | Tasks | Lines Changed |
|------|-------|---------------|
| `profiles.py` | T009, T010 | ~7 deleted, +3 comment |
| `storage.py` | T004, T005 | ~22 deleted |
| `score.py` | T006 | ~5 deleted |
| `tracker_views/settings.py` | T007, T012, T014, T016 | ~8 lines changed |
| `tracker_views/jobs.py` | T011, T013, T015 | ~12 lines changed |
| `scripts/sync_live_db.sh` | T017, T018 | new file (~40 lines) |

---

## Notes

- No test tasks — the spec's validation is empirical (FELFEL regression, UI behavior)
- All changes are deletions or targeted replacements — no new abstractions
- `storage.py` is listed as "NEVER modify" in CLAUDE.md, but this spec explicitly authorizes it for Goal 2
- Run `python -m pytest tests/test_storage.py` after any `storage.py` change (T008, T021)
- Check `settings.py`'s `_render_run_controls` vs `_render_stats_actions` — these are separate sections; ensure the metric fix (T012) targets the right location
