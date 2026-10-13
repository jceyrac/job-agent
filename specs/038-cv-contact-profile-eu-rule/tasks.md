# Tasks: CV contact profile decided deterministically — Swiss only for CH-based jobs (spec 038)

**Branch**: `038-cv-contact-profile-eu-rule` | **Plan**: [plan.md](./plan.md) | **Spec**: [spec.md](./spec.md)

TDD order: each phase lists its tests before its implementation. No new dependency, no
schema change — the classifier is a pure `core/geo_ch.py` module; the rest is a surgical
edit of `core/cv_agent/` (not stable-core).

---

## Phase 1: Foundational — pure classifier + precedence

The classifier is the single blocking prerequisite for US1. It is pure (no LLM/network/DB).

- [X] T001 [P] Create `tests/test_geo_ch.py` covering the detector (`Berlin`→`non_CH`; `Remote (Switzerland)`/`Remote – CH only`→`CH`; `Zurich or Berlin`→`mixed`; empty/no-location→`unknown`; multilingual Swiss names; `CH-8000` postal) and `compute_contact_profile` precedence (each of `explicit`/`explicit_country`/`db_country`/`detected`/`default`; `--contact` over `--country` over `db_country` over detector).
- [X] T002 [P] Create `core/geo_ch.py` with `classify_location`, `normalize_country`, `compute_contact_profile` per `contracts/contact-profile.md` and `research.md` §2–3.
- [X] T003 [P] Add `"core.geo_ch"` to `ALLOWED_CORE_MODULES` in `tests/test_cv_agent_boundary.py`.

## Phase 2: US1 (P1) — deterministic profile + precedence + review.json recording

Story: the CV contact profile is decided deterministically from the job's country, never by
the LLM; the source is recorded in `review.json`.

- [X] T004 [US1] Update `core/cv_agent/state.py` — add inputs `contact_override`/`country_override`, add computed `contact_profile_source`/`contact_profile_flag`, remove `profile_confidence`.
- [X] T005 [US1] Update `core/cv_agent/nodes.py` — `resolve_reference` computes `proposed_profile`/`contact_profile_source`/`contact_profile_flag` via `compute_contact_profile`; `analyze_and_plan` no longer writes `proposed_profile`/`profile_confidence`; `analysis_gate` payload carries the source/flag and the human override maps to source `explicit`; `render` passes `state.get("proposed_profile")` (no default).
- [X] T006 [US1] Update `core/cv_agent/prompts.py` — drop `proposed_profile`/`profile_confidence` and the "DACH"/"Swiss employer"/"CH-viable" wording from `ANALYZE_AND_PLAN_PROMPT`; add a golden-prompt assertion to `tests/test_geo_ch.py`.
- [X] T007 [US1] Update `core/cv_agent/renderer.py` — validate `proposed_profile` and raise on missing/invalid (no silent `CH` fallback).
- [X] T008 [US1] Update `core/cv_agent/review.py` — write `contact_profile`/`contact_profile_source`/`contact_profile_flag`; drop `proposed_profile` from the `fit_analysis` bundle.
- [X] T009 [US1] Update `core/cv_agent/cli.py` — add `--country`; seed `contact_override`/`country_override` into the initial state; remove the `--auto` gate line that injected `args.contact`; the gate display shows `contact_profile_source` instead of `profile_confidence`.
- [X] T010 [US1] Update `tests/test_cv_agent_gate.py` — fixtures `_state`/`_review_state` drop `profile_confidence`; `test_approval_gate_writes_review_json` asserts the new bundle/fit_analysis keys; `test_auto_passes_overrides_to_analysis_resume` asserts `contact_override` is seeded (not `resume["proposed_profile"]`).

## Phase 3: US2 (P1) — `contact_consistency` lint fires only on a residence claim

Story: the CH/FR contact lint stops flagging a bare country token (e.g. "France's
market-leading bank") and only fires on a claim of residence/base.

- [X] T011 [US2] Extend `tests/test_cv_agent_lint.py` with US2-1..4 (bare "France's market-leading bank" passes with CH contact; "Based in Lausanne" fails with FR contact; "open to relocating to …" passes; role sub-lines stay clean).
- [X] T012 [US2] Update `core/cv_agent/lint.py` `_check_contact_consistency` to require a base-verb context (`based in`/`residing in`/`located in`/`living in`/`currently in`/`resident of`…) next to the location token, excluding relocation intent.

## Phase 4: US3 (P1) — empirical validation (no code)

Story: re-run the real postings that motivated the rule and confirm the profile.

- [X] T013 [US3] Run the three quickstart.md empirical runs on the Mac (chargecloud Cologne → `french`; Sygnum/Neho → `swiss`; country-less remote → `french` + `unknown_country` flag) and confirm `review.json` records the expected profile/source/flag.

## Phase 5: US4 (P2) — `roles_match_master` accepts `optional_roles`

Story: linting a rendered CV accepts roles taken from `master["optional_roles"]`.

- [X] T014 [US4] Extend `tests/test_cv_agent_lint.py` with US4-1..3 (optional role with exact `(dates, sub)` passes; altered dates/location still fails; optional material is never emitted by default).
- [X] T015 [US4] Update `core/cv_agent/lint.py` `run_lint` to pass `(master.get("roles") or []) + (master.get("optional_roles") or [])`.

## Phase 6: Polish

- [X] T016 Run `python -m pytest tests/ -q` — 100% green.
- [X] T017 Run `grep -rn "geo_ch" core/ tracker_views/ tracker.py` — only `core/geo_ch.py` and `core/cv_agent/*` import it (FR-009 staging skip).

---

## Dependencies

```
T001/T002/T003 (Foundational, parallel)
        └──▶ US1 (T004–T010, sequential within phase)
                ├──▶ US2 (T011–T012)
                └──▶ US3 (T013, needs US1 done)
                        └──▶ US4 (T014–T015) ──▶ Polish (T016–T017)
```

US2 and US4 touch `core/cv_agent/lint.py` + `tests/test_cv_agent_lint.py` but different
functions/regions; US1 touches the other cv_agent files. US2/US4 can proceed in parallel
with each other once US1's `state.py`/`nodes.py` edits are in (the lint tests import
`run_lint`, which is independent of the profile plumbing).

## Implementation strategy

1. Land the classifier + its tests (Phase 1) first — everything else depends on it.
2. Do US1 end-to-end (state → nodes → prompt → renderer → review → CLI → gate tests).
3. US2 and US4 are small, independent lint edits; do them after US1.
4. US3 is a validation-only run on the Mac against real saved postings; no code.
5. Polish: full suite green + the FR-009 import check (no staging required).
