# Tasks: Location Independence (roadmap step 1a)

**Input**: Design documents from `/specs/032-location-independence/` (`plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/paths.md`, `quickstart.md`)

**Tests**: Requested — FR-006 mandates `tests/test_location_independence.py` (guard-behaviour + repo-scan checks). Test tasks are written first (TDD: they fail against the current code).

**Organization**: Grouped by user story (all P1). The three stories share `paths.py` and `tests/test_location_independence.py`, so the test tasks (T003, T006, T016) touch one file and run sequentially; the *implementation* tasks within a story are `[P]` (distinct files).

## Format: `[ID] [P?] [Story] Description`

- `[P]`: parallel (different files, no dependency on other incomplete tasks)
- `[Story]`: US1 / US2 / US3

---

## Phase 1: Setup

**Purpose**: Baseline before any change.

- [X] T001 Confirm working tree is on branch `032-location-independence` and run `python -m pytest tests/ -q` to establish a green baseline (no new dependency is introduced — stdlib only).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: `paths.py` becomes the single source of truth for every location. Blocks US1/US2 (which consume or extend it).

- [X] T002 Extend `paths.py` per FR-003 and `contracts/paths.md`: rename `_ROOT` → `PROJECT_ROOT` (keep it the sole `__file__` derivation), add `OUTPUT_DIR` (env `JOB_AGENT_OUTPUT_DIR`, default `os.path.join(PROJECT_ROOT, "outputs")`) and `ENV_PATH` (default `os.path.join(PROJECT_ROOT, ".env")`); keep `DATA_DIR`, `DB_PATH`, `data_path()` and the existing `os.makedirs(DATA_DIR, exist_ok=True)` semantics unchanged.

**Checkpoint**: `paths.py` exposes `PROJECT_ROOT`, `DATA_DIR`, `DB_PATH`, `OUTPUT_DIR`, `ENV_PATH`, `data_path()` with no behaviour change (existing `DB_PATH`/`DATA_DIR`/`data_path()` consumers unaffected).

---

## Phase 3: User Story 1 — Prod data location explicit and guarded (Priority: P1) 🎯 MVP

**Goal**: The live app finds its data only through explicit config and refuses to start rather than silently create an empty DB (FR-001, FR-002).

**Independent Test**: `JOB_AGENT_REQUIRE_DB=1 JOB_AGENT_DATA_DIR=<empty> python -c "import paths"` → `RuntimeError`, nothing created; normal start → identical behaviour (quickstart §1–3).

### Tests for User Story 1

- [X] T003 [US1] Create `tests/test_location_independence.py` with guard-behaviour tests (SC-003, FR-002), using a subprocess so env is set before import: (a) `JOB_AGENT_REQUIRE_DB=1` + missing DB → import raises `RuntimeError` naming the path and both env vars, and no directory/file is created; (b) `JOB_AGENT_REQUIRE_DB=1` + DB present → import succeeds; (c) guard unset + empty dir → `DATA_DIR` is created and import succeeds. Run `python -m pytest tests/test_location_independence.py -q` and confirm these fail (guard not implemented yet).

### Implementation for User Story 1

- [X] T004 [US1] Implement the guard in `paths.py` (FR-002): when `os.environ.get("JOB_AGENT_REQUIRE_DB") == "1"` and `os.path.exists(DB_PATH)` is false, `raise RuntimeError` naming `DB_PATH`, `JOB_AGENT_DATA_DIR` and `JOB_AGENT_REQUIRE_DB` **before** any `os.makedirs`; otherwise keep `os.makedirs(DATA_DIR, exist_ok=True)` for the unguarded case (guard on + DB present → no makedirs). Run the T003 tests green.
- [X] T005 [US1] Add an `environment:` block to `docker-compose.yml` for `tracker`, `agent` and `email-monitor` setting `JOB_AGENT_DATA_DIR=/app/data` and `JOB_AGENT_REQUIRE_DB=1` (FR-001; `environment:` takes precedence over `env_file`).

**Checkpoint**: Guard works; compose pins the prod data dir. Verify quickstart §1–3.

---

## Phase 4: User Story 2 — One file knows where things are (Priority: P1)

**Goal**: Every data/output/config location comes from `paths.py` (FR-004). 9 sites.

**Independent Test**: The repo-scan tests (FR-006 a/b) fail on any runtime module deriving a data/output/`.env` path from `__file__` or a CWD-relative `data/` (or hard-coded `/app/data`) literal (quickstart §4).

### Tests for User Story 2

- [X] T006 [US2] Append scan checks to `tests/test_location_independence.py` (FR-006 a/b): walk runtime `*.py` files and fail on (a) a `__file__`-derived data/output/`.env` path outside `paths.py`, and (b) a `"data/` or `"/app/data` string literal used as a path in runtime code. Allow-list: `tests/`, `specs/`, the `scripts/` diagnostic tools deferred to 1b (`filter_funnel.py`, `audit_provenance.py`, `audit_work_mode.py`, `diag_freelance.py`), `scrape.py:21`, `monitoring_agent.py` `ROOT`, `scripts/duplicate_report.py` `ROOT`. Run and confirm these fail on the current code.

### Implementation for User Story 2 (FR-004 — one task per file, all `[P]`)

- [X] T007 [P] [US2] In `export_jobs.py` (lines 26-27): replace `DB_PATH = Path(__file__).parent / "data" / "jobs.db"` and `OUTPUT_DIR = Path(__file__).parent / "data"` with `from paths import DB_PATH, DATA_DIR` and `OUTPUT_DIR = Path(DATA_DIR)`; wrap `DB_PATH` in `Path(...)` in `_require_db()` (`Path(DB_PATH).exists()`, `JobStorage(str(Path(DB_PATH)))`) since `paths.DB_PATH` is a `str`. CSV still writes under `DATA_DIR` (US2 acceptance 1).
- [X] T008 [P] [US2] In `tracker_views/preferences.py:114`: replace `JobStorage("data/jobs.db")` with `JobStorage(DB_PATH)` (add `from paths import DB_PATH`).
- [X] T009 [P] [US2] In `tracker_views/shared.py:95`: replace `os.path.join(os.path.dirname(__file__), "..", ".env")` with `ENV_PATH` (extend the existing `from paths import DB_PATH` import to `from paths import DB_PATH, ENV_PATH`).
- [X] T010 [P] [US2] In `notifier.py` (lines 106 and 210): replace `os.path.join(os.path.dirname(__file__), "outputs")` with `OUTPUT_DIR` (add `from paths import OUTPUT_DIR`).
- [X] T011 [P] [US2] In `score.py:568`: replace `os.path.join(os.path.dirname(__file__), "outputs")` with `OUTPUT_DIR` (add `from paths import OUTPUT_DIR`).
- [X] T012 [P] [US2] In `prepare.py:568`: replace `os.path.join(os.path.dirname(__file__), "outputs", "applications")` with `os.path.join(OUTPUT_DIR, "applications")` (add `from paths import OUTPUT_DIR`).
- [X] T013 [P] [US2] In `seed.py` (lines 18-28): replace `open("data/companies.json")` with `open(data_path("companies.json"))` and the `"data/companies.json ..."` error strings with `data_path("companies.json")` (add `from paths import data_path`).
- [X] T014 [P] [US2] In `export_seed.py:45`: replace `output_path = "data/companies.json"` with `output_path = data_path("companies.json")` (add `from paths import data_path`).
- [X] T015 [P] [US2] In `scripts/scraper_toggle.py:14`: replace `JobStorage("/app/data/jobs.db")` with `JobStorage(DB_PATH)` (add `from paths import DB_PATH`).

**Checkpoint**: T006 scan tests green; all 9 sites read from `paths`.

---

## Phase 5: User Story 3 — Scripts launched as modules (Priority: P1)

**Goal**: All 14 launches use `-m <module>` with unchanged args/flags (FR-005).

**Independent Test**: The scan test (FR-006 c) fails on any `subprocess`/`Popen`/`run` call passing a `"<name>.py"` path; on the drill tracker all 7 buttons + `main.py`'s 4 stages run (quickstart §6–7).

### Tests for User Story 3

- [X] T016 [US3] Append the launch scan to `tests/test_location_independence.py` (FR-006 c): fail on any runtime `*.py` passing a `".py"` filename to `subprocess.run`/`Popen`. Allow-list matches T006 (`monitoring_agent.py` included in the conversion, so it is **not** allow-listed). Run and confirm it fails on the current code.

### Implementation for User Story 3 (FR-005 — one task per file, all `[P]`)

- [X] T017 [P] [US3] In `main.py` (lines 77, 89, 100, 113): convert the four launches to `-m` — `[sys.executable, "-m", "scrape", "--monitored-only", "--no-score"]`, `[sys.executable, "-m", "scrape"]`, `[sys.executable, "-m", "score", "--extract"]`, `[sys.executable, "-m", "score", "--profile", active_id]` (args/`check` unchanged).
- [X] T018 [P] [US3] In `scrape.py` (lines 318-319): convert to `[sys.executable, "-m", "score", "--extract"]` and `[sys.executable, "-m", "score", "--profile", profile.id]` (`check=False` unchanged).
- [X] T019 [P] [US3] In `tracker_views/jobs.py` (lines 119, 123, 135): convert to `[sys.executable, "-u", "-m", "scrape"]`, `[sys.executable, "-u", "-m", "score", "--profile", active_id]`, and `[sys.executable, "-m", "score", "--extract"]` (preserve `-u` where present; `-u` precedes `-m`).
- [X] T020 [P] [US3] In `tracker_views/settings.py` (lines 200, 215, 230, 824): convert to `[sys.executable, "-u", "-m", "scrape"]`, `[sys.executable, "-u", "-m", "scrape", "--monitored-only"]`, `[sys.executable, "-u", "-m", "score", "--profile", active_id]`, and `[sys.executable, "-m", "score", "--extract"]` (preserve `-u`; `-u` precedes `-m`).
- [X] T021 [P] [US3] In `monitoring_agent.py:83`: convert to `[sys.executable, "-m", "export_seed"]`, keeping `cwd=ROOT` (dev tool, included per FR-005).

**Checkpoint**: T016 scan test green; all 14 launches use `-m`.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T022 [P] Document in `CLAUDE.md` (Infrastructure section) the env vars `JOB_AGENT_DATA_DIR`, `JOB_AGENT_REQUIRE_DB`, `JOB_AGENT_OUTPUT_DIR` and the rule "locations come from `paths.py` only; scripts are launched with `-m`" (FR-007).
- [X] T023 Run the full suite `python -m pytest tests/ -q` (SC-006) and the quickstart scenarios §1–6 (guard loud/ok/unchanged, guard test, `python -m scrape/score/export_seed --help`).
- [X] T024 Confirm the deploy-time validations are queued for verva (SC-001 parity, SC-002 drill tracker buttons + `main.py` 4 stages, SC-005 health/cron/CSV/Preferences) via `docs/migration-checklist.md` — do **not** deploy in this phase; deploy is a separate step after user confirmation.

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1) → Foundational (Phase 2) → US1 → US2 → US3 → Polish.
- US1, US2, US3 are all P1 but are executed in order because they share `paths.py` and `tests/test_location_independence.py`.

### User Story Dependencies

- **US1** (guard + compose): depends on Foundational `paths.py` (T002). Blocks nothing downstream but its test file is extended by US2/US3.
- **US2** (9 location sites): depends on Foundational (`paths.py` exposes `OUTPUT_DIR`/`ENV_PATH`/`data_path`). Independent of US1's guard.
- **US3** (14 launches): depends on Foundational only in that the repo is stable; no `paths` dependency. Independent of US1/US2 code.

### Within Each User Story

- Test task first (write, confirm it FAILS), then implementation, then confirm green.

### Shared-file note

- `tests/test_location_independence.py` is created in T003 and appended in T006/T016 — these three run sequentially (no `[P]`).
- `paths.py` is touched by T002 (foundational) and T004 (guard) — sequential.

### Parallel Opportunities

- US2 implementation tasks T007–T015: all `[P]` (9 distinct files).
- US3 implementation tasks T017–T021: all `[P]` (5 distinct files).
- T022 (docs) is `[P]` with respect to T023/T024.

---

## Parallel Example: User Story 2

```bash
# After T006 (scan tests written and failing), launch all 9 conversions together:
Task: "In export_jobs.py replace DB_PATH/OUTPUT_DIR with paths (T007)"
Task: "In tracker_views/preferences.py use JobStorage(DB_PATH) (T008)"
Task: "In tracker_views/shared.py use ENV_PATH (T009)"
Task: "In notifier.py use OUTPUT_DIR (T010)"
Task: "In score.py use OUTPUT_DIR (T011)"
Task: "In prepare.py use OUTPUT_DIR/applications (T012)"
Task: "In seed.py use data_path('companies.json') (T013)"
Task: "In export_seed.py use data_path('companies.json') (T014)"
Task: "In scripts/scraper_toggle.py use JobStorage(DB_PATH) (T015)"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (baseline) + Phase 2 (`paths.py` exposure).
2. Phase 3: US1 guard + compose env → run T003 tests green → quickstart §1–3.
3. **STOP and VALIDATE**: guard fails loudly and creates nothing; unguarded path unchanged.

### Incremental Delivery

1. Setup + Foundational → `paths.py` is the single location source.
2. US1 → guarded prod data dir (SC-003).
3. US2 → all 9 locations via `paths` (SC-004 a/b).
4. US3 → all 14 launches via `-m` (SC-004 c, SC-002).
5. Polish → docs + full suite (SC-006) + deploy-time checklist queued (SC-001/SC-005).

---

## Notes

- `[P]` tasks touch different files; no two `[P]` tasks in the same phase share a file.
- Exact line → line mappings live in `data-model.md` (location + launch tables); tasks reference them but are self-contained.
- `storage.py`, `models.py`, `profiles.py`, `scorer.py`, `llm.py`, `scrapers/`, `tracker_views/onboarding.py` are **not** modified by any task (non-goals).
- Run `python -m pytest tests/ -q` before declaring the feature done (SC-006); deploy to verva is a separate, user-confirmed step.
