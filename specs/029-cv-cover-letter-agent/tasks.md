---
description: "Task list for CV + Cover Letter Agent (029)"
---

# Tasks: CV + Cover Letter Agent (029)

**Input**: Design documents from `/specs/029-cv-cover-letter-agent/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Not unit-tested (per spec §VI, validation is **empirical** — see
`quickstart.md`). Each user story below carries a runnable "Independent Test" in
place of a test suite.

**Organization**: Grouped by user story, each an independently runnable increment
of the pipeline. All tasks touch files under `cv_agent/` (repo root), plus
`requirements.txt` and `.gitignore`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on incomplete tasks)
- **[Story]**: US1–US4 (maps to the spec's functional requirements / acceptance scenarios)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the one new dependency and the package skeleton.

- [X] T001 Add `langgraph>=1.2,<2` and `langgraph-checkpoint-sqlite>=3.0,<4` to `requirements.txt`, install into `.venv/` (`pip install "langgraph>=1.2,<2" "langgraph-checkpoint-sqlite>=3.0,<4"`)
- [X] T002 [P] Create `cv_agent/__init__.py` — package docstring (role in the flow) re-exporting `compile_graph`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The LangGraph skeleton — the checkpointer→interrupt→resume foundation
that every later story hangs off. All node names must exist (as pass-through
stubs) so the graph compiles from the start.

**⚠️ CRITICAL**: No user story work until this phase compiles and the CLI skeleton
runs an empty graph.

- [X] T003 [P] Create `cv_agent/state.py` — `CVAgentState` TypedDict per `data-model.md` §1, including `revision_count: Annotated[int, operator.add]` (reducer — FR-017)
- [X] T004 [P] Create `cv_agent/prompts.py` — one prompt constant per LLM node (`extract_requirements`, `analyze_and_plan`, `tailor_cv`, `draft_cover_letter`, `draft_recruiter_message`, `self_critique`); `tailor_cv`/`self_critique` prompts are templates whose master-CV anchor is injected at call time
- [X] T005 Create `cv_agent/nodes.py` — all 12 node function stubs (pass-through), each with a docstring stating reads-from / does / writes-back / LangGraph concept (FR-017)
- [X] T006 Create `cv_agent/graph.py` — `StateGraph`: register nodes, edges, conditional edges (`route_after_analysis`, `route_after_critique`, `route_after_approval`), `SqliteSaver.from_conn_string(paths.data_path("cv_agent_checkpoints.sqlite"))`, `interrupt_before=["analysis_gate","approval_gate"]`, `compile()`; ASCII flow header (FR-017)
- [X] T007 Create `cv_agent/cli.py` — driver skeleton: parse args (`job_id|url`, `--letter`, `--paste`, `--profile`), resolve active profile via `load_active_profile`, `invoke`/resume loop around the two interrupts, print gates, emit `CV_AGENT_RESULT` lines (`contracts/cli-contract.md`)

**Checkpoint**: `python -m cv_agent.cli <job_id>` runs the graph end-to-end with
stub nodes (no LLM, no render) and exits cleanly.

---

## Phase 3: User Story 1 — Resolve a reference to a job (Priority: P1) 🎯 MVP

**Goal**: `resolve_reference` turns `job_id | url | pasted text` into a
`JobPosting`-shaped state; url/paste entries are extracted + scored **through the
scorer** and persisted; `refresh_context` degrades gracefully. (FR-001–FR-004,
FR-018)

**Independent Test**:
```bash
.venv/bin/python -m cv_agent.cli <job_id>        # resolves from DB (see state dump)
.venv/bin/python -m cv_agent.cli <careers-url>    # persists a scored job (re-run → same id, no dup)
.venv/bin/python -m cv_agent.cli --paste <<'EOF'
<paste posting text>
EOF
```

- [X] T008 [US1] Implement `resolve_reference` (id branch) in `cv_agent/nodes.py` — `db.get_job_for_prepare` + `db.get_score_result` + `job_actions._dict_to_posting` → `job`, `job_id`, `description_text`, `score`, `score_reason`
- [X] T009 [US1] Implement `resolve_reference` (url + paste branches) in `cv_agent/nodes.py` — url: `httpx` GET + `bs4` HTML→text, title/company from `<title>`/`og:` / host, `source="manual"`; paste: stdin text, `source="paste"`
- [X] T010 [US1] Implement url/paste persistence in `cv_agent/nodes.py` — `extract_job_fields(job)` → `evaluate_for_profile(job, profile)` → `save_scored` (or `save_unscored` on `None`), no duplicated scoring logic
- [X] T011 [US1] Implement `refresh_context` (optional) in `cv_agent/nodes.py` — re-fetch known url if stored desc below threshold (`httpx`, no LLM), else delegation stub / skip; state-only, never writes DB; no crash on fetch failure

**Checkpoint**: all three entry kinds populate state and url/paste persist a
deduped, scored job.

---

## Phase 4: User Story 2 — Analyze fit + first gate (Priority: P2)

**Goal**: `extract_requirements` + `analyze_and_plan` produce the analysis
report; `analysis_gate` interrupts with proceed/adjust/abort; abort marks the job
`archived`. (FR-005–FR-007)

**Independent Test**:
```bash
.venv/bin/python -m cv_agent.cli <job_id>   # choose `abort` → CV_AGENT_RESULT aborted <id>, job archived
```

- [X] T012 [US2] Implement `extract_requirements` in `cv_agent/nodes.py` — `llm.call(json_mode=True)` → `requirements`, `letter_required`, `recruiter_contact` (schema `data-model.md` §2.1)
- [X] T013 [US2] Implement `analyze_and_plan` in `cv_agent/nodes.py` — reads `score`/`score_reason` only, emits `fit_analysis` + `proposed_profile` + `profile_confidence` (schema §2.2), **emits NO score**
- [X] T014 [US2] Implement `analysis_gate` (`interrupt()`) + `mark_skipped` node + `route_after_analysis` conditional in `cv_agent/nodes.py` and `cv_agent/graph.py` — abort → `set_status(job_id, "archived", notes="cv_agent: skipped at analysis_gate")`; adjust merges `user_directives` (contact-profile override here)

**Checkpoint**: analysis prints, abort exits cleanly with the job archived; no
documents generated.

---

## Phase 5: User Story 3 — Tailor + critique (Priority: P3)

**Goal**: `tailor_cv` re-angles the master CV, conditional letter/recruiter
message, and `self_critique` bounds a ≤3 revise loop. (FR-008–FR-010)

**Independent Test**:
```bash
.venv/bin/python -m cv_agent.cli <job_id>   # a posting that doesn't require a letter → letter skipped
```

- [X] T015 [US3] Implement `tailor_cv` in `cv_agent/nodes.py` — load `cv_data_master.json`, re-angle into `cv_content` (schema §2.3), set `slug`; content only, no facts absent from master
- [X] T016 [US3] Implement `draft_cover_letter` + `draft_recruiter_message` in `cv_agent/nodes.py` — conditional on `letter_required OR --letter` / named `recruiter_contact`
- [X] T017 [US3] Implement `self_critique` in `cv_agent/nodes.py` + `route_after_critique` in `cv_agent/graph.py` — coverage/keywords/factuality vs master; non-empty `factuality_violations` forces revise; loop back to `tailor_cv` while `revision_count < 3`

**Checkpoint**: a tailored `cv_content` is produced; the critique loop stops after
3 revisions.

---

## Phase 6: User Story 4 — Render + second gate + record (Priority: P4)

**Goal**: deterministic render to `.docx` + PDF, `approval_gate` interrupt, and
`file_and_record` copies to Nextcloud + records the application. (FR-011–FR-013)

**Independent Test**:
```bash
.venv/bin/python -m cv_agent.cli <job_id>   # approve at both gates → CV_AGENT_RESULT ok <pdf>
```

- [X] T018 [US4] Create `cv_agent/renderer.py` — deterministic render wrapper: merge `cv_content` with master fixed fields (`photo`, `relocation`) + derived `contact` (`CH`/`FR` ← `proposed_profile`) + `filename`, write `cv_data_<slug>.json` into `.cv_pipeline/`, run `node render_cv.js <json> <outdir>`, run `soffice --headless --convert-to pdf`; **imports no `llm`** (research §4, contracts `node-io-contract.md`)
- [X] T019 [US4] Implement `render` node in `cv_agent/nodes.py` — call `renderer.render(...)`, set `output_paths`
- [X] T020 [US4] Implement `approval_gate` (`interrupt()`) + `file_and_record` + `route_after_approval` in `cv_agent/nodes.py` and `cv_agent/graph.py` — approve → copy to `~/Nextcloud/Documents/01 Job/Job applications/<Company> - <Title>/`, `save_application(job_id, analysis=JSON, cover_letter=text)`, `set_status(job_id, "ready", notes=…)`; reject → revise loop (bounded by `revision_count < 3`)

**Checkpoint**: a tailored PDF exists locally and in Nextcloud, the job status is
`ready`, and the application is recorded.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: cross-cutting validation and finishing (FR-014–FR-017 invariants, §VI).

- [X] T021 [P] Finalize `requirements.txt` (pinned pairs) and `.gitignore` for `data/cv_agent_checkpoints.sqlite`
- [X] T022 Run all six `quickstart.md` validation scenarios against real `jobs.db` data, a live URL, and a pasted posting
- [X] T023 Verify the guardrail invariants via grep: no `bind_tools`/`ChatOpenAI`/`tools.py` in `cv_agent/`; `renderer.py` imports no `llm`; `analyze_and_plan` emits no score; every model call goes through `llm.call`
- [X] T024 [P] FR-017 pedagogical pass — confirm every module + node docstring and inline LangGraph-mechanics comments (conditional edges, `interrupt_before`, resume via `invoke(None)`, state reducers) are present

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (1)** → **Foundational (2)** → **US1 (3)** → **US2 (4)** → **US3 (5)** → **US4 (6)** → **Polish (7)**
- Foundational blocks all user stories (the graph must compile with all node names).
- User stories are sequential here (single developer, one `nodes.py`/`graph.py`), but each is independently runnable and committable.

### Within Each User Story

- `resolve_reference` (US1) before persistence (T010) — persistence reuses the resolved job.
- `tailor_cv` (US3) before `self_critique` (US3) — critique reads `cv_content`.
- `renderer.py` (US4) before `render` node — the node delegates to it.

### Parallel Opportunities

- T001 ⇄ T002 (deps install vs package docstring).
- T003 ⇄ T004 (state.py vs prompts.py — different files).
- T018 (renderer.py) is independent of the LLM nodes — could be written any time after Foundational.
- T021 ⇄ T024 (requirements/gitignore vs doc pass) — different concerns.

### Suggested MVP (User Story 1)

Complete Setup + Foundational + US1 → the CLI resolves and persists a job
reference. That is the smallest shippable, verifiable increment; US2–US4 then add
analysis → tailoring → rendering on top of the same skeleton.

---

## Notes

- `nodes.py` and `graph.py` are the two hot files — sequential tasks within a
  story should edit the same file without conflict.
- `renderer.py` imports no `llm` (factual firewall) — keep it that way.
- Statuses touched: `archived` (abort) and `ready` (approved/recorded). Never
  `applied` — applying is a later tracker action.
- Commit after each user story checkpoint.
