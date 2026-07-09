# Tasks: Residence & Relocation Model

**Input**: Design documents from `specs/022-residence-relocation-model/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, quickstart.md

**Tests**: Mock-only — no unit test tasks. Validation via `score.py --mock` and quickstart.md.

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, …)

---

## Phase 1: Setup — Profiles data model (Priority: P1) 🎯

**Goal**: Add `relocation` and `contract_geo` fields to `SearchProfile` with full round-trip and back-compat synthesis. This is the foundation — everything else reads these fields.

**Independent Test**: `from_criteria(to_criteria_dict(UNIFIED_JC))` round-trips correctly. Legacy criteria dict (no `relocation` key) synthesizes Swiss-only single-residence form with Russia absent.

### Implementation for US1

- [ ] T001 [US1] Add `relocation: dict = field(default_factory=dict)` and `contract_geo: dict = field(default_factory=dict)` to `SearchProfile` dataclass in `profiles.py`
- [ ] T002 [US1] Add `relocation` and `contract_geo` keys to `to_criteria_dict()` method in `profiles.py`
- [ ] T003 [US1] Add back-compat synthesis in `from_criteria()` in `profiles.py` — when `relocation` is absent, synthesize Swiss-only single-residence form from `work_mode_geography`; when `contract_geo` is absent, synthesize default (excludes Russia)
- [ ] T004 [US1] Seed `relocation` and `contract_geo` in `UNIFIED_JC` with the confirmed values from spec §Target data model in `profiles.py`
- [ ] T005 [US1] Verify round-trip: `python -c "from profiles import UNIFIED_JC; d = UNIFIED_JC.to_criteria_dict(); p = SearchProfile.from_criteria('test','Test',d); assert p.relocation == UNIFIED_JC.relocation; assert p.contract_geo == UNIFIED_JC.contract_geo; print('OK')"`

**Checkpoint**: `SearchProfile` carries `relocation` and `contract_geo`. Legacy criteria dicts synthesize correctly. No other file touched.

---

## Phase 2: User Story 2 — Engagement resolution (Priority: P1) 🎯

**Goal**: Implement `_resolve_engagement()` replacing `_geography_for_mode()` as the single geography/feasibility enforcement point in `evaluate_for_profile()`. Contract legality, per-residence feasibility, salary floor, rationale injection. Fix comp_flag home derivation.

**Independent Test**: `python score.py --mock --profile unified_jc` — all 11 existing cases in band; MoscowChain feasible at cost 4 (or 4–8 band if RUB unparsed), MoscowBank Tier-0 blocked, MoscowStartup Tier-0 blocked (or 4–8 if RUB unparsed), ParisChain feasible at cost 2.

### Implementation for US2

- [ ] T006 [US2] Add `_RESIDENCE_ZONES` dict and `_zone_of(company_country)` function in `scorer.py` — maps country names to residence zone keys (most-specific match wins, EU-27 → "eu")
- [ ] T007 [US2] Add `Engagement` dataclass (feasible_residences, cheapest_residence, relocation_cost, blocked_reason, rationale) in `scorer.py`
- [ ] T008 [US2] Implement `_resolve_engagement(job, profile) -> Engagement` in `scorer.py` — contract legality rule, per-residence feasibility loop, salary floor (three-state), result assembly with rationale prose
- [ ] T009 [US2] Replace Tier-0 rule 5 (per-mode geography, lines 763-779) in `scorer.py` with call to `_resolve_engagement()` — infeasible → Tier-0 reject score 2; feasible → pass to Tier 1 with `relocation_cost` and `residence_base` threaded
- [ ] T010 [US2] Replace comp_flag home-country hardcode (`"Switzerland"`) in `scorer.py` — derive home from zero-cost residence (`min(profile.relocation, key=lambda r: profile.relocation[r]["cost"])`)
- [ ] T011 [US2] Thread `relocation_cost` and `residence_base` through `_evaluation_result()` in `scorer.py` — add to result dict (nullable, following `comp_flag` pattern)
- [ ] T012 [US2] Inject relocation rationale prose (when cost > 0) into `profile_context` before Tier-1 LLM call in `scorer.py` — same pattern as monitoring note injection
- [ ] T013 [US2] Delete `_geography_for_mode()` and its definition in `scorer.py` — confirm zero remaining callers with grep

**Checkpoint**: `_geography_for_mode()` is gone. Engagement resolution is the single geography enforcement point. Mock test passes with existing cases in band.

---

## Phase 3: User Story 3 — Schema migration + persistence (Priority: P2)

**Goal**: Add `relocation_cost` and `residence_base` columns to `job_scores` following the comp_flag Phase 8 migration pattern. Persist both in `save_scored()`. Expose in tracker queries.

**Independent Test**: `python -m pytest tests/test_storage.py -q` — all 146 pass (plus any new column-existence tests). Columns visible in sqlite3 pragma.

### Implementation for US3

- [ ] T014 [US3] Add Phase 9 inline migration in `storage.py` — `PRAGMA table_info` guard pattern, `ALTER TABLE job_scores ADD COLUMN relocation_cost INTEGER`, same for `residence_base TEXT`
- [ ] T015 [US3] Add `relocation_cost` and `residence_base` to `save_scored()` in `storage.py` — INSERT column list, VALUES placeholders, ON CONFLICT DO UPDATE SET, and parameter binding
- [ ] T016 [US3] Add `relocation_cost` and `residence_base` to `get_digest()` SELECT in `storage.py` — `s.relocation_cost`, `s.residence_base`
- [ ] T017 [US3] Add `relocation_cost` and `residence_base` to `get_all_for_tracker()` SELECT in `storage.py` — needed for job card badge in US6
- [ ] T018 [US3] Run `python -m pytest tests/test_storage.py -q` — 100% pass

**Checkpoint**: New columns exist, persist, and are returned by tracker query methods. Tests green.

---

## Phase 4: User Story 4 — Mock cases (Priority: P2)

**Goal**: Add 4 new MOCK_JOBS entries exercising relocation, salary floor, and contract geography. Two RUB cases use `requires_spec_023` placeholder bands until RUB extraction lands.

**Independent Test**: `python score.py --mock --profile unified_jc` — 15 cases total, all in band.

### Implementation for US4

- [ ] T019 [US4] Add MoscowChain mock job to `MOCK_JOBS` in `score.py` — remote RU, salary above floor → feasible cost 4
- [ ] T020 [US4] Add MoscowBank mock job to `MOCK_JOBS` in `score.py` — on-site RU → infeasible (russia.work_modes excludes on-site)
- [ ] T021 [US4] Add MoscowStartup mock job to `MOCK_JOBS` in `score.py` — remote RU, salary below floor → infeasible
- [ ] T022 [US4] Add ParisChain mock job to `MOCK_JOBS` in `score.py` — hybrid FR, Web3 → feasible cost 2, high band
- [ ] T023 [US4] Add corresponding expectation entries in `_run_mock()` in `score.py` — use `requires_spec_023` comment and `(4, 8)` placeholder bands for the two RUB-salary cases; `(1, 2)` for Tier-0 rejects; `(7, 9)` for ParisChain
- [ ] T024 [US4] Run `python score.py --mock --profile unified_jc` — confirm all 15 cases in band

**Checkpoint**: New mock cases pass. Moscow on-site and low-salary deterministic rejections verified. Paris hybrid passes through cost 2.

---

## Phase 5: User Story 5 — Settings UI (Priority: P3)

**Goal**: Replace per-mode geography widgets in `_render_profile_editor()` with a Residence & relocation section. One row per residence key with cost slider, work-mode multiselect, optional salary-floor input, remote employer countries textarea. Contract geo editor. Save block persists `relocation` and `contract_geo`.

**Independent Test**: Launch Streamlit, navigate to Settings → Profile Editor, verify new section renders. Add France row, save, confirm criteria JSON persists via sqlite3.

### Implementation for US5

- [ ] T025 [US5] Remove per-mode geography widgets from `_render_profile_editor()` in `tracker_views/settings.py` — the on-site/hybrid/remote country textareas; do NOT delete the fields from the dataclass or criteria
- [ ] T026 [US5] Add "Residence & relocation" section to `_render_profile_editor()` in `tracker_views/settings.py` — per-residence rows with cost slider (0–10), work-mode multiselect, optional salary-floor number input, remote_employer_countries textarea; addable/removable rows via `st.session_state`
- [ ] T027 [US5] Add contract_geo editor — per contract type (permanent/freelance/contract), multiselect over residence zone keys
- [ ] T028 [US5] Wire save block — `profile.relocation = ...` and `profile.contract_geo = ...` from form state; remove `work_mode_geography` widget assignments (field stays inert)

**Checkpoint**: Settings UI shows Residence & relocation section. Save persists to DB. Legacy work_mode_geography still round-trips but no longer has widgets.

---

## Phase 6: User Story 6 — Job card badge (Priority: P3)

**Goal**: Show a small badge `🧳 {residence} · cost {n}` on job cards when `residence_base` is present and not the zero-cost residence.

**Independent Test**: After rescoring with the new model, find a Paris/FR job in the tracker — card shows `🧳 france · cost 2`.

### Implementation for US6

- [ ] T029 [US6] Add residence badge to job card renderer in `tracker_views/jobs.py` — when `job.get("residence_base")` is present and not equal to the zero-cost residence key, show `🧳 {residence_base} · cost {relocation_cost}` inline with existing badges

**Checkpoint**: Non-home-residence jobs show relocation badge on cards.

---

## Phase 7: Validation

**Purpose**: End-to-end validation per quickstart.md.

- [ ] T030 Run `python score.py --mock --profile unified_jc` — all 15 cases in band, FELFEL unchanged
- [ ] T031 Run `python -m pytest tests/test_storage.py -q` — 100% pass (146+)
- [ ] T032 Verify migration: launch `python -c "from storage import JobStorage; db = JobStorage('data/jobs.db'); print('OK')"` — no errors, columns added
- [ ] T033 Verify round-trip: `python -c "from profiles import SearchProfile, UNIFIED_JC; ..."` — full criteria dict survives serialize→deserialize
- [ ] T034 [P] grep for `_geography_for_mode` across repo — confirm zero references outside specs/
- [ ] T035 [P] grep for any file touched outside the 6 allowed files — confirm no opportunistic refactors per Constitution V

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (US1 — profiles)**: No dependencies — can start immediately
- **Phase 2 (US2 — scorer)**: Depends on US1 (reads `profile.relocation` and `profile.contract_geo`)
- **Phase 3 (US3 — storage)**: Depends on US2 (persists `relocation_cost` and `residence_base` from `_evaluation_result()`)
- **Phase 4 (US4 — mocks)**: Depends on US2 (exercises Engagement resolution)
- **Phase 5 (US5 — settings)**: Depends on US1 (reads/writes `profile.relocation` and `profile.contract_geo`)
- **Phase 6 (US6 — badge)**: Depends on US3 (`get_all_for_tracker()` returns new columns)
- **Phase 7 (Validation)**: Depends on all phases

### Story Dependencies

```
US1 (profiles) ──┬── US2 (scorer) ──┬── US3 (storage) ── US6 (badge)
                 │                  │
                 │                  └── US4 (mocks)
                 │
                 └── US5 (settings)
```

- **US2** depends on US1 (reads new fields)
- **US3** depends on US2 (persists new result fields)
- **US4** depends on US2 (exercises Engagement resolution)
- **US5** depends on US1 (reads/writes new fields)
- **US6** depends on US3 (reads new columns from tracker queries)

### Parallel Opportunities

- **US5** can run in parallel with US2/US3/US4 — touches settings.py only, no dependency on scorer/storage changes beyond US1
- **T014 + T015** (migration + save_scored) can run in parallel
- **T019-T022** (mock jobs) all touch different dict entries in one list — can be done as one edit
- **T034, T035** (validation greps) can run in parallel

---

## Parallel Example: US2 + US5

```bash
# After US1 completes, these two can run concurrently:
# US2: scorer.py (T006-T013) — Engagement resolution, Tier-0 rewrite
# US5: settings.py (T025-T028) — Residence & relocation UI section
```

---

## Implementation Strategy

### MVP First (US1 + US2)

1. Phase 1: US1 — profiles.py (T001-T005)
2. Phase 2: US2 — scorer.py (T006-T013)
3. **STOP and VALIDATE**: `python score.py --mock --profile unified_jc` — all 11 cases in band
4. This is the minimum viable change — the new model works, mocks prove it

### Full Delivery

1. MVP (US1 + US2) → mock-validated
2. + US3 (storage) → persisted, tests green
3. + US4 (mocks) → 4 new regression cases
4. + US5 (settings) → UI editable
5. + US6 (badge) → visible on cards
6. + Phase 7 (validation) → ready to deploy

### File Edit Summary

| File | Tasks | Est. Lines Changed |
|------|-------|-------------------|
| `profiles.py` | T001-T005 | +40 / -0 |
| `scorer.py` | T006-T013 | +110 / -25 |
| `storage.py` | T014-T018 | +15 / -2 |
| `score.py` | T019-T024 | +55 / -0 |
| `tracker_views/settings.py` | T025-T028 | +80 / -25 |
| `tracker_views/jobs.py` | T029 | +5 / -0 |

---

## Notes

- No test tasks — validation is mock-based (`score.py --mock`) per Constitution VI
- `_geography_for_mode()` deletion (T013): verify zero callers with grep before deleting
- The two RUB-salary mock cases use placeholder bands `(4, 8)` with `requires_spec_023` comments — tighten to `(1, 2)` Tier-0 bands after Spec 023 lands
- Constitution V (surgical): no changes to scrape.py, scrapers/, title_gate.py, llm.py, main.py, onboarding.py, shared.py
- `work_mode_geography` stays on dataclass and in criteria — deprecated but never deleted (016/017 idiom)
