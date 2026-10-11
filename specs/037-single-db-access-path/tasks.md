---
description: "Task list for spec 037 — Single DB Access Path + Single LLM Provider (roadmap step 2a + 2c)"
---

# Tasks: Single DB Access Path + Single LLM Provider

**Input**: Design documents from `specs/037-single-db-access-path/` — `plan.md`, `spec.md`,
`research.md`, `data-model.md`, `quickstart.md`, `inventory.md`.

**Tests**: Requested (spec FR-002 unit tests, FR-005 + FR-009 guard tests). Guard tests are written
first in the Foundational phase and expected to FAIL on current HEAD (raw SQL and provider residue
are still present); the unit tests are written alongside each new `JobStorage` method.

**Branch**: `037-single-db-access-path`.

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3

---

## Phase 1: Setup

**Purpose**: Confirm a clean, green starting point before any migration.

- [X] T001 Checkout branch `037-single-db-access-path`, activate `.venv/`, and run `python -m pytest tests/ -q` — confirm the full suite is green before touching code.

---

## Phase 2: Foundational (Guard Tests — the enforcement boundary)

**Purpose**: The two guard tests define this feature's definition-of-done. Write them first and
confirm each FAILS on current HEAD (proving the guard is not vacuous), then they go GREEN as US1
and US2 land.

- [X] T002 [P] Write `tests/test_guard_db_access.py` — AST guard (spec FR-005) walking `core/` (minus `core/storage.py`), `tracker_views/`, `tracker.py`, failing on `_conn` attribute access on a non-`self` object, `sqlite3.connect`, and `import sqlite3`; include a negative fixture per form; confirm it FAILS now (raw SQL present).
- [X] T003 [P] Write `tests/test_guard_llm_provider.py` — text-scan guard (spec FR-009) over runtime `*.py` (minus `core/llm.py`), `.env.example`, `docker-compose.yml`, failing case-insensitively on `groq|openai|anthropic|deepseek|ollama`; include a negative fixture; confirm it FAILS now (Groq + DeepSeek residue present).

**Checkpoint**: Both guard tests exist and FAIL on current HEAD — they detect the violations they
are meant to prevent.

---

## Phase 3: User Story 1 — Every DB access goes through `JobStorage` (P1) 🎯 MVP

**Goal**: Replace all raw-SQL sites (14 in `tracker_views/`, 7 in `core/`) with named, unit-tested
`JobStorage` methods, identical SQL and transaction boundaries (FR-001–FR-006).

**Independent Test**: DB guard test green + `grep -rnE "_conn\(|sqlite3\.connect|import sqlite3" core/ tracker_views/ tracker.py | grep -v core/storage.py` → no output; full suite green; parity identical on staging.

> All new methods go into `core/storage.py`; unit tests into `tests/test_storage.py` (in-memory DB).
> Tasks share those two files, so they run sequentially in order.

- [X] T004 [US1] Add `get_database_stats()` and `get_contacts_by_role_family()` to `core/storage.py` + unit tests in `tests/test_storage.py`; wire `tracker_views/dashboard.py:115` and `tracker_views/settings.py:780` to the **shared** `get_database_stats()` (FR-003) and `tracker_views/settings.py:798` to `get_contacts_by_role_family()`.
- [X] T005 [US1] Add `update_contact_fields()` (allowlist `{is_unverified, last_verified_at, is_current, notes}`) and `bulk_mark_contacts_verified()` to `core/storage.py` + unit tests; wire `tracker_views/contact_detail.py:77,86,97,134` and `tracker_views/contacts.py:81` (bulk-verify keeps one `with self._conn()` transaction).
- [X] T006 [US1] Add `get_tracking_status()` and `get_contacts_discovered_for_job()` to `core/storage.py` + unit tests; wire `tracker_views/job_detail.py:67` and `:340`.
- [X] T007 [US1] Add `"notes"` to `_ALLOWED_COMPANY_FIELDS` in `core/storage.py:2650`; wire `tracker_views/settings.py:488,509` → `set_company_monitored`, and `tracker_views/company_detail.py:280` + `tracker_views/forms.py:52` → `update_company_fields` (reused, unchanged behaviour).
- [X] T008 [US1] Add `get_calibration_signals()` to `core/storage.py` + unit test (all 8 SELECTs → structured dict); refactor `core/context_tuner.py:41` to call it (module keeps only prompt-building).
- [X] T009 [US1] Add `delete_scores_for_profile()` and `get_jobs_to_score()` to `core/storage.py` + unit tests; wire `core/score.py:175` and `:317` (keep the same SQL and call shape — no N+1). *Deviation: `_get_jobs_to_score` was dead code (never called) AND referenced `s.status` (no such column since Phase 1e.4) — removed instead of migrated; the live path already uses `get_jobs_for_scoring`.*
- [X] T010 [US1] Add `get_best_score_profile_id()` to `core/storage.py` + unit test; wire `core/prepare.py:552`.
- [X] T011 [US1] Add `get_export_seed_companies()` to `core/storage.py` + unit test; wire `core/export_seed.py:20`.
- [X] T012 [US1] Add `get_jobs_missing_description()` and `set_job_description()` to `core/storage.py` + unit tests; wire `core/backfill_descriptions.py:74`.
- [X] T013 [US1] Add the 8 preference-report methods to `core/storage.py` (`get_cohort_jobs`, `get_applications_count`, `get_classified_jobs`, `get_company_sector`, `get_calibration_deltas`, `get_archived_notes`, `get_few_shot_anchors`, `get_action_item_candidates`) + unit tests; refactor `core/preference_report.py` to call them (drop its `_connect()`/`sqlite3`/`PRAGMA query_only`); reuse `get_database_stats()` for `_section_header` counts. *Deviation: fixed a stale `j.company_country` → `c.company_country` (+ `LEFT JOIN companies`) in the action-items country-candidates query — same latent bug as T008.*
- [X] T014 [US1] Run `python -m pytest tests/ -q` and the quickstart sweep `#4`/`#5` — DB guard test GREEN, zero raw SQL outside `core/storage.py` + allow-list. *Sweep #4 (provider residue) still red — that is US2's guard, expected until T015–T021.*

**Checkpoint**: US1 complete — all DB access routes through `JobStorage`; DB guard test green.

---

## Phase 4: User Story 2 — Only `core/llm.py` knows the LLM provider (P2)

**Goal**: Remove every provider name (Groq **and** DeepSeek) from runtime code outside `core/llm.py`;
fix the onboarding wizard; delete `core/email_monitor.py`; rewrite `.env.example` (FR-007–FR-010).

**Independent Test**: Provider guard test green + `grep -riE "groq|openai|anthropic|deepseek|ollama" core/ tracker_views/ tracker.py .env.example docker-compose.yml | grep -v core/llm.py` → no output; fresh onboarding asks for `LLM_API_KEY`, not Groq.

- [X] T015 [P] [US2] Reword provider residue in `core/scorer.py`: `DeepSeek` → `LLM` in the docstring/print sites (`:567,576,610,621,654,656,660,886,906`); the three control-flow guards `:609,653,885` → `if "LLM_API_KEY" in msg or "not set" in msg:`; the `:707` Groq comment → provider-agnostic.
- [X] T016 [P] [US2] Reword provider residue in `core/prepare.py` (`:268` docstring, `:279` `"deepseek-chat"` → `llm.MODEL`, `:734` fallback log), `core/company_researcher.py:251` comment, and `core/cv_agent/cli.py:242` string (`LLM_API_KEY / DEEPSEEK_API_KEY` → `LLM_API_KEY`).
- [X] T017 [P] [US2] In `tracker_views/settings.py:21` change the secret entry `("DEEPSEEK_API_KEY", …)` → `("LLM_API_KEY", "LLM API key (LLM extraction + scoring)", True)`; in `tracker_views/onboarding.py:435–459` replace the Groq wizard with a provider-agnostic `LLM_API_KEY` guard (write via `shared.set_secret`). *Deviation: used `if not env_is_set("LLM_API_KEY"):` instead of `if not llm.is_configured():` — `llm._client` is cached at import and stays stale after `set_secret`+`st.rerun()`, so `is_configured()` would re-show the wizard forever; the env-var check matches the original Groq guard and the Settings page and works correctly on save+rerun.*
- [X] T018 [P] [US2] Rewrite `.env.example` to document `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL` (per `core/llm.py`); drop `GROQ_API_KEY`, `GEMINI_API_KEY`, and the Hydroxide/email_monitor block; keep `GMAIL_*`, `NOTIFY_TO`, `JOPLIN_TOKEN`, `X_RAPIDAPI_KEY`.
- [X] T019 [P] [US2] `git rm core/email_monitor.py`; remove the `email-monitor` service block from `docker-compose.yml`; drop `email-monitor` from the `scripts/deploy.sh:37` build list (`docker compose build tracker agent`).
- [X] T020 [US2] Update `email_monitor` references: `core/storage.py:3445` (`find_jobs_by_company` docstring), `CLAUDE.md` Infrastructure section, `README.md`, `docs/freelance-sourcing-map.md`, `docs/restore-procedure.md`, `docs/migration-checklist.md`; append the removal record to `docs/history.md` (purpose, last commit containing it, `git show <sha>:core/email_monitor.py`).
- [X] T021 [US2] Run `python -m pytest tests/ -q` and quickstart sweep `#4` — provider guard test GREEN, zero provider names outside `core/llm.py`.

**Checkpoint**: US2 complete — only `core/llm.py` names a provider; provider guard test green.

---

## Phase 5: User Story 3 — Released through the standard flow, no behaviour change (P1)

**Goal**: Prove parity and release per FR-011 / SC-004 / SC-005. Gated on the user's "go" at each
deploy step; avoid cron windows (07:00/11:00/16:00).

**Independent Test**: Staging checklist green, parity identical, deployed SHA = staged SHA, health OK, next cron `success`.

- [ ] T022 [US3] On verva, `./scripts/staging.sh up <full-sha>`; run the US3-1 checklist (dashboard counts, settings counts + contacts-by-role, company-monitoring toggle, job detail, contact-detail edit actions, contacts bulk-verify, company-detail notes, `forms.py` add-company dialog); run `python -m core.score --extract`, `python -m core.scrape --monitored-only --no-score`, and the touched preference-report / context-tuner entry points.
- [ ] T023 [US3] Run parity (`scripts/fingerprint` + `scripts/compare_fingerprints`) against the pre-change baseline — result "fingerprints identical"; tear down staging.
- [ ] T024 [US3] On user "go": `git merge --ff-only <full-sha>` onto `main`, `./scripts/deploy.sh`, `tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501`; confirm `git -C /opt/job-agent rev-parse HEAD` = merged SHA, health check OK, remove any orphan `job-email-monitor` container/image, and next scheduled cron run reports `success`.

**Checkpoint**: US3 complete — released with no behaviour change.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T025 [US2] FR-012: drop the resolved known violations — remove the "raw SQL in `tracker_views/`" note from `CLAUDE.md` (Code style section) and the "Groq residue" + "raw SQL" known-violation entries from `.specify/memory/constitution.md` (constitution PATCH).
- [ ] T026 Run the full quickstart validation: `python -m pytest tests/ -q` (100% pass), both guard tests green, both grep sweeps empty, and the pipeline entry points complete normally.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → **US1 (Phase 3)** → **US2 (Phase 4)** → **US3 (Phase 5)** → **Polish (Phase 6)**.
- US1 and US2 are independent of each other once Foundational completes (US1 touches DB access; US2 touches provider residue + `email_monitor`), but both must be green before US3.

### Within Each Story

- Guard tests (Phase 2) written first, confirmed failing, then green.
- US1: add `JobStorage` methods + unit tests, then wire each caller file.
- US2: reword/delete residue files (parallelizable), then update references, then verify.

### Parallel Opportunities

- **Phase 2**: T002 and T003 run in parallel (different test files).
- **Phase 4**: T015, T016, T017, T018, T019 touch disjoint files and run in parallel; T020 follows T019.
- **Phase 1 + US1**: sequential — every US1 task appends to `core/storage.py` and `tests/test_storage.py`.

---

## Implementation Strategy

### MVP First (US1 only)

Complete Phase 1 → Phase 2 (guard tests failing) → Phase 3 (US1). Stop and validate: DB guard test
green, full suite green, parity identical on staging. This alone delivers the step-3 prerequisite.

### Incremental Delivery

1. Setup + Foundational → guards written and failing.
2. US1 → DB access unified → validate.
3. US2 → provider unified, `email_monitor` gone → validate.
4. US3 → staging + parity + deploy.
5. Polish → drop known violations, final validation.

---

## Notes

- `core/storage.py`, `core/scorer.py`, `tracker_views/onboarding.py` are Stable-core edits — each is
  the explicit subject of step 2a/2c (justified in `plan.md` Complexity Tracking); keep them
  additions-only / comment-string-only / config-flow-only respectively.
- `docs/roadmap-api.md` is intentionally not edited (its `:66` is the step's own plan description).
- Reuse `set_company_monitored` and `update_company_fields` verbatim (behaviour unchanged).
- Commit after each task or logical group; run `tests/test_storage.py` before marking done.
