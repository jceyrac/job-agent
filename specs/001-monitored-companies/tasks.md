# Tasks: Monitored Companies

**Input**: Design documents from `specs/001-monitored-companies/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅

**Tests**: Unit tests for `storage.py` changes are REQUIRED per CLAUDE.md (run before any commit touching storage.py). Integration tests for ATS adapters are manual validation per quickstart.md.

**Organization**: Tasks are grouped by plan phase (A→E) within user story priority order. Each phase is independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create scraper sub-package structure — no logic changes, just file moves.

- [ ] T001 Create `scrapers/boards/__init__.py` and `scrapers/ats/__init__.py` and `scrapers/company_sites/__init__.py` with package docstrings, preserving `scrapers/base.py` at its current location
- [ ] T002 [P] Move all aggregation board scrapers into `scrapers/boards/`: `linkedin.py`, `indeed.py`, `_jobspy_helpers.py`, `web3career.py`, `remoteok.py`, `weworkremotely.py`, `cryptojobslist.py`, `cryptojobs_com.py`, `defi_jobs.py`, `tietalent.py`, `jobup.py`, `wellfound.py`. Update relative imports to `from scrapers.base import BaseScraper`.
- [ ] T003 [P] Update `scrape.py` scraper discovery (`discover_scrapers()`) to scan `scrapers/boards/`, `scrapers/ats/`, and `scrapers/company_sites/` sub-packages. Verify `python scrape.py --list` shows all existing scrapers.

**Checkpoint**: All existing scrapers still work from new locations. `python scrape.py` produces identical output to pre-refactor.

---

## Phase 2: Foundational — Data Model (Plan Phase A, blocks all user stories)

**Purpose**: Extend database schema. All storage changes go through `storage.py`. Must run 162 tests green before proceeding.

### Tests for Data Model

- [ ] T004 [P] Add test cases for companies new columns (careers_url, ats_provider, ats_identifier, scraper_id, monitored, detected_at) in `tests/test_storage.py`
- [ ] T005 [P] Add test cases for jobs new columns (monitored_company_id FK, filtered_non_product) in `tests/test_storage.py`
- [ ] T006 [P] Add test cases for migrations table (create, idempotent re-run, duplicate detection) in `tests/test_storage.py`
- [ ] T007 [P] Add test cases for pipeline_runs.run_type in `tests/test_storage.py`

### Implementation

- [ ] T008 Create `migrations` table in `storage.py` (`JobStorage._ensure_migrations_table()`): columns id, name (UNIQUE), applied_at. Add `_migrate()` method that runs pending migrations idempotently, called from `__init__`.
- [ ] T009 Extend `companies` table in `storage.py` (`JobStorage._ensure_companies_table()`): add `careers_url TEXT`, `ats_provider TEXT`, `ats_identifier TEXT`, `scraper_id TEXT`, `monitored BOOLEAN DEFAULT FALSE`, `detected_at TIMESTAMP`. Register as migration "add_monitored_companies".
- [ ] T010 Add `monitored_company_id INTEGER REFERENCES companies(id)` (nullable) to `jobs` table in `storage.py` (`JobStorage._ensure_jobs_table()`). Register as migration "add_monitored_company_id".
- [ ] T011 Add `filtered_non_product BOOLEAN DEFAULT FALSE` to `jobs` table in `storage.py`. Register as migration "add_filtered_non_product".
- [ ] T012 Add `run_type TEXT DEFAULT 'full'` to `pipeline_runs` table in `storage.py`. Values: `full` or `monitored_only`. Register as migration "add_pipeline_run_type".
- [ ] T013 Add `JobStorage` methods for company monitoring: `set_company_monitored(company_id, monitored: bool)`, `get_monitored_companies() -> list[dict]`, `is_company_scrapable(company_id) -> bool`, `get_companies_by_ats_provider(provider) -> list[dict]`.
- [ ] T014 Add `JobStorage` methods for provenance: `set_job_monitored_company(job_id, company_id)`, `set_job_filtered_non_product(job_id)`, `get_jobs_by_monitored_status(monitored: bool)`.
- [ ] T015 Add `JobStorage` method `upsert_company_from_job(company_name)` — called at scrape time to auto-create company rows from job postings (without scrape_method, not yet monitorable).
- [ ] T016 Backfill `companies` table from historical jobs in new migration "backfill_companies_from_jobs": SELECT DISTINCT company FROM jobs, INSERT INTO companies (name) for each not already present.
- [ ] T017 Seed ~30 Greenhouse boards from current `CRYPTO_WEB3_BOARDS` constant into `companies` table in new migration "seed_greenhouse_boards": INSERT companies with `ats_provider='greenhouse'`, `ats_identifier=<token>`, `monitored=true` for each board in the current constant.
- [ ] T018 Run `python -m pytest tests/test_storage.py -q` — all tests must pass (existing 162 + new ones).

**Checkpoint**: DB schema extended. Companies table has seed data. All storage tests green. Ready to build on top.

---

## Phase 3: User Story 1 & 2 — Toggle Monitoring + Monitoring-Only Run (Plan Phase B, P1)

**Goal**: User can mark a company as monitored and run `--monitored-only` to collect its openings. Greenhouse adapter drives this first pass.

**Independent Test**: Run `python scrape.py --monitored-only` with seeded Greenhouse boards, verify openings appear in DB with `monitored_company_id` set.

### Tests for User Story 1 & 2

- [ ] T019 [P] [US1] [US2] Add test for `set_company_monitored()` toggle and `get_monitored_companies()` in `tests/test_storage.py`
- [ ] T020 [P] [US1] [US2] Add test for `is_company_scrapable()` logic (ats_provider IS NOT NULL OR scraper_id IS NOT NULL) in `tests/test_storage.py`

### Implementation

- [ ] T021 [US2] Convert Greenhouse scraper from constant-driven to DB-driven: in `scrapers/ats/greenhouse.py`, replace `CRYPTO_WEB3_BOARDS` import with a `fetch_targets(db: JobStorage)` that returns companies where `ats_provider='greenhouse' AND monitored=true`. The scraper becomes a pure function (list of identifiers → list of jobs).
- [ ] T022 [US2] Add `--monitored-only` flag to `scrape.py` (argparse, mutually exclusive with default full scrape mode). In this mode, skip `scrapers/boards/` discovery entirely.
- [ ] T023 [US2] Build company-keyed dispatcher in `scrape.py`: iterate `JobStorage.get_monitored_companies()`, group by `ats_provider`, invoke the matching ATS adapter from `scrapers/ats/<provider>.py` with the list of identifiers. For each job returned, call `JobStorage.set_job_monitored_company(job_id, company_id)`.
- [ ] T024 [US2] Update `BaseScraper` in `scrapers/base.py` to accept an optional `targets: list[dict]` parameter (company rows with ats_provider + ats_identifier), so ATS adapters receive their targets at init time rather than discovering them internally.
- [ ] T025 [US2] Add rate-limiting per ATS provider in the dispatcher: 1s delay between companies sharing the same provider (polite to ATS APIs). Reuse existing delay infrastructure.
- [ ] T026 [US2] Wire `monitored_company_id` into the scrape write path: when the dispatcher writes a job row, include the company's ID. The existing `insert_job()` or `upsert_job()` in `storage.py` must accept an optional `monitored_company_id` parameter.
- [ ] T027 [US1] [US2] Run `python -m pytest tests/test_storage.py -q` — all tests green.

### Validation (manual, per quickstart.md)

- [ ] T028 [US1] [US2] Validate Greenhouse parity: run `python scrape.py --monitored-only` and confirm job count matches the old code's greenhouse output for the same boards.
- [ ] T029 [US1] [US2] Validate monitored-only skips boards: confirm no LinkedIn/Indeed/RemoteOK jobs appear in the run output.
- [ ] T030 [US1] [US2] Validate 0 monitored companies: `python scrape.py --monitored-only` with no monitored companies exits cleanly with "nothing to monitor".

**Checkpoint**: `--monitored-only` works end-to-end for Greenhouse. Monitoring flag flows from DB → scraper → job row.

---

## Phase 4: User Story 2 (continued) — ATS Adapters (Plan Phase C, P1)

**Goal**: Extend monitoring to Lever, Ashby, Workday, SmartRecruiters, and Workable.

**Independent Test**: Add one known company per provider, run `--monitored-only --source <provider>`, verify jobs appear.

### Implementation

- [ ] T031 [P] [US2] Create `scrapers/ats/lever.py`: calls `https://api.lever.co/v0/postings/{token}?mode=json`, parses JSON response into `JobPosting` list. Inherits `BaseScraper`. SOURCE_NAME = "lever".
- [ ] T032 [P] [US2] Create `scrapers/ats/ashby.py`: calls Ashby public board API (`https://api.ashbyhq.com/posting-api/v1/boards/{board}/jobs`), paginates, parses into `JobPosting` list. SOURCE_NAME = "ashby".
- [ ] T033 [P] [US2] Create `scrapers/ats/workday.py`: POST `https://{tenant}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with JSON body, paginates via offset/limit, parses into `JobPosting` list. SOURCE_NAME = "workday". Note: `ats_identifier` format is `tenant/site` (e.g., `lombardodier/lausanne`).
- [ ] T034 [P] [US2] Create `scrapers/ats/smartrecruiters.py`: calls `https://api.smartrecruiters.com/v1/companies/{id}/postings`, paginates, parses into `JobPosting` list. SOURCE_NAME = "smartrecruiters".
- [ ] T035 [P] [US2] Create `scrapers/ats/workable.py`: calls `https://apply.workable.com/api/v3/accounts/{id}/jobs`, parses JSON response into `JobPosting` list. SOURCE_NAME = "workable".
- [ ] T036 [US2] Register all new ATS adapters in `scrapers/ats/__init__.py` for scraper discovery. The `discover_scrapers()` function (updated in T003) must pick them up automatically.
- [ ] T037 [US2] Run `python -m pytest tests/test_storage.py -q` — all tests green.

### Validation (manual, per quickstart.md)

- [ ] T038 [US2] Validate Lever: add a known Lever company (e.g., a crypto company using Lever), run `--monitored-only --source lever`, verify jobs.
- [ ] T039 [US2] Validate Ashby: add Kraken (`ats_provider=ashby`), run `--monitored-only --source ashby`, verify jobs appear.
- [ ] T040 [US2] Validate Workday: add Lombard Odier (`ats_provider=workday`, `ats_identifier=lombardodier/lausanne`), run `--monitored-only --source workday`, verify jobs.
- [ ] T041 [US2] Validate SmartRecruiters: add Swissquote (`ats_provider=smartrecruiters`), run, verify.
- [ ] T042 [US2] Validate Workable: add a known Workable company, run, verify.

**Checkpoint**: All 6 ATS adapters functional. `--monitored-only` works across all providers.

---

## Phase 5: User Story 6 — Add Company + ATS Detection (Plan Phase D, P2)

**Goal**: User can add a company via careers URL, auto-detect its ATS, and enable monitoring from the UI.

**Independent Test**: Paste `https://boards.greenhouse.io/fireblocks` into the add-company form → Greenhouse detected → toggle becomes active.

### Implementation

- [ ] T043 [US6] Create `ats_detection.py` at project root: `detect_ats_from_url(careers_url: str) -> dict | None`. Hostname pattern matching against known providers (`boards.greenhouse.io` → greenhouse, `jobs.lever.co` → lever, `*.ashbyhq.com` → ashby, `*.myworkdayjobs.com` → workday, `careers.smartrecruiters.com` → smartrecruiters, `apply.workable.com` → workable). Extract identifier from URL path.
- [ ] T044 [US6] Add vanity domain detection to `ats_detection.py`: `discover_ats_from_page(careers_url: str) -> dict | None`. Fetch the careers page HTML (single HTTP GET), search for known ATS board URLs or API endpoints in the page source. Return provider + identifier if found.
- [ ] T045 [US6] Add `ats_detection.py` function `resolve_scrape_method(careers_url: str) -> dict`: tries hostname match first, then page fetch. Returns `{provider, identifier}` or raises `DetectionFailed` with reason. Falls back to manual input prompt.
- [ ] T046 [US6] Add manual fallback to `ats_detection.py`: `validate_manual_entry(provider: str, identifier: str) -> dict`. Validates provider is in known list, returns the resolved method.
- [ ] T047 [US6] Add "Add Company" form in `tracker_views/settings.py`: text input for careers URL, "Detect" button that calls `resolve_scrape_method()`. On success: company created/updated with `ats_provider` + `ats_identifier`, monitoring toggle becomes active. On failure: show "no scrapable source detected" + manual provider/identifier fields.
- [ ] T048 [US6] Wire company upsert from UI: add `JobStorage` method `upsert_company_from_detection(name, careers_url, ats_provider, ats_identifier)` that creates or updates a company row. Called from the Settings form on successful detection.
- [ ] T049 [US6] Add monitoring toggle to the company detail view: a Streamlit toggle widget that calls `set_company_monitored()`. Disabled (greyed out) with tooltip "no scrapable source detected" when `ats_provider IS NULL AND scraper_id IS NULL`.
- [ ] T050 [US6] Run `python -m pytest tests/test_storage.py -q` — all tests green.

### Validation (manual, per quickstart.md)

- [ ] T051 [US6] Hostname detection: paste `https://boards.greenhouse.io/fireblocks` → Greenhouse detected, identifier = fireblocks.
- [ ] T052 [US6] Vanity domain: paste `https://careers.coinbase.com` → page fetched, embedded ATS discovered.
- [ ] T053 [US6] Non-scrapable URL: paste `https://example.com` → "no scrapable source detected", manual fields shown.

**Checkpoint**: Companies can be added from the UI. ATS detection works for all 6 providers. Toggle respects scrapability.

---

## Phase 6: User Story 3 — Title Gate (Plan Phase E, P1)

**Goal**: Non-PM jobs from monitored companies never reach the LLM. The title gate runs deterministically before any Groq call.

**Independent Test**: Feed "Senior Tech Product Owner" → passes. Feed "Software Engineer" → filtered.

### Implementation

- [ ] T054 [US3] Create `title_gate.py` at project root: `is_product_management_title(title: str) -> bool`. Case-insensitive substring match against inclusion list (product manager, senior product manager, staff product manager, principal product manager, lead product manager, group product manager, director of product, head of product, vp product, chief product officer, product owner, technical product owner). Never exact equality.
- [ ] T055 [US3] Add title gate invocation in `score.py`: before the LLM extraction call (and before the LLM scoring call), check `is_product_management_title(job.title)`. If False: call `JobStorage.set_job_filtered_non_product(job.id)`, log "filtered: non-PM title", skip to next job.
- [ ] T056 [US3] Add FELFEL regression case to `title_gate.py` as a module-level doctest or assertion: `assert is_product_management_title("Senior Tech Product Owner") == True`.
- [ ] T057 [US3] Run `python -m pytest tests/test_storage.py -q` — all tests green.

### Validation (manual, per quickstart.md)

- [ ] T058 [US3] Verify FELFEL passes: score a job titled "Senior Tech Product Owner" → reaches LLM (not filtered).
- [ ] T059 [US3] Verify non-PM filtered: score a job titled "Software Engineer, Backend" → marked `filtered_non_product`, no Groq call.
- [ ] T060 [US3] Verify edge case: "Product Marketing Manager" → passes (substring match on "product manager" — acceptable false positive per safety rule).

**Checkpoint**: Title gate protects Groq quota. FELFEL regression green. Non-PM jobs auditable via `filtered_non_product` column.

---

## Phase 7: User Story 4 — Scoring Signal (Plan Phase E, P2)

**Goal**: Monitoring provenance injected as prose into LLM scoring context. Monitored jobs score higher, but clearly irrelevant ones still score low.

**Independent Test**: Score two identical jobs (one monitored, one not) → monitored scores higher.

### Implementation

- [ ] T061 [US4] In `scorer.py`, add monitoring signal to the LLM `scoring_context`: read `job.monitored_company_id`, if set, append prose to the system prompt: "This job is from a company the user actively monitors — treat as a strong positive signal. However, score honestly: if the role is clearly junior or the company type is fundamentally mismatched (large bank, Big 4, etc.), apply a soft downgrade."
- [ ] T062 [US4] Ensure the monitoring signal is applied via the prose path (scoring_context), NOT as a hard-coded arithmetic bonus. The existing `scoring_context` injection mechanism in `scorer.py` / `profiles.py` is used — no new code path.
- [ ] T063 [US4] Verify seniority remains a soft signal: confirm that `score.py` does NOT add a seniority gate — the LLM assesses seniority from the full description text per C3.

### Validation (manual, per quickstart.md)

- [ ] T064 [US4] Score two otherwise-identical jobs (same title, location, description quality) — one monitored, one not. Verify monitored job scores at least 1 point higher.
- [ ] T065 [US4] Score a clearly junior role ("Junior PM, 0-2 years") from a monitored company. Verify it scores low despite the monitoring signal.
- [ ] T066 [US4] Score a senior PM role at a private bank from a monitored company. Verify it lands in the middle range (soft downgrade, not rejection).

**Checkpoint**: Monitoring provenance flows into scoring. Signal is strong but not an override.

---

## Phase 8: User Story 5 — Visual Distinction (Plan Phase E, P2)

**Goal**: Monitored-company jobs show `🎯 Monitored · {company}` badge, independent of score, visible without scoring.

**Independent Test**: Load Jobs page with mix of monitored and non-monitored jobs → badge visible on monitored ones.

### Implementation

- [ ] T067 [US5] Add provenance badge to job cards in `tracker_views/job_helpers.py`: read `monitored_company_id` (via `company.monitored`), render `🎯 Monitored · {company.name}` badge when true. Position: next to score badge, not replacing it.
- [ ] T068 [US5] Add badge CSS in `tracker.py` global styles: distinct color/treatment from score badge, same prominence regardless of score value. Use `st.html()` for targeted CSS.
- [ ] T069 [US5] Ensure badge renders even when scoring is disabled/absent: check `company.monitored` directly from the job's company relationship, not from score data.
- [ ] T070 [US5] Add optional sort/pin toggle in `tracker_views/jobs.py` sidebar: "Show monitored first" checkbox that sorts monitored-company jobs to top of list.

### Validation (manual, per quickstart.md)

- [ ] T071 [US5] Load Jobs page → monitored-company jobs show `🎯 Monitored · {company}` badge, non-monitored don't.
- [ ] T072 [US5] Low-score monitored job (3/10) and high-score non-monitored job (8/10) → monitored badge is equally prominent, score badge differs.
- [ ] T073 [US5] Filter by minimum score = 7 → low-score monitored jobs hidden (badge doesn't override filters).
- [ ] T074 [US5] Disable scoring → badge still visible.

**Checkpoint**: Monitored jobs visually distinct at a glance. Badge works with and without scoring.

---

## Phase 9: User Story 7 — Settings Management Panel (Plan Phase E, P3)

**Goal**: Settings page lists all monitored companies with status, pause/resume, and last check date.

**Independent Test**: Open Settings, see monitored companies list, pause one, verify it's skipped in next run.

### Implementation

- [ ] T075 [US7] Add "Monitored Companies" panel to `tracker_views/settings.py`: table listing all companies where `monitored=true` (or have `ats_provider` set). Columns: company name (linked to company detail), ATS provider, monitoring status (active/paused), last monitoring check, pause/resume button.
- [ ] T076 [US7] Add pause/resume controls: Streamlit button that toggles `monitored` via `JobStorage.set_company_monitored()`. Button label: "Pause" when active, "Resume" when paused. Visual distinction (green dot / grey dot).
- [ ] T077 [US7] Add "Remove" button: sets `monitored=false` (same as pause — configuration is preserved per FR-003a).
- [ ] T078 [US7] Run `python -m pytest tests/test_storage.py -q` — all tests green.

### Validation (manual, per quickstart.md)

- [ ] T079 [US7] Open Settings → monitored companies listed with correct status and provider.
- [ ] T080 [US7] Pause a company → `monitored=false` in DB, company skipped in next `--monitored-only` run.
- [ ] T081 [US7] Resume a company → `monitored=true`, scraping config intact, company included in next run.

**Checkpoint**: Full management UI. Pause/resume cycle works end-to-end.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Tests, docs, final validation.

- [ ] T082 Run full test suite: `python -m pytest tests/test_storage.py -q`. All tests must pass (existing 162 + all new ones from this feature).
- [ ] T083 Run through all quickstart validation scenarios in `specs/001-monitored-companies/quickstart.md` — Phase A through E, confirming each checkpoint.
- [ ] T084 Run `python scrape.py --monitored-only` end-to-end with all 30 seeded Greenhouse boards + any manually added companies. Confirm jobs written, deduplication works, rate-limiting functional.
- [ ] T085 Run `python score.py --extract; python score.py` with monitored jobs in DB. Confirm title gate filters non-PM, monitoring signal appears in scoring, FELFEL regression passes.
- [ ] T086 Launch `streamlit run tracker.py` and verify: provenance badge on Jobs page, add-company form in Settings, monitored companies panel, pause/resume cycle, toggle in company detail.
- [ ] T087 Commit all changes with message: `feat: add monitored companies — tracking, ATS adapters, title gate, scoring signal, provenance badge`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately.
- **Foundational (Phase 2)**: Depends on Setup (Phase 1). **BLOCKS all user stories.**
- **User Story 1 & 2 — Greenhouse + dispatcher (Phase 3)**: Depends on Foundational (Phase 2). Blocks Phase 5+ (needs dispatcher).
- **User Story 2 continued — ATS adapters (Phase 4)**: Depends on Phase 3 (needs dispatcher pattern from Greenhouse).
  - All 5 adapters (T031-T035) marked [P] — can be implemented in parallel.
- **User Story 6 — ATS detection + add company (Phase 5)**: Depends on Phase 4 (needs all providers for detection).
- **User Story 3 — Title gate (Phase 6)**: Depends on Foundational (Phase 2). Independent of Phases 3-5.
- **User Story 4 — Scoring signal (Phase 7)**: Depends on Foundational (Phase 2). Independent of UI phases.
- **User Story 5 — Visual distinction (Phase 8)**: Depends on Foundational (Phase 2). Independent of other Phase E items.
- **User Story 7 — Settings panel (Phase 9)**: Depends on Phase 5 (needs add-company form). Independent of Phases 6-8.
- **Polish (Phase 10)**: Depends on all prior phases.

### User Story Dependencies

- **US1 & US2 (P1)**: Can start after Foundational. No dependencies on other stories.
- **US3 (P1)**: Can start after Foundational. Independent of US1/US2 (operates on jobs in DB, not on scrape pipeline).
- **US4 (P2)**: Can start after Foundational. Independent (reads `monitored_company_id` from DB).
- **US5 (P2)**: Can start after Foundational. Independent (reads company.monitored from DB).
- **US6 (P2)**: Depends on Phase 3+4 (needs dispatcher + all ATS adapters for detection).
- **US7 (P3)**: Depends on US6 (needs add-company form in Settings).

### Parallel Opportunities

- Phase 4 ATS adapters: T031, T032, T033, T034, T035 all in parallel.
- Phase 6 (Title gate), Phase 7 (Scoring signal), Phase 8 (Visual distinction) can all run in parallel after Foundational — they touch different files.
- Tests within each phase marked [P] can run in parallel.

---

## Parallel Example: Phase 4

```bash
# Launch all 5 ATS adapters in parallel (different files, no cross-dependencies):
Task: "Create scrapers/ats/lever.py"
Task: "Create scrapers/ats/ashby.py"
Task: "Create scrapers/ats/workday.py"
Task: "Create scrapers/ats/smartrecruiters.py"
Task: "Create scrapers/ats/workable.py"
```

## Parallel Example: Phase 6 + 7 + 8

```bash
# Title gate, scoring signal, and visual distinction touch different files:
Task: "Create title_gate.py + wire in score.py"       # Phase 6
Task: "Inject monitoring signal in scorer.py"         # Phase 7
Task: "Add provenance badge in job_helpers.py"        # Phase 8
```

---

## Implementation Strategy

### MVP First (Phases 1-3: Setup + Foundational + Greenhouse)

1. Complete Phase 1: Setup (scraper sub-packages)
2. Complete Phase 2: Foundational (data model, migrations, seed data)
3. Complete Phase 3: Greenhouse DB-driven + `--monitored-only` flag
4. **STOP and VALIDATE**: Run `--monitored-only` with 30 Greenhouse boards → jobs appear in DB with `monitored_company_id`
5. This is already a working MVP — one ATS provider, monitoring flag, monitored-only mode

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. Add Greenhouse + dispatcher → First monitored-only run works (MVP!)
3. Add Lever, Ashby, Workday, SmartRecruiters, Workable → All 6 ATS providers
4. Add ATS detection + company addition UI → Users can add their own targets
5. Add title gate → Groq quota protected
6. Add scoring signal → Monitoring affects scores
7. Add visual distinction → Badge visible in UI
8. Add Settings panel → Bulk management
9. Polish → Tests, validation, commit

### Phases 6-8 Can Run in Parallel

Once Foundational is done and some monitored jobs exist in the DB (from Phase 3+4), the title gate (Phase 6), scoring signal (Phase 7), and visual distinction (Phase 8) can all be developed and tested independently — they touch different files and have no cross-dependencies.

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks within the same phase
- [Story] label maps task to specific user story for traceability
- Tests are included only for `storage.py` changes (per CLAUDE.md requirement)
- Integration tests for scrapers are manual validation (per quickstart.md — live HTTP calls)
- Constitution Principle V (surgical edits): never touch `storage.py`, `models.py`, `profiles.py`, `scorer.py`, `scrape.py`, `main.py`, `tracker_views/shared.py`, `tracker_views/onboarding.py` unless the task explicitly lists them
- Constitution Principle VIII: `boards/` scrapers are moved verbatim (no logic changes). `ats/` adapters are new. `company_sites/` is empty at launch.
- `storage.py` changes in Phase 2: follow existing patterns (WAL mode, parameterized queries, no raw SQL outside helper methods)
