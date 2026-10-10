# Implementation Plan: CV agent review loop (auto mode, deterministic lint, review bundle)

**Branch**: `036-cv-agent-review-loop` | **Date**: 2026-10-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/036-cv-agent-review-loop/spec.md`

## Summary

Add three self-contained improvements to the existing spec-029 CV agent, all inside
`core/cv_agent/` and with no DB/API/scorer change:

1. **Non-interactive driving** (US1/US2, P1) — `--auto` runs to `approval_gate` with
   zero `input()` calls and leaves the checkpoint resumable; `--resume <thread_id>`
   with `--approve`/`--reject` finishes a paused run.
2. **Deterministic zero-LLM lint** (US3, P1) — a new pure-Python `lint` node after
   `render` checks the rendered `cv_data_<slug>.json` + PDF against 9 rules, each
   with a seeded-defect unit test; 4 rules (facts/roles/em-dash/pages) feed the
   existing bounded revise loop (US5, P2).
3. **One review bundle** (US4, P1) — `review.json` (≤ 20 KB) written at every stop at
   `approval_gate`, so a reviewer reads one file instead of the master + logs.

Plus the agent↔domain import-boundary guard (US6, P2): the private
`core.job_actions._dict_to_posting` becomes the public `core.models.posting_from_dict`,
and an AST guard test pins the allowed `core` imports of `core/cv_agent/`.

## Technical Context

**Language/Version**: Python 3.11 (`X | Y` unions, f-strings, dataclasses).

**Primary Dependencies**: LangGraph (`langgraph` — `StateGraph`, `interrupt`,
`Command`, `SqliteSaver`); `httpx` + `bs4`/`lxml` (fetch, unchanged); `pypdf`
(page count — **already declared**, `requirements.txt` line 12 `pypdf>=3.0.0`).
No new dependency.

**Storage**:
- SQLite via `JobStorage` (unchanged — `core/storage.py` is never touched).
- LangGraph checkpointer `SqliteSaver` at `paths.data_path("cv_agent_checkpoints.sqlite")`.
- Rendered deliverables + `review.json` on the local filesystem under
  `output_paths.local_dir` (`CV_OUTPUT_DIR` → config `cv.output_dir` →
  `<data>/cv_outputs/<Company> - <Title>`).

**Testing**: `pytest`. New `tests/test_cv_agent_lint.py` (unit, seeded defects) and
`tests/test_cv_agent_boundary.py` (AST guard); extend `tests/test_cv_agent_gate.py`
(auto/resume + review.json).

**Target Platform**: macOS dev (CLI-only). This feature does not change prod services.

**Project Type**: CLI agent (LangGraph) inside the existing `job_agent` package.

**Performance Goals**: `review.json` ≤ 20 KB (≈ 5k tokens); lint is pure-Python
(no LLM, no network, no subprocess beyond reading the PDF page count).

**Constraints**:
- **No new dependency** — `pypdf` is already in `requirements.txt`, present in dev
  and the Docker image.
- **Zero-LLM deterministic lint** (Constitution §IV): structure/control flow derived
  in code, never by the model.
- **Agent↔domain boundary** (Constitution §X): `core/cv_agent/` depends on the domain
  only through a fixed allow-list; the domain never imports an agent.
- **Never modify**: `core/storage.py`, `core/scorer.py`, `core/llm.py`,
  `core/scrape.py`, `core/main.py`, `core/scrapers/`. `core/models.py` is touched
  **only** for the `posting_from_dict` relocation, explicitly authorised by FR-009/US6.

**Scale/Scope**: single-user solo project; one agent; 29 scraper modules declare
`SOURCE_NAME`, 23 `ENABLED=True` (reference figure for the master data edit).

---

**Dependency justification (Constitution §V)** — the user asked that the PDF
page-count library be justified in `plan.md`:

- **Chosen: `pypdf`.** It is *already* a declared dependency (`requirements.txt`
  line 12, `pypdf>=3.0.0`), so introducing it adds **nothing** to the dependency
  surface — it is the one library this repo has already committed to for PDF
  metadata/page reads. `PdfReader(path).pages` gives the page count in one line,
  pure-Python, no system libs, no rendering.
- **Alternatives rejected**: `PyMuPDF` (fitz) — heavier, AGPL-licensed, and not
  declared (only present incidentally in `.venv`); `PyPDF2` — a stale fork of the
  same codebase, undeclared. Neither is justified when `pypdf` is already in the
  lockfile-style `requirements.txt`.

---

## Constitution Check

*GATE: pass before research; re-checked after design — no change.*

| # | Principle | Status | Notes |
|---|-----------|--------|-------|
| I | Filet large, point de filtrage unique | ✅ | Lint checks **factual/style conformance** of a rendered CV, never job fit. The scorer stays the sole fit judge; no scraper/scoring path is touched. |
| II | Deux chemins d'amélioration | ✅ | Code path: spec + code. The master-CV data edits (em-dash fix, job-agent figures) are **prose-path** edits made directly in `.cv_pipeline/cv_data_master.json` (outside the repo) — tracked as a distinct task, not a code diff. |
| III | Profil unifié unique | ✅ | Not affected. |
| IV | Structure déterministe, prose LLM | ✅ | Core principle of this spec. Lint is deterministic (no LLM/network); `lint_ok`, the retryable-check set, and the `REVISION_LIMIT` bound are code-derived. `review.json` structure is deterministic. |
| V | Modification chirurgicale | ✅ | Confined to `core/cv_agent/` + the `_dict_to_posting` relocation + one new test + one tracked config file. No new dependency (pypdf already declared — justified above). Non-goals explicit. |
| VI | Validation empirique | ✅ | SC-002 (seeded-defect unit test per check) + SC-005 (run on 3 hand-done postings: Pennylane, CRS Product Lead, FELFEL, each rated ship / small patch / rewrite). |
| VII | Sécurité d'abord | ✅ | Lint/review are local; no new egress; `publish` unchanged (no secret logged). Personal allowlist is **not** committed (derived from the master / `.cv_pipeline`). |
| VIII | Scrapers par modèle d'acquisition | ✅ | Not affected. |
| IX | Scoring optionnel | ✅ | Not affected. |
| X | Catalogue / espace utilisateur | ✅ | No schema change. `file_and_record` unchanged (still writes the local DEV DB); the non-goal records that this stays local until roadmap step 6b. |
| XI | Migration progressive | ✅ | Additive: new flags default off; interactive mode byte-for-byte unchanged; no schema/DB change; app stays operational. |

**Result**: no violations. Complexity Tracking table intentionally left empty.

## Project Structure

### Documentation (this feature)

```text
specs/036-cv-agent-review-loop/
├── plan.md              # this file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (state fields + review.json schema)
├── quickstart.md        # Phase 1 output (validation scenarios)
├── contracts/
│   ├── cli-contract.md        # CLI V2 (new flags + result lines)
│   └── node-io-contract.md    # node I/O + topology V2 (lint node)
└── tasks.md             # Phase 2 (NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
core/
├── models.py                          # + posting_from_dict()  (FR-009, US6 — verbatim move)
├── job_actions.py                     # − _dict_to_posting; import posting_from_dict
├── score.py                           # drop unused _dict_to_posting from import (line 15)
└── cv_agent/
    ├── cli.py                         # run() refactor: --auto / --resume / --approve / --reject
    ├── graph.py                       # + lint node + route_after_lint edge
    ├── nodes.py                       # + lint node; _dict_to_posting → posting_from_dict
    ├── state.py                       # + thread_id, lint, lint_ok
    ├── review.py                      # NEW: write_review(state) → review.json path
    ├── lint.py                        # NEW: deterministic lint (9 checks)
    └── lint_config.py                 # NEW: generic style rules (tracked) — personal allowlist
                                       #      derived from cv_data_master.json / .cv_pipeline (untracked)

tests/
├── test_cv_agent_lint.py              # NEW: per-check seeded-defect unit tests
├── test_cv_agent_boundary.py          # NEW: agent↔domain import guard (AST, + negative fixtures)
└── test_cv_agent_gate.py              # EXTEND: --auto / --resume / review.json
```

**Structure Decision**: single-project layout. Everything lives in `core/cv_agent/`
(the agent's own module) except the one relocated function in `core/models.py` and
its three import sites. The two new contracts mirror the spec-029 pattern
(`specs/029-…/contracts/`), documenting the V2 CLI and node I/O that extend V1.

## Complexity Tracking

> Filled only on Constitution Check violations — none for this spec.
