# Tasks: Platform Ingestion Scope — title judgement moves to scoring

**Input**: Design documents from `/specs/040-platform-ingestion-scope/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — guard tests (FR-008) and the scorer/storage extensions are part of the spec, and the project convention is to keep `tests/` green with every change.

**Organization**: grouped by user story so each story is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: maps to US1/US2/US3 from spec.md
- Exact file paths in every description.

---

## Phase 1: Setup

**Purpose**: green baseline before any change.

- [X] T001 Verify branch `040-platform-ingestion-scope` is checked out, `.venv/` is active, and `python -m pytest tests/ -q` is green before touching anything.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the scope accessors, the seeding migration, and the exclude-word helper — every user story depends on these.

- [X] T002 [P] Add `title_excluded(title, words) -> str | None` to `core/title_gate.py` — case-insensitive word-boundary `\b{w}\b`, returns the first offending word (or `None`), mirroring the existing `title_matches_profile`.
- [X] T003 Add `get_ingestion_scope()` / `set_ingestion_scope(title_families, exclude, remote_or_hybrid)` typed accessors to `core/storage.py`, following the `get_freshness_days` coercion template (absent/malformed → `{"title_families": [], "exclude": [], "remote_or_hybrid": False}`; `set_*` validates list[str]/bool and raises `ValueError`).
- [X] T004 Add `_migrate_ingestion_scope(conn)` to `core/storage.py`, run **after** `_migrate_user_space_separation` in `_init_db`; seed `config.ingestion_scope` from the active profile (`job_titles` → `title_families`, `title_exclude` → `exclude`, `scrape_remote_or_hybrid` → `remote_or_hybrid`), record `ingestion_scope` in `migrations`. **No `job_scores` write.**

**Checkpoint**: scope accessors + migration + `title_excluded` in place, recorded in `migrations`.

---

## Phase 3: User Story 1 — Ingestion is driven by a platform scope, not a profile (Priority: P1) 🎯 MVP

**Goal**: `core/scrape.py` and every scraper read titles/exclude/remote-hybrid only from `config.ingestion_scope`; the redundant profile title gate is gone.

**Independent Test**: with the scope initialised from today's profile values, a broad scrape and a monitored scrape ingest the same jobs as before; changing the profile's `job_titles` no longer changes ingestion.

### Tests for User Story 1

- [X] T005 [P] [US1] Add a fixture test in `tests/test_ingestion_scope.py`: profile `job_titles` differs from `ingestion_scope.title_families` → `_run_broad_scrape` builds the `JobFilter` passed to a scraper's `fetch()` from the **scope** (assert the filter the mocked scraper receives), not the profile.

### Implementation for User Story 1

- [X] T006 [US1] In `core/scrape.py::_run_broad_scrape`, build `JobFilter` from `get_ingestion_scope()` + the freshness window (replacing the profile reads at lines ~331–336); remove the redundant `title_matches_profile` gate before `save_unscored` (line ~379).
- [X] T007 [US1] In `core/scrapers/greenhouse.py` (lines ~140–168), filter on `ingestion_scope["title_families"]` instead of `active_profile.job_titles`.
- [X] T008 [P] [US1] Add `scripts/ingestion_scope.py` CLI (`--show`, `--set-titles`, `--set-exclude`, `--remote-or-hybrid on|off`); each mutation read-modifies-writes the whole scope and re-prints `--show`; invalid input → stderr + exit `2`; empty `--set-titles` allowed (= open ingestion, warn).

**Checkpoint**: US1 independently testable — ingestion follows the scope.

---

## Phase 4: User Story 2 — Title judgement is a Tier 0 rule, stored in the user space (Priority: P1)

**Goal**: title filtering moves to a new **first** Tier 0 rule in `evaluate_for_profile` (no LLM), for **every** job; the monitored-only gate, `set_job_filtered_non_product`, and the scoring pre-filter title clauses are removed.

**Independent Test**: a job ingested under the scope but outside the profile's titles gets a `job_scores` row with `scored_by='tier_0'` and a `filtered: title …` reason, with no LLM call; `jobs.filtered_non_product` is never written; the feed still hides it.

### Tests for User Story 2

- [X] T009 [P] [US2] Extend `tests/test_title_gate.py` for `title_excluded` (word boundary: `intern` excludes `intern`, not `international`; empty words → `None`; case-insensitive).
- [X] T010 [P] [US2] Extend `tests/test_scorer_parsing.py` for the new first Tier 0 rule: non-matching title → `score=1`, `reason="filtered: title not in profile"`, `scored_by="tier_0"`; exclude word → `filtered: excluded title word (<w>)`; empty `job_titles` → open; assert no LLM call on the filtered branch.
- [X] T011 [P] [US2] Add guard tests in `tests/test_ingestion_scope.py`: (a) no `core/scrape.py` / `core/scrapers/**` reads `job_titles` / `title_exclude` / `scrape_remote_or_hybrid`; (b) no runtime code reads or writes `jobs.filtered_non_product` outside the migration (allow-list: the `_migrate_*` functions and the `models.py` dataclass field).

### Implementation for User Story 2

- [X] T012 [US2] In `core/scorer.py::evaluate_for_profile`, insert the **first** Tier 0 rule (before the denylist rule) using `title_matches_profile(job.title, profile.job_titles)` and `title_excluded(job.title, profile.title_exclude)`; both branches `score=1`, `scored_by="tier_0"`, `comp_flag` passed through, `country_code=None`.
- [X] T013 [US2] In `core/score.py`, remove the monitored-only title gate + `set_job_filtered_non_product` call (lines ~442–448) and remove the `title_contains` / `exclude_title_contains` clauses from `effective_pre_filter` (lines ~376–383), leaving only the location/remote clauses.
- [X] T014 [US2] In `core/storage.py`, delete `set_job_filtered_non_product` and drop the `SELECT j.filtered_non_product` columns from `get_jobs_for_scoring` (`storage.py:1717`) and `get_job_for_prepare` (`storage.py:2737`); keep the column itself and the `JobPosting.filtered_non_product` field (expand/contract, dropped at step 10).

**Checkpoint**: US1 + US2 both work; `filtered_non_product` is write-only in the schema, never in runtime code.

---

## Phase 5: User Story 3 — Released through the standard flow, no visible change (Priority: P1)

**Goal**: the step ships through the standard release flow with no ingestion-volume jump; docs and the one-line Settings note are in place.

- [X] T015 [US3] In `.specify/memory/constitution.md`, PATCH the Sync Impact Report to drop the known Constitution I violation ("`scrape.py` applique `title_matches_profile` avant sauvegarde") — FR-010.
- [X] T016 [P] [US3] Update `CLAUDE.md`: document `config.ingestion_scope` and that it is edited via `python -m scripts.ingestion_scope` (current-focus note already reflects the no-backfill decision).
- [X] T017 [P] [US3] In `tracker_views/settings.py`, add the read-only note next to the job-titles field: "Ingestion uses the platform scope — edit with `python -m scripts.ingestion_scope`" (FR-011; no other UI change).

**Checkpoint**: all three stories complete; docs consistent.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T018 Extend `tests/test_storage.py` (in-memory DB) for `get_ingestion_scope()`/`set_ingestion_scope()` round-trip, malformed-input coercion, and `_migrate_ingestion_scope` idempotency (second open changes nothing).
- [X] T019 Run `python -m pytest tests/ -q` — full suite green (new guard + scorer + title_gate + storage tests all pass).
- [ ] T020 Run `quickstart.md` §1–§6 against a copy DB (`/tmp/spec040.db`): scope seeded identically, parity **byte-identical** before scoring, then exactly `N` new Tier 0 title rows after `core.score --profile <active>` with no other score change.
- [ ] T021 Follow the release flow (FR-009): branch → staging on the exact SHA with US3 checks → `git merge --ff-only` → `./scripts/deploy.sh` → SHA check → next two scheduled runs within usual volume.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → **US1 (Phase 3)** and **US2 (Phase 4)** (US1/US2 share the foundational accessors; US2's Tier 0 rule and US1's scope are independent of each other) → **US3 (Phase 5)** → **Polish (Phase 6)**.

### User Story Dependencies

- **US1 (P1)**: needs Foundational (T003 accessors, T004 migration). Independent of US2.
- **US2 (P1)**: needs Foundational (T002 helper, T003 accessors for `get_freshness_days` already exists). Independent of US1.
- **US3 (P1)**: depends on US1+US2 (docs/release describe the finished change); its doc tasks T016/T017 are parallel.

### Within Each User Story

- Tests first (T005, T009–T011 must fail before their implementation tasks).
- Foundational before stories; models/helpers before services; core before integration.

### Parallel Opportunities

- T002 (title helper) and T003 (accessors) are independent files → parallel.
- T005, T009, T010, T011 are all test files → parallel within their stories.
- T008 (CLI) is a new file independent of T006/T007 → parallel with them.
- T016 (CLAUDE.md) and T017 (settings.py) are different files → parallel.

---

## Parallel Example: User Story 2 tests

```bash
Task: "Extend tests/test_title_gate.py for title_excluded …"
Task: "Extend tests/test_scorer_parsing.py for the new first Tier 0 rule …"
Task: "Add guard tests in tests/test_ingestion_scope.py …"
```

---

## Implementation Strategy

### MVP First (US1)

1. Phase 1 → Phase 2 (accessors + migration + helper).
2. Phase 3 (US1) → validate: ingestion follows the scope, parity on ingestion identical.
3. Then Phase 4 (US2) → validate: Tier 0 title rows, no flag write, feed unchanged.

### Incremental Delivery

1. Foundational → scope accessors live (no behaviour change yet).
2. US1 → ingestion reads the scope; profile edits no longer affect ingestion.
3. US2 → title judgement at scoring; flagged jobs converge on the next scoring run.
4. US3 + Polish → docs, guard suite green, release flow, quickstart validation.

---

## Notes

- `core/storage.py`, `core/scrape.py`, `core/scorer.py`, `core/score.py`, `core/scrapers/greenhouse.py` are on the "NEVER modify" list but are explicitly in scope for this spec (roadmap step 4) — surgical edits only.
- The expected-change check (quickstart §4) needs FR-006's pre-filter title-clause removal or `N` will be `0` (see `research.md` §9).
- Commit after each task or logical group; keep diffs minimal (Constitution V).
