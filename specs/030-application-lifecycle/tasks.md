# Tasks: Application Lifecycle Tracking + Date-Correct Reports (spec 030)

**Input**: `specs/030-application-lifecycle/` — `spec.md` (8 user stories), `plan.md`, `research.md`, `mockup.html` (approved).

**Tests**: REQUIRED — SC-006 mandates tests-first for report-by-date, notes-edit invariance, derived labels, reasons, round numbering, FR-001 transition guards, FR-009 action sets, FR-027 date validation, FR-031 provenance/manual-wins, US7/SC-007, migration idempotence.

**Organization**: tasks grouped by user story. Foundational storage work blocks every story.

**Constitution guardrails (spec 030)**: in-scope = `storage.py`, `export_jobs.py`, `tracker_views/{job_helpers,jobs,job_detail,reports,dashboard,shared}.py`, `scripts/dedupe_jobs.py`, `scripts/dedupe_title_company.py`, `preference_report.py`, `context_tuner.py`, `tests/test_storage.py`, one migration. **NEVER touch** `models.py`, `profiles.py`, `scrape.py`, `scorer.py`, `main.py`, `llm.py`, `cv_agent/`, `scrapers/`, `tracker_legacy.py`, `email_monitor.py`, `migrate_*.py`.

---

## Phase 1: Setup

**Purpose**: confirm a green baseline before any change.

- [X] T001 Confirm working branch (`030-application-lifecycle`, else `main` per current state), then run `python -m pytest tests/` and record the green baseline (162 tests). No code changes.

---

## Phase 2: Foundational — storage core (blocking prerequisite)

**Purpose**: schema, migration, single entry point, derived-value helpers, and report query builders in `storage.py`. Every user story depends on this phase. **Tests are written first and expected to fail until T011–T016 land.**

**⚠️ CRITICAL**: no user story work begins until this phase is green.

### Tests (write first — `tests/test_storage.py`, in-memory DB)

> All test tasks below add functions to the **same** file `tests/test_storage.py`; they are independent but not `[P]` (shared file — apply sequentially in one sitting).

- [X] T002 Test derived labels — every variant: `Applied`, `Interviewing · round N` (+` · next dd/mm`), `Offer`, `Rejected — no interview`, `Rejected — after N interview(s)`, `Rejected — offer withdrawn by employer`, `Withdrawn — no interview`, `Withdrawn — after N interview(s)`, `Withdrawn — offer declined` (FR-003).
- [X] T003 Test round numbering + interviews held: 1st/2nd interview → `round 1`/`round 2`; future interview counts as scheduled, not held (FR-003).
- [X] T004 Test reasons stored un-prefixed and exported with `Employer:` / `Me:` prefix from event type (FR-002/018).
- [X] T005 Test FR-001 transition guards — reject disallowed transitions (e.g. `scraped → interviewing`, `rejected → offer`, `interviewing → applied`).
- [X] T006 Test FR-027 date validation on the API path — future application/decision/withdrawal refused; future interview allowed; event before application refused; interview after decision refused; refused write names the conflicting event + date.
- [X] T007 Test FR-031 provenance & manual-wins — agent write with `source_ref` is idempotent (duplicate → no-op); non-manual edit/delete of a `manual` event refused + logged; a manual edit of an agent event flips `source` to `manual`.
- [X] T008 Test migration idempotence + no-duplicate + provenance — run twice → no change; jobs with a pre-existing `application_submitted` don't get a second; undatable jobs logged not dated; `source` set to `manual`/`migration` correctly (FR-024/025/030).
- [X] T009 Test report by application date (US1) + notes-edit invariance — job applied 08-20/rejected 09-04 appears in August (`Rejected — no interview`, decision 04/09), absent from September; editing `job_tracking.notes` or a reason alone changes no report output and no event date (FR-007/016).
- [X] T010 Test back-dated & out-of-order entry (US7/SC-007) — enter Applied 03/09 + Rejected 18/09 on 30/09, then add interview 10/09 via "+ Add past event"; Applications/Activity output equals the day-logged equivalent (status `rejected`, label `Rejected — after 1 interview`).

### Implementation

- [X] T011 `storage.py` — `VALID_STATUSES` += `interviewing`, `offer`, `withdrawn`; `INTERACTION_TYPES` += `interview_invited`, `withdrawn`; add `notes TEXT`, `source TEXT`, `source_ref TEXT` to the `interactions` `CREATE TABLE` (both the `SCHEMA` string and the inline "if not exists" block) (FR-001/002/024).
- [X] T012 `storage.py` — new `_migrate_application_lifecycle(conn)`: Part A adds the three columns via `PRAGMA table_info` guard + partial unique index `(job_id, type, source_ref) WHERE source_ref IS NOT NULL`; Part B backfills `application_submitted`/`decision_received` from `status_history` with `source='migration'`, sets pre-existing auto-logged rows to `source='manual'`, logs undatable jobs; records `backfill_lifecycle_events`. Call it in the migration block after `_migrate_monitored_companies(conn)` (FR-024/025).
- [X] T013 `storage.py` — new `record_lifecycle_event(job_id, *, event, occurred_at, new_status, outcome=None, notes=None, source="manual", source_ref=None)`: FR-001 transition guard, FR-027 date validation, FR-031 idempotency + manual-wins, FR-004 company resolve/create, one-transaction event insert + `job_tracking.status` + `status_history`, never calls `_auto_log_status_interaction` (FR-004/005/006/026/027/028/030/031). Docstring documents the agent-call contract (FR-030); no `import streamlit`.
- [X] T014 `storage.py` — read/derived helpers: `get_lifecycle_events(job_id)` (chronological, incl. `source`/`source_ref`), `get_lifecycle_summary(job_id)` (derived view: application date, interviews held, next interview, round, decision, label, reason), `update_lifecycle_event(event_id, *, occurred_at=None, notes=None)`, `delete_lifecycle_event(event_id)` (FR-003/014/015/031).
- [X] T015 `storage.py` — report query builders shared by UI + CLI: `build_application_rows(...)` (by application date), `build_activity_rows(...)` (by event date), `application_date_missing(...)` (FR-016/020/021/022).
- [X] T016 `storage.py` — FR-023 cohort fixes: `get_engaged_job_keys` and `get_jobs_for_scoring` add `interviewing`/`offer`/`withdrawn` (research.md #5/#6).

**Checkpoint**: `python -m pytest tests/` green — storage core complete; all user stories can now proceed.

---

## Phase 3: User Story 1 — Date-correct Applications report (Priority: P1) 🎯 MVP

**Goal**: the Applications report selects rows by application date (never `job_tracking.changed_at`), with derived current stage, reason, and ORP-helper columns in English.

**Independent Test**: September 2026 shows only September applications; the 6 August applications rejected in September appear in August with `Rejected — no interview`.

- [X] T017 [US1] `export_jobs.py` — replace `build_export_rows` / `STATUS_TO_RESULTAT` with an event-based Applications builder that calls `storage.build_application_rows`; English columns (FR-018), `Reason` with `Employer:`/`Me:` prefix, `Interview held` + `ORP result` derived from events (FR-018/022).
- [X] T018 [US1] `tracker_views/reports.py` — Applications preset uses `storage.build_application_rows`; metrics `Entries / Pending / Interviewing / Offer / Rejected / Withdrawn` (FR-019); replace the "Statuses" multiselect with a "Current stage" filter defaulting to all (FR-017).

**Checkpoint**: US1 works end-to-end against a job applied 08-20 / rejected 09-04.

---

## Phase 4: User Story 6 — Backfill surfacing (Priority: P1, prerequisite of US1)

**Goal**: lifecycle jobs whose application date could not be recovered are not silently dropped.

**Independent Test**: the 9 undatable rejected jobs appear under "Application date missing".

- [X] T019 [US6] `tracker_views/reports.py` — collapsed "Application date missing (N)" list from `storage.application_date_missing(...)`, each row linking to the job's timeline (FR-021).

> The backfill migration itself is foundational (T012); this phase only surfaces its undatable remainder.

---

## Phase 5: User Story 2 — "Interviewing" action + interview logging (Priority: P1)

**Goal**: contextual action bar with a primary `🎤 Interviewing` action on `applied`, `🎤 + Interview` on `interviewing`, both with inline date prompts.

**Independent Test**: click Interviewing on an `applied` job, confirm with interview date → state label "Interviewing · round 1 · next 14/10"; second interview → "round 2".

- [X] T020 [US2] `tracker_views/job_helpers.py` — convert `_render_action_bar` to contextual (FR-008/009/010/011): per-state primary/secondary/⋯ via `st.button(type="primary")` + `st.popover`; `_derive_state()` returns `interviewing`/`offer`/`withdrawn`; state caption shows the derived label (FR-003).
- [X] T021 [US2] Test FR-009 per-state action sets — add a pure, importable mapping of derived-state → (primary, secondaries, ⋯) and assert it matches the FR-009 table for every state (kept testable without Streamlit render).
- [X] T022 [US2] `tracker_views/job_helpers.py` — `🎤 Interviewing` / `🎤 + Interview` actions open inline date prompts (invite + first interview, at least one required; later interviews single date), route through `record_lifecycle_event`, Cancel writes nothing (FR-012).

**Checkpoint**: US2 works on the Jobs page and Job detail action bar (both call `_render_action_bar`).

---

## Phase 6: User Story 3 — End states: rejection with/without interview, offer, withdrawal (Priority: P1)

**Goal**: record rejection (before/after interviews, optional employer reason), offer, and withdrawal (optional my reason, incl. declining an offer).

**Independent Test**: reject an `applied` job → "Rejected — no interview"; reject an `interviewing` job with 2 interviews → "Rejected — after 2 interviews"; withdraw from `offer` → "Withdrawn — offer declined".

- [X] T023 [US3] `tracker_views/job_helpers.py` — `❌ Rejected`, `🎉 Offer`, `🏳️ Withdrawn` actions with inline date + optional reason prompts (Employer's reason for rejections, My reason for withdrawals), route through `record_lifecycle_event` (FR-012). Offer rescinded = Rejected from `offer`; offer declined = Withdrawn from `offer` (FR-001).

**Checkpoint**: US2 + US3 together cover the full action bar for every derived state.

---

## Phase 7: User Story 7 — Month-end batch update with back-dated events (Priority: P1)

**Goal**: back-dating and out-of-order entry are effortless at month end (session-sticky date pre-fill + context in the prompt).

**Independent Test**: on 30/09 record Applied 03/09 then Rejected 18/09, then add an interview 10/09 — reports match day-logged entry.

- [X] T024 [US7] `tracker_views/job_helpers.py` — date prompts pre-fill the last date confirmed this session (fallback today) and show the job's application date + last event date for context (FR-029).

> Out-of-order validity is already enforced by `record_lifecycle_event` (T013, FR-027); this phase is the UI convenience layer.

---

## Phase 8: User Story 4 — Application timeline on Job detail (Priority: P2)

**Goal**: chronological timeline with edit/delete and "+ Add past event".

**Independent Test**: edit the application date from 02/10 to 30/09 → the job moves from the October to the September report.

- [X] T025 [US4] `tracker_views/job_detail.py` — "Tracking status" selectbox includes `interviewing`/`offer`/`withdrawn`, routed through `record_lifecycle_event` with the same date prompt (FR-013).
- [X] T026 [US4] `tracker_views/job_detail.py` — Application timeline section (only when ≥1 lifecycle event): chronological list with date, type, round, outcome, notes, source badge for non-manual events (FR-014/031); Edit/Delete per event via `update_lifecycle_event`/`delete_lifecycle_event`, prompting before a state-affecting change (FR-015).
- [X] T027 [US4] `tracker_views/job_detail.py` — "+ Add past event" control (invite/interview/decision/withdrawal, any valid date, any order) routed through `record_lifecycle_event` (FR-028); a state-affecting add prompts before the status flips (FR-015).

**Checkpoint**: US4 works; correcting a date re-flows the reports immediately.

---

## Phase 9: User Story 5 — Activity report (Priority: P2)

**Goal**: a second report listing every lifecycle event in the period, regardless of application month.

**Independent Test**: an application from 25/09 with an interview on 14/10 appears in Activity 10/2026 (not in Applications 10/2026).

- [X] T028 [US5] `export_jobs.py` — Activity builder calling `storage.build_activity_rows` (FR-020/022).
- [X] T029 [US5] `tracker_views/reports.py` — Activity preset (Event date, Event incl. round, Company, Title, Application date, Current stage, Reason, URL, ID) (FR-020).

**Checkpoint**: US5 works; Applications and Activity presets coexist.

---

## Phase 10: User Story 8 — Ready for a future agent (Priority: P3)

**Goal**: the storage API is programmatic, provenance-aware, and documented so a future email agent can call it safely. No agent is built.

**Independent Test**: `record_lifecycle_event(..., source="agent:test", source_ref="<msg-123>")` stores the event with that date/source; a repeat call is a no-op; changing a manual event's date is refused.

- [X] T030 [US8] `storage.py` — verify the `record_lifecycle_event` docstring documents the full agent contract (params, `source`/`source_ref` semantics, idempotency, manual-wins) and that `storage.py` has no Streamlit import (FR-030). Fill any docstring gaps.

> The API, provenance columns, idempotency and manual-wins rules already landed in foundational (T011–T013, T007). This phase is the contract + verification checkpoint.

---

## Phase 11: Polish & cross-cutting — FR-023 consumer sweep + full validation

**Purpose**: every remaining status-list/cohort occurrence learns the three new statuses (research.md FR-023 inventory), then full regression.

- [X] T031 [P] `tracker_views/shared.py` — `_ENGAGED_STATUSES` += `interviewing`, `offer`, `withdrawn` (research.md #11).
- [X] T032 [P] `tracker_views/dashboard.py` — `status_order` / `status_icons` += the three statuses (+ icons); widen the 6-column pipeline row to 9 (research.md #12).
- [X] T033 [P] `tracker_views/jobs.py` — status filter += the three statuses (research.md #14).
- [X] T034 [P] `scripts/dedupe_jobs.py` + `scripts/dedupe_title_company.py` — update the dead `NON_NEW_STATUSES` constant += the three statuses (or delete it); logic already correct via `!= "new"` (research.md #18).
- [X] T035 [P] `preference_report.py` — add the three statuses to every `'applied'`-as-positive-interest cohort; leave `archived` true-negative cohorts (research.md #19).
- [X] T036 [P] `context_tuner.py` — add the three statuses to the `'applied','rejected'` positive cohorts; leave `archived` cohorts (research.md #20).
- [X] T037 Run `python -m pytest tests/`; fix any regressions until green (SC-006).
- [X] T038 Validate SC-001/SC-007 end-to-end on the dev DB (in-memory or a scratch copy): September shows only September applications; the US7 scenario reproduces the day-logged report.

**Checkpoint**: all 8 stories + FR-023 sweep done; full suite green.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: none.
- **Foundational (P2)**: depends on Setup. **BLOCKS all user stories.**
- **US1 (P3)**, **US6 (P4)**: depend on Foundational. US6 depends on US1 (both edit `tracker_views/reports.py`).
- **US2 (P5)**, **US3 (P6)**, **US7 (P7)**: depend on Foundational; US3 depends on US2; US7 depends on US2/US3 (all edit `tracker_views/job_helpers.py`).
- **US4 (P8)**: depends on Foundational (uses storage helpers + entry point directly); edits `tracker_views/job_detail.py`.
- **US5 (P9)**: depends on Foundational; edits `export_jobs.py` + `tracker_views/reports.py` (after US1/US6 leave that file stable).
- **US8 (P10)**: depends on Foundational.
- **Polish (P11)**: depends on Foundational; parallelizable; run after the stories that own each file.

### Within each user story

Tests (where present) are written and fail before implementation; storage before UI; entry point before actions/timeline; report builders before report presets.

### Parallel opportunities

- Foundational test tasks T002–T010 are independent functions (single file — apply together).
- Polish tasks T031–T036 are `[P]` (distinct files, no cross-dependencies).

---

## Implementation Strategy

1. **MVP first** = Phase 1 + Phase 2 + US1 (Phases 1–3): the date-correct Applications report is the bug that blocks the monthly ORP declaration today.
2. **Incremental**: US6 → US2 → US3 → US7 (the lifecycle actions and month-end flow) → US4 → US5 → US8 → Polish.
3. Deploy only after all phases pass and the mockup-derived behavior is verified (SC-001/SC-007).
