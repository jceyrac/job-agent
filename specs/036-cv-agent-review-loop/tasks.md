# Tasks: CV agent review loop (spec 036)

**Input**: Design documents from `specs/036-cv-agent-review-loop/`

**Prerequisites**: `plan.md`, `spec.md` (clarified), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Test tasks are included (SC-002 requires a seeded-defect unit test per lint
check; SC-006 requires a boundary guard with negative fixtures). Write tests first where
marked — confirm they fail, then implement.

**Organization**: Grouped by user story. Because US1/US2 (`--auto`/`--resume`) depend on
US3 (`lint`) and US4 (`review.json`) via the `review` result line and `thread_id`, the
phases are ordered **by build dependency, not strict priority** — see "Dependencies".

## Format: `[ID] [P?] [Story] Description`

- **[P]** = parallelizable (disjoint files, no dependency on another incomplete task)
- **[Story]** = US1 … US6 (omitted for Setup/Foundational/Polish)

---

## Phase 1: Setup

**Purpose**: Record the green baseline before any change.

- [X] T001 Run `python -m pytest tests/` and confirm the full suite is green; note the count. Confirm `pypdf` is importable (`python -c "from pypdf import PdfReader"`) and that `core/cv_agent/cli.py`, `graph.py`, `nodes.py`, `state.py`, `renderer.py` all exist.

---

## Phase 2: Foundational (blocking prerequisites for US1/US2/US4)

**Purpose**: `thread_id` in state (needed by the `review.json` bundle and `--resume`) and
the master-CV data corrections the lint checks and empirical validation depend on.

- [X] T002 [P] Master data edits (**prose-path, outside the repo — on the Mac**): in `.cv_pipeline/cv_data_master.json`, (a) replace `—` (U+2014) with `–` (U+2013) in role titles (verified safe — neither `render_cv.js` nor `core/cv_agent/` splits titles on `" — "`), and (b) add the job-agent project with current verified figures (23 enabled scrapers, not "11 sources") and a reference date. Do **not** commit this file — it lives outside the repo.
- [X] T003 [P] Add `thread_id: str` to `CVAgentState` in `core/cv_agent/state.py`, and seed `"thread_id": _thread_id(reference)` into the `initial` dict in `core/cv_agent/cli.py` `run()`. (No behaviour change yet — the field is just threaded through so `approval_gate` and `--resume` can use it.)

**Checkpoint**: suite still green; `thread_id` present in state; master corrected.

---

## Phase 3: User Story 3 — Deterministic lint of the rendered CV (Priority: P1) 🎯 MVP

**Goal**: After `render`, a pure-Python `lint` node (no LLM, no network) checks the final
`cv_data_<slug>.json` + PDF against 9 rules; each check returns `{id, status, detail}`.

**Independent Test**: `python -m pytest tests/test_cv_agent_lint.py` — every check has a
seeded-defect test that fails it, and a clean fixture that passes it.

### Tests for US3

- [X] T004 [P] [US3] Create `tests/test_cv_agent_lint.py` with a seeded-defect test per check (9 checks — see `contracts/node-io-contract.md`): `pages` (>2 pages), `em_dash`, `facts_numbers` (unknown numeric token), `roles_match_master` (mismatched dates/employer), `current_role_first`, `contact_consistency` ("Based in Lausanne" + `FR`), `in_progress_courses`, `banned_phrases`, `interests_present`. Build fixtures from a small hand-written `rendered_data` dict + a generated 3-page PDF. Confirm they fail against a stub.

### Implementation for US3

- [X] T005 [US3] Create `core/cv_agent/lint_config.py` — the **tracked** generic style rules only: the banned-phrase list, the em-dash/punctuation rule, the retryable-check set `RETRYABLE_LINT_IDS = {"facts_numbers", "roles_match_master", "em_dash", "pages"}`, and the in-progress-course allowlist. No personal data (numbers/employers/claims) here.
- [X] T006 [US3] Create `core/cv_agent/lint.py` — pure-Python, imports **no** `llm` and no network. Public entry `run_lint(rendered_data: dict, pdf_path: str, proposed_profile: str, master: dict, config) -> tuple[list[dict], bool]` returning `(lint, lint_ok)`. Personal allowlists (allowed numbers, employers, extra roles, claims) are derived from `master` at runtime (or read from a sidecar next to the master in `.cv_pipeline/`). Page count via `from pypdf import PdfReader`.
- [X] T007 [US3] Wire the node: add `lint: list[dict]` and `lint_ok: bool` to `CVAgentState` in `core/cv_agent/state.py`; add a `lint` node to `core/cv_agent/nodes.py` (reads `output_paths.json`/`output_paths.pdf`, `proposed_profile`, `load_master()`, config; writes `lint`, `lint_ok`); register it in `core/cv_agent/graph.py` between `render` and `publish`, with a `route_after_lint` conditional router that returns `"publish"` for now (the retryable branch is US5). Pedagogical comment on the new node/edge (FR-008).

**Checkpoint**: `lint` node runs after `render`; `lint`/`lint_ok` reach `approval_gate`; lint unit tests green.

---

## Phase 4: User Story 4 — One compact review bundle (Priority: P1)

**Goal**: `review.json` written to `output_paths.local_dir` at every stop at `approval_gate`, ≤ 20 KB.

**Independent Test**: a run reaching `approval_gate` leaves `review.json` with the exact keys of `data-model.md` §4.

### Tests for US4

- [X] T008 [P] [US4] Add a test in `tests/test_cv_agent_gate.py` asserting `review.json` is written on the `approval_gate` interrupt, contains the required keys (`job`, `requirements`, `fit_analysis`, `cv` without `photo`/`interests`, `critique`, `lint`, `lint_ok`, `revision_count`, `refine_notes`, `output_paths`, `thread_id`), and serializes ≤ 20 KB on a representative fixture.

### Implementation for US4

- [X] T009 [US4] Create `core/cv_agent/review.py` — `write_review(state: dict) -> str` returning the written path. Build the bundle per `data-model.md` §4: read `cv` from `output_paths.json` (the rendered data file) minus `photo`/`interests`; narrow `requirements` and `fit_analysis` to the spec-listed fields.
- [X] T010 [US4] In `core/cv_agent/nodes.py` `approval_gate`, call `write_review(state)` **before** `interrupt(payload)` (FR-006). No other change to the gate's resume logic.

**Checkpoint**: every `approval_gate` pause leaves a fresh, correctly-shaped `review.json`.

---

## Phase 5: User Story 1 — Run to review with zero interaction (Priority: P1)

**Goal**: `--auto` resumes `analysis_gate` automatically (`decision=proceed` + overrides), runs to `approval_gate`, writes `review.json`, exits 0 — never auto-approving.

**Independent Test**: `printf '%s' "<posting>" | python -m core.cv_agent.cli --paste --auto --contact swiss` exits 0 with last line `CV_AGENT_RESULT review <path> <thread_id>` and zero `input()` calls; the job status is unchanged.

### Tests for US1

- [X] T011 [P] [US1] Add a test in `tests/test_cv_agent_gate.py` (patch `interrupt`/`input` as the existing gate test does): `--auto` runs to `approval_gate`, exits 0, prints the `review` result line with the `thread_id`, never auto-approves (no `file_and_record`, status unchanged).

### Implementation for US1

- [X] T012 [US1] In `core/cv_agent/cli.py`: add `--auto`, `--contact {swiss,french}`, `--directives`, `--title`, `--company` to `parse_args`; refactor `run()` so `--auto` auto-resumes `analysis_gate` with `Command(resume={"decision": "proceed", "user_directives": …, "proposed_profile": …})` (map `--contact`→`proposed_profile`, `--directives`→`user_directives`; `--title`/`--company` seed the job's title/company, not `title_override`), then continues until the `approval_gate` pause and exits 0 with `CV_AGENT_RESULT review <review.json path> <thread_id>`. Without `--auto`, behaviour is unchanged (interactive gates).

**Checkpoint**: `--auto` is a full zero-prompt run to `approval_gate`; interactive mode unchanged.

- [X] T023 [US1] (added 2026-10-10 — US1 scope note) Close the spec-029 `--paste` gap: `--paste --auto` **requires** `--title`/`--company` (fail fast in `cli.py` before any LLM call); seed `job_title`/`job_company` into the `initial` state and use them in the paste branch of `nodes.resolve_reference`; make `JobPosting.id` (in `core/models.py`) raise when title **or** company is empty with no URL; derive the paste thread id from the job_id (`_paste_thread_id` = `sha256("{title}::{company}::paste")`), not `sha256(pasted text)`; add `renderer._refuse_clobber` (write a `job_id` marker into each `<Company - Title>` folder, refuse a render whose stored `job_id` differs). Tests in `tests/test_cv_agent_paste.py` + the updated `test_auto_passes_overrides_to_analysis_resume`.

---

## Phase 6: User Story 2 — Approve/reject a pending run non-interactively (Priority: P1)

**Goal**: `--resume <thread_id>` with exactly one of `--approve` / `--reject "notes"`.

**Independent Test**: `--resume <thread_id> --approve` prints `CV_AGENT_RESULT ok <pdf>`; `--resume <thread_id> --reject "…"` re-stops at `approval_gate` and prints the `review` line; a bad/unknown thread prints `error` and exits non-zero.

### Tests for US2

- [X] T013 [P] [US2] Add tests in `tests/test_cv_agent_gate.py`: `--resume --approve` → `ok`; `--resume --reject` → `review` (re-stops, re-writes `review.json`); `--resume` on an unknown thread or a thread not paused at `approval_gate` → exit code 5 + `CV_AGENT_RESULT error`.

### Implementation for US2

- [X] T014 [US2] In `core/cv_agent/cli.py`: add `--resume <thread_id>`, `--approve`, `--reject` to `parse_args` (require exactly one of `--approve`/`--reject` with `--resume`); implement resume via `app.get_state(config)` → resume the paused `approval_gate` with `Command(resume={"approved": bool, "approval_notes": …})`; run to the next pause/END; on an unknown thread or wrong gate, print `CV_AGENT_RESULT error …` and return exit code 5 (documented in `contracts/cli-contract.md`).

**Checkpoint**: a full `--auto` → `--resume --approve` pair completes with zero `input()` (SC-001).

---

## Phase 7: User Story 5 — Lint failures feed the revise loop (Priority: P2)

**Goal**: a `fail` on a retryable check routes back to `tailor_cv` with the lint details as revision notes, counted against `REVISION_LIMIT`.

**Independent Test**: a seeded retryable failure (e.g. a planted `—`) re-enters `tailor_cv`; a non-retryable failure (e.g. `interests_present`) still publishes; the `REVISION_LIMIT` cap stops the loop with `lint_ok=false` in `review.json`.

### Tests for US5

- [X] T015 [P] [US5] Add tests in `tests/test_cv_agent_lint.py` (or a graph-level test) for `route_after_lint`: retryable fail + under cap → `tailor_cv`; retryable fail + at cap → `publish`; non-retryable fail → `publish`; and that the lint node appends retryable failure summaries to `refine_notes`.

### Implementation for US5

- [X] T016 [US5] In `core/cv_agent/lint.py` (or the `lint` node in `nodes.py`): on a retryable `fail`, append human-readable summaries (`lint: <id> — <detail>`) to `refine_notes` (the existing list reducer). In `core/cv_agent/graph.py` `route_after_lint`: return `tailor_cv` only when some check in `RETRYABLE_LINT_IDS` is `fail` **and** `revision_count < REVISION_LIMIT`, else `publish` (FR-008 comment).

**Checkpoint**: lint-driven revise reuses `tailor_cv` under the shared cap; the five non-retryable checks stay informational.

---

## Phase 8: User Story 6 — Explicit boundary between agents and the domain (Priority: P2)

**Goal**: the private `_dict_to_posting` becomes a public `core.models.posting_from_dict`, and an AST guard pins the agent↔domain import boundary.

**Independent Test**: `python -m pytest tests/test_cv_agent_boundary.py` green; each rule fails on its negative fixture.

### Implementation for US6

- [X] T017 [US6] Relocate the helper (FR-009, no behaviour change):
  - `core/models.py`: add module-level `posting_from_dict(d: dict) -> JobPosting`, body moved **verbatim** from `core/job_actions.py:20-64` (`d.get(...)` fields, `posted_date` `%Y-%m-%d` and `extracted_at` `%Y-%m-%dT%H:%M:%S` guards, `tags=[]`, `salary=None`).
  - `core/job_actions.py`: delete the `_dict_to_posting` def; change `from core.models import JobPosting` → `from core.models import JobPosting, posting_from_dict`; replace the two calls at `:218` (`extract_one`) and `:294` (`score_one`).
  - `core/score.py:15`: drop the unused `_dict_to_posting` from the import (leave `extract_one, score_one, _discover_contacts`).
  - `core/cv_agent/nodes.py:52` and `:245`: import and call `posting_from_dict` from `core.models`.
  - Run `python -m pytest tests/` (touches `core/models.py` — Development Workflow step 3).
- [X] T018 [US6] Create `tests/test_cv_agent_boundary.py` (mirror `tests/test_installable_project.py`: AST walk over `git ls-files -- '*.py'`). Three rules, allow-list in the test file with a comment per entry: (A) no `core/cv_agent/**` imports a `core` module outside `{models, llm, paths, profiles, scorer, storage}` (internal `core.cv_agent.*` imports are skipped); (B) no `core/cv_agent/**` imports a `_`-prefixed name from any `core` module; (C) no `core` module outside `core/cv_agent/` imports `core.cv_agent` (entry-point allow-list empty — the agent launches only via `python -m core.cv_agent.cli`). Each rule gets a negative fixture (plant a temp module/import, assert the guard flags it, remove it).

**Checkpoint**: `core/cv_agent/` depends on the domain only through the allow-list; the domain never imports an agent.

---

## Phase 9: Polish & release

- [X] T019 Run `python -m pytest tests/` — full suite green (SC-006).
- [ ] T020 [P] Empirical validation (SC-005, Constitution §VI): run `--paste --auto --title … --company …` then `--resume … --approve` on the hand-done postings (Pennylane, CRS Product Lead; FELFEL dropped — stale offer). For each, record whether the draft is **ship / small patch / rewrite**, and whether `lint_ok` was true (a `false` on the current master-derived CV is a documented real defect — SC-002).
- [ ] T021 Run the `quickstart.md` scenarios end-to-end (auto → bundle; resume approve; resume reject; resume error paths; interactive regression).
- [ ] T022 [P] Release (deploy-gated, user drives — see `docs/migration-checklist.md`). Because this spec now touches `core/models.py` (the `JobPosting.id` OR-raise runs on **every** scraper in the cron, not just `posting_from_dict`), it goes through the **full** release flow, not just a merge: (1) stage on the **exact SHA** (`scripts/staging.sh up <full-sha>`); (2) on staging run **one real `python -m core.scrape`** and **`python -m core.score --extract`** and confirm no job raises on `.id` (live-DB check returned 0 empty-url/empty-title-or-company rows, so this is expected clean); (3) parity script green; (4) ff-only merge onto `main`; (5) `./scripts/deploy.sh` on verva; (6) confirm `git -C /opt/job-agent rev-parse HEAD` equals the merged SHA; (7) the **next nightly cron** is green. **STOP and hand over for the user's go at each deploy-gated step.**

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)** → **Foundational (P2)** → **US3** → **US4** → **US1** → **US2** → **US5** → **US6** → **Polish**.

This **deviates from strict P1→P2 order** for build reasons, documented here:
- `review.json` (US4) needs `lint`/`lint_ok` (US3) and `thread_id` (P2).
- `--auto` (US1) and `--resume` (US2) print the `review` result line, which needs `review.json` (US4) and `thread_id` (P2).
- `lint → revise` (US5, P2) builds on the `lint` node (US3).
- `US6` lands last: the move (US6-1) is a no-behaviour-change refactor and the guard test (US6-2/3/4) must verify the **final** import state.

### Within a story

- Test task first (confirm fail), then implementation.
- `lint_config.py` (T005) before `lint.py` (T006) before the node wiring (T007).
- `review.py` (T009) before wiring into `approval_gate` (T010).
- CLI changes are sequential within US1/US2 (both edit `core/cv_agent/cli.py`).

### Parallel Opportunities

```text
T002 (master, outside repo) ∥ T003 (state.py + cli.py seed)
T004 (lint tests) ∥ T005 (lint_config) ∥ T006 (lint.py)     — disjoint files
T008 (review test) ∥ T009 (review.py)
T011 (auto test)   ∥ T012 (auto cli)                          — test-first
T013 (resume test) ∥ T014 (resume cli)                        — test-first
T019 (suite) ∥ T020 (empirical) ∥ T021 (quickstart) ∥ T022 (release, deploy-gated)
```

---

## Implementation Strategy

### MVP First (US3 + US4 + US1 + US2 — the P1 core)

1. T001 baseline → T002/T003 foundational.
2. US3 lint (T004–T007) → US4 review.json (T008–T010).
3. US1 `--auto` (T011–T012) → US2 `--resume` (T013–T014).
4. **STOP and VALIDATE**: `--paste --auto` → `--resume --approve` completes with zero
   `input()`; `review.json` ≤ 20 KB; lint unit tests + gate tests green. This is the whole
   P1 surface — the reviewer workflow works end-to-end.

### Incremental Delivery (P2 + polish)

1. US5 lint→revise (T015–T016).
2. US6 boundary (T017–T018).
3. Polish (T019–T022): full suite, empirical validation, quickstart, release.

---

## Notes

- **Stable core**: the only `core/` files touched are `core/models.py` (adding
  `posting_from_dict`, FR-009/US6) and `core/job_actions.py`/`core/score.py` (the import
  sites). `core/storage.py`, `core/scorer.py`, `core/llm.py`, `core/scrape.py`,
  `core/main.py`, `core/scrapers/` are never touched. DB schema unchanged (non-goal).
- **Personal data stays out of the repo**: `lint_config.py` holds only generic rules; the
  personal allowlist is derived from `cv_data_master.json` / `.cv_pipeline/` at runtime.
- **`pypdf`**: already declared (`requirements.txt` line 12) — no new dependency
  (justification in `plan.md`).
- **No new LLM/provider references**: `lint.py`, `review.py` and the guard import no `llm`.
- Commits end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
