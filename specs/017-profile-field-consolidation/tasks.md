# Tasks: Spec 017 — Profile field consolidation

**Input**: Design documents from `/specs/017-profile-field-consolidation/`

**Prerequisites**: spec.md (required), plan.md (required)

**Tests**: No new test files required. Validation via existing `tests/test_storage.py` + mock regression.

---

## Phase 1: Foundational — Canonical profile fields (profiles.py)

**Goal**: `SearchProfile` gains canonical fields; legacy synthesis in `from_criteria`; seed updated; `scoring_context` prose edited. Every later phase depends on this.

**Independent Test**: `python -c "from profiles import SearchProfile, UNIFIED_JC; ..."` — round-trip + legacy synthesis works.

- [ ] T001 Add `job_titles`, `title_exclude`, `allowed_contract_types`, `languages_spoken` fields to `SearchProfile` dataclass in `profiles.py`
- [ ] T002 [P] Add deprecation comments above `scrape_titles`, `search_query_titles`, `scrape_exclude`, `excluded_languages`, `banned_countries` in `profiles.py`
- [ ] T003 [P] Add `effective_search_locations()` method to `SearchProfile` in `profiles.py`
- [ ] T004 Add new keys to `to_criteria_dict()` and legacy-synthesis logic to `from_criteria()` in `profiles.py`
- [ ] T005 Update `UNIFIED_JC` seed: `job_titles`, `title_exclude`, `allowed_contract_types`, `languages_spoken`, `excluded_sectors=[]`, `search_locations=[]`, `banned_countries=[]` in `profiles.py`
- [ ] T006 Update module docstring in `profiles.py`
- [ ] T007 Edit `scoring_context` seed prose: remove German/Spanish numeric cap, add soft sector line, keep large-corporate cap in `profiles.py`

**Checkpoint**: `from profiles import UNIFIED_JC; print(UNIFIED_JC.job_titles)` works.

---

## Phase 2: Pipeline reads canonical fields

**Goal**: Every pipeline touch-point derives from canonical fields instead of legacy/hardcoded sources.

**Independent Test**: `python score.py --mock --profile unified_jc` — all 11 cases in band.

### 2a. title_gate.py

- [ ] T008 [P] Parameterise `is_product_management_title()` signature with optional `titles` param, keeping `_PM_TITLES` fallback in `title_gate.py`

### 2b. scorer.py

- [ ] T009 Replace `excluded_languages` rule with positive `languages_spoken` rule at Tier-0 in `scorer.py`
- [ ] T010 [P] Add `allowed_contract_types` rule at Tier-0 position 4 in `scorer.py`
- [ ] T011 [P] Remove `banned_countries` Tier-0 block entirely in `scorer.py`

### 2c. score.py

- [ ] T012 Inject `title_contains` / `exclude_title_contains` into effective pre-filter from `job_titles` / `title_exclude` in `score.py`
- [ ] T013 [P] Replace digest language filter with positive `languages_spoken` form in `score.py`
- [ ] T014 [P] Add contract-type digest filter in `score.py`
- [ ] T015 Add 3 mock cases (USRemoteCo, BerlinBank, InternCo) + expectations in `score.py`

### 2d. scrapers/greenhouse.py

- [ ] T016 [P] Remove `PM_TITLE_KEYWORDS` and `_is_pm_title()`; import `is_product_management_title` from `title_gate`; pass `profile.job_titles` in `scrapers/greenhouse.py`
- [ ] T017 [P] Remove hardcoded `base_location == "United States"` filter in `scrapers/greenhouse.py`

### 2e. scrapers/boards/linkedin.py + indeed.py

- [ ] T018 [P] Replace `search_query_titles` with `job_titles` as search terms in `scrapers/boards/linkedin.py`
- [ ] T019 [P] Replace `search_locations` with `effective_search_locations()` in `scrapers/boards/linkedin.py`
- [ ] T020 [P] Replace `search_query_titles` with `job_titles` as search terms in `scrapers/boards/indeed.py`
- [ ] T021 [P] Replace `search_locations` with `effective_search_locations()` (keeping `_to_indeed_slug()`) in `scrapers/boards/indeed.py`

### 2f. scrape.py

- [ ] T022 Build `JobFilter` from `profile.job_titles` / `profile.title_exclude` in `scrape.py`
- [ ] T023 Pass `profile.job_titles` to `is_product_management_title()` per-job gate call in `scrape.py`

### 2g. filters.py

- [ ] T024 [P] Remove dead `company_sizes`, `contract_types`, `allowed_geo_zones` branches; preserve date filter + return arity in `filters.py`

**Checkpoint**: `python score.py --mock --profile unified_jc` passes all 11 cases.

---

## Phase 3: Settings UI reflects canonical fields

**Goal**: Profile editor shows only canonical inputs; deprecated inputs removed; save block updated.

**Independent Test**: Settings save → `sqlite3 data/jobs.db "SELECT criteria FROM search_profiles"` contains `job_titles` and `languages_spoken`.

- [ ] T025 Remove "Search query titles", "Scrape titles", `pre_filter: title_contains` / `exclude_title_contains` widgets in `tracker_views/settings.py`
- [ ] T026 [P] Add `job_titles` and `title_exclude` text areas in Search block in `tracker_views/settings.py`
- [ ] T027 [P] Add `allowed_contract_types` multiselect near work modes / company sizes in `tracker_views/settings.py`
- [ ] T028 [P] Replace `excluded_languages` text area with `languages_spoken` text area in `tracker_views/settings.py`
- [ ] T029 [P] Remove "Banned countries" text area in `tracker_views/settings.py`
- [ ] T030 Relabel `search_locations` to indicate it's an override (empty = derive from geography) in `tracker_views/settings.py`
- [ ] T031 Rename "🕸 Scrape net & advanced" expander to "🕸 Advanced scrape inputs" in `tracker_views/settings.py`
- [ ] T032 Update `st.form_submit_button` save block: assign canonical fields, remove deprecated/removed field assignments in `tracker_views/settings.py`

**Checkpoint**: Settings UI shows one title input, no banned countries, new contract type multiselect.

---

## Phase 4: Validate

**Goal**: All acceptance criteria verified.

- [ ] T033 Run `python -m pytest tests/test_storage.py` — 145 tests pass
- [ ] T034 Run `python score.py --mock --profile unified_jc` — all 11 cases in band
- [ ] T035 Run legacy synthesis round-trip: `from_criteria` with old keys → canonical fields present
- [ ] T036 Verify `effective_search_locations()` returns union of work_mode_geography countries when `search_locations` is empty

---

## Dependencies & Execution Order

```
Phase 1 (T001–T007) ── BLOCKS ──> Phase 2 (T008–T024) ──> Phase 3 (T025–T032) ──> Phase 4 (T033–T036)
                                        │
                                        └── T008, T009/T010/T011, T016/T017, T018-T021, T024 can run in parallel
                                            within Phase 2 (they touch different files)
```

**Within Phase 2**, the `score.py` tasks (T012–T015) depend on `scorer.py` (T009–T011) being done first.

## Parallel Opportunities

- T002, T003 can run together (different methods in same file — sequential safer)
- T009, T010, T011 can run together (same function block — sequential safer)
- T016, T017 can run together (same file)
- T018, T019 (linkedin.py) and T020, T021 (indeed.py) can run in parallel
- T024 is independent of all other Phase 2 tasks
- T026, T027, T028, T029, T030 in Phase 3 can run in parallel (different widgets in same form — sequential safer)

## Implementation Strategy

Since this is a solo project, follow the sequential phase order. Each phase is a commit checkpoint.

1. Phase 1 → commit → verify
2. Phase 2 → commit → verify mock
3. Phase 3 → commit → verify UI
4. Phase 4 → final validation → push
