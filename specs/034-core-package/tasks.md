# Tasks: Domain Package `core/` (spec 034, roadmap step 1c)

**Input**: Design documents from `/specs/034-core-package/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/rewrite-imports.md, quickstart.md

**Tests**: Guard tests are **required** (FR-004, FR-005, FR-014). No other new tests — the
existing suite is the regression net.

**Organization**: Grouped by user story (US1, US2, US3 — all P1, in commit order).

## Format: `[ID] [P?] [Story] Description`

- **[P]** = parallelizable (different files, no dependency on another task)
- **[Story]** = US1 / US2 / US3

---

## Phase 1: Setup

**Purpose**: Record the green baseline and create the branch **before** any change.

- [X] T001 Run `python -m pytest tests/` and confirm the full suite is green; note the count for the final US3 validation. *(261 passed, 2026-10-08.)*
- [X] T002 Create branch `034-core-package` (from `main`). Phase A and Phase B are two separate commits on it; one release covers both.

---

## Phase 2: User Story 1 — No location or string reference can break silently (Priority: P1) 🎯 MVP

**Goal**: Remove the last `__file__`-derived locations, route `monitoring_agent.py`'s
source-tree file paths through a single `SCRAPERS_SRC_DIR` constant, and add guard tests
that resolve every string-based module reference, so the move (US2) can only fail loudly.

**Independent Test**: With no file moved, the suite is green; deliberately breaking
one `-m` string, one `importlib` string, one `mock.patch` target, a `SCRAPERS_SRC_DIR`
subdirectory, or reintroducing a `__file__` derivation makes a guard test fail.

### Implementation (commit A)

- [X] T003 [US1] Update `paths.py` (FR-001, FR-002): add `CV_PIPELINE_DIR` and `SCRAPERS_SRC_DIR`.
  - `CV_PIPELINE_DIR = os.environ.get("CV_PIPELINE_DIR") or os.path.join(os.path.dirname(PROJECT_ROOT), ".cv_pipeline")` → resolves to `…/AI-Suite/.cv_pipeline` (unchanged).
  - `SCRAPERS_SRC_DIR = os.path.join(PROJECT_ROOT, "scrapers")` — the single constant for every source-tree scraper path. **Pre-move** it points at `scrapers/`; Phase B (T013) changes only this constant to `core/scrapers/`.
- [X] T004 [US1] Update `cv_agent/renderer.py` (FR-001): import `CV_PIPELINE_DIR` from `paths`, delete the local `_REPO_ROOT`/`CV_PIPELINE_DIR` (lines 31–35), keep `MASTER_CV_PATH = os.path.join(CV_PIPELINE_DIR, "cv_data_master.json")`. Depends on T003.
- [X] T005 [US1] Update `monitoring_agent.py` (FR-002): import `PROJECT_ROOT` and `SCRAPERS_SRC_DIR` from `paths`; replace `ROOT = Path(__file__).resolve().parent` with `ROOT = Path(PROJECT_ROOT)`; and derive every source-tree file-path literal from `SCRAPERS_SRC_DIR` (no hardcoded `"scrapers/…"` string left):
  - `GREENHOUSE_CONFIG_PATH` (line 30) → `os.path.join(SCRAPERS_SRC_DIR, "greenhouse.py")`
  - `ATS_CONFIG_PATHS` (lines 33–36) → `os.path.join(SCRAPERS_SRC_DIR, "ats", "lever.py")` / `"workable.py"`
  - `_write_scraper_stub(provider, f"scrapers/ats/{provider}.py")` (line 172) → call site passes a source-tree-relative path `f"ats/{provider}.py"` and the stub writes `Path(SCRAPERS_SRC_DIR) / rel_path`
  - `_write_scraper_stub("custom", f"scrapers/company_sites/{slug}.py")` (line 270) → `f"company_sites/{slug}.py"` (same `Path(SCRAPERS_SRC_DIR) / rel_path` join)
  - `ROOT / "specs"` stays as-is (specs live at the repo root; already correct via `ROOT = Path(PROJECT_ROOT)`).

  Depends on T003. The generated-spec/stub *prose* strings (e.g. `_write_ats_spec` lines 190/199, stub `Reference: scrapers/ats/lever.py`) are **not** filesystem paths — leave them for the T010 grep audit.
- [X] T006 [P] [US1] Rewrite scraper discovery in `scrape.py` (FR-003): replace `scrapers_dir = os.path.join(os.path.dirname(__file__), "scrapers")` (line 21) with `pkgutil.iter_modules(<subpkg>.__path__)` for `boards`/`ats`/`company_sites` and `pkgutil.iter_modules(scrapers.__path__)` for the root-level fallback loop. No `__file__`.
- [X] T007 [US1] Create `tests/test_core_module_refs.py` (FR-004, FR-005, FR-014):
  - **FR-004** — fail on any `__file__` token in runtime `*.py` (the 27 domain modules, `scrapers/`, `cv_agent/`, `tracker.py`, `tracker_views/`, `scripts/`) outside two allow-listed *patterns* (not a directory allow-list): `paths.py` (the single allowed `__file__`-derived location) and the AST-matched call `is_active_page(__file__)` (call to `is_active_page` whose sole argument is `__file__`). `tracker_views/` is covered, not exempt. Phase A also fixes the two real violations this guard otherwise flags — `scripts/duplicate_report.py` (`ROOT/data/jobs.db` → `paths.DB_PATH`) and `scripts/fingerprint.py` (`os.path.dirname(__file__)/regression_cases.json` → `importlib.resources.files("scripts") / "regression_cases.json"`, with `[tool.setuptools.package-data] scripts = ["*.json"]` in `pyproject.toml` so the JSON ships with the install). Negative fixture: a planted `__file__` makes the guard fail.
  - **FR-005** — resolve every string module reference and fail with `file:line` on any unresolvable one: `-m` args in `sys.executable` launch lists (excludes `git commit -m`), literal/f-string-prefix args of `importlib.import_module`, and `mock.patch`/`monkeypatch.setattr` string targets in `tests/` (walk the dotted path by attribute, mirroring `_pytest.monkeypatch.resolve`; stdlib `builtins.*`/`sys.*` resolve against their module). Negative fixture for each of the three kinds.
  - **FR-014** — assert the source-tree base directories referenced by `SCRAPERS_SRC_DIR` exist: `SCRAPERS_SRC_DIR` itself and its `ats/`, `boards/`, `company_sites/` subdirectories (imported from `paths`). Negative fixture: a temp value for `SCRAPERS_SRC_DIR` that does not exist makes the guard fail.
  - Refine `tests/test_location_independence.py::test_no_py_subprocess_launches` so bare `.py` literals that are `os.path.join(...)` filename arguments are not treated as `python name.py` launches (the `monitoring_agent.py` `SCRAPERS_SRC_DIR` filenames are path components, not launches).
- [X] T008 [US1] Commit Phase A on `034-core-package`: `refactor(spec-034): Phase A — remove last __file__ refs, route scraper paths through SCRAPERS_SRC_DIR, add core-module guards`.

**Checkpoint**: `python -m pytest tests/` green (T001 count + new guard tests); the quickstart Phase-A negative check passes; **nothing moved**; exactly one commit (T008).

---

## Phase 3: User Story 2 — Domain moved into `core/` in one mechanical commit (Priority: P1)

**Goal**: The 27 domain modules, `scrapers/` and `cv_agent/` under a `core/` package,
imported everywhere as `core.<module>`, no shims, in a single commit. The only
source-tree location change is `SCRAPERS_SRC_DIR` in `core/paths.py`.

**Independent Test**: Full suite green; no domain module at the root;
`python -m scripts.rewrite_core_imports --check` is a no-op; `pip install -e .`
exposes `core`, `core.scrapers.*`, `core.cv_agent`, `tracker_views`, `scripts`,
`tracker`.

### Implementation (one commit, T020)

- [X] T009 [P] [US2] Pre-move check: inspect `data/cv_agent_checkpoints.sqlite` on the Mac for unfinished LangGraph threads (edge case). The spec's Assumptions already confirm none — discardable. No code change.
- [X] T010 [US2] **Grep audit (pre-move, FR-015)**: grep runtime code — the 27 domain modules, `scrapers/`, `cv_agent/`, `tracker.py`, `tracker_views/`, `scripts/` — for string literals containing `scrapers/`, `cv_agent/`, or any `<domain module>.py` (one of the 27 names + `.py`). List every hit with `file:line` in the task output; each hit is either (a) already derived from a `paths` constant (e.g. `SCRAPERS_SRC_DIR`), or (b) justified here. Expected hits and dispositions:
  - `monitoring_agent.py` filesystem paths — already fixed via `SCRAPERS_SRC_DIR` in T005.
  - `monitoring_agent.py` `_write_ats_spec` prose (lines ~190/199) and the stub `Reference: scrapers/ats/lever.py` docstring — **justified**: prose describing the module path in generated SpecKit specs/stubs; updated to `core/scrapers/…` in T013 (one-line text edits).
  - Anything else — fix via a `paths` constant or justify; **no unexplained literal may survive into the move**.

  ```bash
  # representative (adjust file list to the actual 27 names):
  grep -rnE '["'\''`](scrapers|cv_agent)/' --include='*.py' \
    ats_detection.py backfill_descriptions.py company_researcher.py context_tuner.py \
    create_profile.py cv_extract.py email_monitor.py export_jobs.py export_seed.py \
    filters.py job_actions.py llm.py main.py models.py monitoring_agent.py notifier.py \
    paths.py preference_report.py prepare.py profile_generator.py profiles.py score.py \
    scorer.py scrape.py seed.py storage.py title_gate.py tracker.py \
    scrapers/ cv_agent/ tracker_views/ scripts/
  grep -rnE '(ats_detection|backfill_descriptions|company_researcher|context_tuner|create_profile|cv_extract|email_monitor|export_jobs|export_seed|filters|job_actions|llm|main|models|monitoring_agent|notifier|paths|preference_report|prepare|profile_generator|profiles|score|scorer|scrape|seed|storage|title_gate)\.py' \
    --include='*.py' ats_detection.py backfill_descriptions.py company_researcher.py \
    context_tuner.py create_profile.py cv_extract.py email_monitor.py export_jobs.py \
    export_seed.py filters.py job_actions.py llm.py main.py models.py monitoring_agent.py \
    notifier.py paths.py preference_report.py prepare.py profile_generator.py profiles.py \
    score.py scorer.py scrape.py seed.py storage.py title_gate.py tracker.py \
    scrapers/ cv_agent/ tracker_views/ scripts/
  ```
- [X] T011 [P] [US2] Create `scripts/rewrite_core_imports.py` per `contracts/rewrite-imports.md` (FR-007): idempotent, `--check` mode, moved names = 27 modules ∪ `scrapers` ∪ `cv_agent`; rewrites imports and `-m`/`importlib`/`mock.patch` strings. **No file-path-string rule** — source-tree paths are handled by `SCRAPERS_SRC_DIR` (T005/T013), not the script. Locates the repo via `core.paths.PROJECT_ROOT`, uses no `__file__`.
- [X] T012 [US2] `git mv` the 27 domain modules (`ats_detection` … `title_gate`), `scrapers/` and `cv_agent/` into `core/`; create `core/__init__.py` (FR-006). `tracker.py`, `tracker_views/`, `scripts/`, `tests/` and the five one-offs stay at the root.
- [X] T013 [US2] Update `core/paths.py`: `PROJECT_ROOT` → `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` (one level up — FR-009) **and** `SCRAPERS_SRC_DIR` → `os.path.join(PROJECT_ROOT, "core", "scrapers")` (the only source-tree location change). Update the two `monitoring_agent.py` prose strings from the T010 audit to `core/scrapers/…` (spec template lines ~190/199 + stub `Reference:` docstring). Verify `DATA_DIR`/`DB_PATH`/`OUTPUT_DIR`/`ENV_PATH`/`CV_PIPELINE_DIR`/`SCRAPERS_SRC_DIR` resolve to the same values as before.
- [X] T014 [US2] Run `python -m scripts.rewrite_core_imports` to rewrite all imports and string references (FR-007, FR-008), then `python -m scripts.rewrite_core_imports --check` (no-op).
- [X] T015 [P] [US2] Update non-Python references (FR-011): `docker-compose.yml` (`agent`: `python -m core.main`; `email-monitor`: `python -m core.email_monitor`) and `scripts/deploy.sh:59` (`python -m core.seed`).
- [X] T016 [P] [US2] Update `pyproject.toml` (FR-010): `py-modules = ["tracker"]`; add `core`, `core.scrapers`, `core.scrapers.ats`, `core.scrapers.boards`, `core.scrapers.company_sites`, `core.cv_agent` to `packages`. Update `tests/test_installable_project.py` so a domain module left at the root fails the root-module guard.
- [X] T017 [P] [US2] Update `tests/test_core_module_refs.py` FR-004 allow-list from `{paths.py}` to `{core/paths.py}` (post-move). FR-005 and FR-014 need no change (FR-005 resolves the now-`core.`-prefixed names; FR-014 asserts `SCRAPERS_SRC_DIR` still exists — now `core/scrapers/`).
- [X] T018 [P] [US2] Update docs (FR-012): `CLAUDE.md` (commands, key files, NEVER-modify list → `core/` paths), `docs/*.md` references, and the constitution Stable-core list to `core/` paths (PATCH wording + Sync Impact Report per Governance).
- [X] T019 [US2] Validate (SC-002): `pip install -e ".[dev]"`; `python -m pytest tests/` green; `grep` finds no root-form import of a moved module; `python -m core.cv_agent.cli --help` works and prints the same `CV_PIPELINE_DIR`/`MASTER_CV_PATH` (SC-004).
- [ ] T020 [US2] Commit Phase B on `034-core-package` (single commit): `refactor(spec-034): move domain into core/ package`.

**Checkpoint**: one commit (T020) containing T010–T019; suite green; no domain module at the root.

---

## Phase 4: User Story 3 — Released through the staging flow with no behaviour change (Priority: P1)

**Goal**: Validate the moved code on staging on the exact SHA, then ff-only merge and
deploy; the live tracker, nightly cron and CV agent behave exactly as before. One release
covers both Phase A and Phase B.

**Independent Test**: Staging on the candidate SHA passes all checks; after merge +
deploy, health check, parity and next-night cron are green.

### Implementation (deploy-gated, on verva — see `docs/migration-checklist.md`)

- [ ] T021 [US3] `git push` `034-core-package`; on verva `scripts/staging.sh up <full-sha>`. Confirm the staging tracker on 8502 serves the backup; click every background-launch button (Jobs: fetch/score/extract; Settings: scrape/monitored-only/score/extract) and confirm each runs `python -m core.*`; open `/job_detail?id=<id>`. **STOP — hand over for my manual checks.**
- [ ] T022 [US3] Parity fingerprint identical (candidate vs prod image, same backup copy, same `--as-of`); `staging.sh run python -m core.scrape --source <one cheap source>` and `staging.sh run python -m core.score --extract` complete normally; `scripts/staging.sh down`. **STOP — hand over for my go.**
- [ ] T023 [US3] After the go: ff-only merge of the SHA onto `main` (`git merge --ff-only <full-sha>`; refuse non-ff).
- [ ] T024 [US3] Run the deploy command (`./scripts/deploy.sh` on verva); then verify `git -C /opt/job-agent rev-parse HEAD` equals the merged SHA, `core.seed` ran OK, health check OK, and `tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501` is up.
- [ ] T025 [US3] Next-night cron: the latest `runs` row (`run_type='full'`) has `status='success'`, produced by `docker compose run --rm agent` running `python -m core.main`. Confirm parity green and the tracker smoke test (feed, status change, job detail).
- [ ] T026 [US3] Closing commit on `main` recording the release (docs), mirroring spec 033's `docs(spec-033): close …`.

**Checkpoint**: deployed SHA = staged SHA; cron green; tracker fully operational.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)**: no deps (T001 baseline, T002 branch).
- **US1 (P2)**: after Setup. No story deps. **MVP** — the move is impossible to do safely without it.
- **US2 (P3)**: after US1 (the guards are the safety net). One commit.
- **US3 (P4)**: after US2 (deploys the moved code). Deploy-gated on the user's go.

### Within US1

- T003 → T004 and T003 → T005 (both need the new `paths` constants).
- T006 (scrape.py) is parallel and independent (disjoint file).
- T007 (guards) after T003–T006 so the guards are green.
- T008 (commit) is last.

### Within US2

- T009 (checkpoint check) and T011 (rewrite script) are parallel.
- T010 (grep audit) runs after US1 but before the move — its disposition feeds T013.
- T012 (`git mv`) → T013 (`core/paths.py` + prose) → T014 (run rewrite) is a strict chain.
- T015–T018 are parallel (disjoint files) after T014.
- T019 (validate) is last, then T020 (commit).

### Parallel Opportunities

```text
# US1 — disjoint files, run together:
T005 (monitoring_agent.py) ∥ T006 (scrape.py)

# US2 — after the git mv + rewrite, disjoint files:
T015 (compose + deploy.sh) ∥ T016 (pyproject + root-module guard)
   ∥ T017 (FR-004 allow-list) ∥ T018 (docs + constitution)
```

---

## Implementation Strategy

### MVP First (US1 only)

1. T001 baseline + T002 branch → T003–T007 (hardening + guards).
2. **STOP and VALIDATE**: suite green; each guard fails when its violation is
   reintroduced (quickstart Phase-A negative check). This is the whole point —
   the move is now a mechanical operation that can only fail loudly.

### Incremental Delivery

1. US1 → suite green, guards in place (nothing moves). **Commit A (T008).**
2. US2 → one `git mv` + rewrite commit; suite green; `core` importable everywhere. **Commit B (T020).**
3. US3 → staging on the exact SHA → ff-only merge → deploy → SHA check → cron green. **One release for both commits.**

---

## Notes

- **Stable-core exception**: US2 touches `storage.py`, `models.py`, `profiles.py`,
  `scrape.py`, `scorer.py`, `main.py`, `scrapers/` — sanctioned because step 1c is
  precisely about moving them (FR-012 updates the list to `core/` paths). The move
  is verbatim (`git mv`); no logic rewrite.
- The rewrite script is a **migration tool**, never imported by the app; it uses no
  `__file__`, so FR-004 needs no allow-list entry for it.
- `prompts/` is historical and not rewritten (spec edge case).
- Constitution amendment in T018 is a **PATCH** (wording only) and must carry a
  Sync Impact Report in the HTML comment header.
- Commits T008/T020 end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- Every task touches non-stable-core files only in US1; US2 is the sanctioned move.
- `SCRAPERS_SRC_DIR` (paths.py) is the single source of truth for scraper source-tree
  paths; Phase B (T013) changes its value, not the strings that use it.
