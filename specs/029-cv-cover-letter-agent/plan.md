# Implementation Plan: CV + Cover Letter Agent (029)

**Branch**: `main` | **Date**: 2026-09-10 | **Spec**: `specs/029-cv-cover-letter-agent/spec.md`

**Input**: Feature specification from `specs/029-cv-cover-letter-agent/spec.md`

## Summary

A standalone LangGraph agent (`job_agent/cv_agent/`, CLI-only) that turns a
single job reference — an existing `job_id`, a live URL, or pasted text — into a
tailored CV, and conditionally a cover letter and/or recruiter message, through a
fixed pipeline with two human-in-the-loop gates. It reuses the repo's unified LLM
path (`llm.call`), the scorer's extraction/evaluation (the sole fit judge), the
deterministic `.cv_pipeline` render harness, and `JobStorage` — no new tables,
no tracker changes, no scorer changes. The checkpointer→interrupt→resume topology
is the project's first real LangGraph agent and its reusable foundation.

## Technical Context

**Language/Version**: Python 3.11.15 (repo convention: `X | Y` unions, f-strings)

**Primary Dependencies**: `langgraph` (>=1.2,<2) + `langgraph-checkpoint-sqlite`
(>=3.0,<4) — **the only new dependencies**, justified in §Complexity Tracking.
Existing reuse: `llm`, `storage`, `paths`, `profiles`, `models`, `scorer`,
`job_actions`, `httpx`, `beautifulsoup4`.

**Storage**: SQLite (`data/jobs.db`, WAL) — read/write via `JobStorage` only. One
new file: the LangGraph checkpointer `data/cv_agent_checkpoints.sqlite`
(`paths.data_path("cv_agent_checkpoints.sqlite")`), gitignored with the data dir.
No schema change.

**Testing**: `pytest tests/` (162 storage tests — must stay green; no storage
changes). The agent itself is validated empirically per `quickstart.md` (§VI):
real `jobs.db` job, a live URL, and a pasted posting.

**Target Platform**: dev Mac (LibreOffice at
`/Applications/LibreOffice.app/Contents/MacOS/soffice`) and prod Ubuntu
(`soffice` on PATH).

**Project Type**: CLI library package (`cv_agent/`), run as
`python -m cv_agent.cli …`, independent of the Streamlit tracker.

**Performance Goals**: not latency-critical; one job per invocation. LLM rate
limits respected by the existing `llm.call` backoff (no extra sleeps).

**Constraints**: two interrupt gates are terminal; revise loop hard-capped at 3;
`renderer.py` imports no `llm`; no `bind_tools`/`tools.py`; factual integrity —
rendered CV introduces no claim absent from `cv_data_master.json`.

**Scale/Scope**: single-user, solo project. ~7 new files in `cv_agent/`, one new
dependency pair, one checkpoint DB.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Filet large / scorer seul juge de fit | ✅ PASS | URL/paste route through `extract_job_fields` + `evaluate_for_profile`; `analyze_and_plan` reads score, never emits one. |
| II. Deux chemins | ✅ PASS | Agent is code (dev→git→deploy); no new runtime config — active profile via existing `config`. |
| III. Profil unifié unique | ✅ PASS | Uses `load_active_profile(db)` → single profile; `profile_id` preserved. |
| IV. Structure déterministe, prose LLM | ✅ PASS | Orchestration + `renderer.py` deterministic; LLM emits only JSON/prose; revise loop is an integer bound. |
| V. Chirurgical | ✅ PASS (1 justified exception) | New `cv_agent/` package + checkpoint DB; no scorer/schema/scraper/tracker change. Exception: `langgraph` dep — justified in §Complexity Tracking. |
| VI. Validation empirique | ✅ PASS | `quickstart.md` scenarios 1–3 validate on real data. |
| VII. Sécurité | ✅ PASS | LLM egress only via `llm.call`; `refresh_context` fetches only the declared URL; no new secrets. |
| VIII. Scrapers | N/A | No scraper touched. |
| IX. Scoring optionnel | ✅ PASS | FR-018: falls back to `DEFAULT_PROFILE_ID`, `refresh_context` optional, graceful on fetch failure. |

**Re-check after Phase 1 design**: no change — the design (research §1–§10)
preserves all guardrails. The single deviation (new dependency) is the one the
spec itself mandates.

## Project Structure

### Documentation (this feature)

```text
specs/029-cv-cover-letter-agent/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── cli-contract.md
│   └── node-io-contract.md
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
cv_agent/
├── __init__.py     # package docstring; re-exports compile_graph()
├── state.py        # CVAgentState TypedDict + revision_count reducer
├── prompts.py      # one prompt constant per LLM node (master-derived anchors)
├── nodes.py        # node functions (heavily commented — FR-017)
├── renderer.py     # deterministic render wrapper (imports NO llm) — factual firewall
├── graph.py        # StateGraph: nodes, edges, conditional edges, checkpointer, compile
└── cli.py          # human driver loop: run, handle 2 interrupts, prompt/parse, resume
```

**Structure Decision**: single flat package `cv_agent/` at the repo root
(matching the existing flat module layout `llm.py`, `storage.py`, `scorer.py`).
No `src/` nesting, no tests dir of its own (validation is empirical per §VI; the
grep-based invariants live in `quickstart.md`).

## Complexity Tracking

> Filled because Constitution §V has one justified violation: a new dependency.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| New dependency `langgraph` + `langgraph-checkpoint-sqlite` | The spec's stated goal is a LangGraph agent whose checkpointer→interrupt→resume topology is the reusable foundation for future human-validated agents; `interrupt_before`, `interrupt()`, `SqliteSaver`, and resume-via-`invoke(None)` are LangGraph primitives (FR-015/FR-017). | A hand-rolled state machine with pickle persistence would re-implement exactly these primitives, add maintenance surface, and forfeit the learning value and the "reusable foundation" — the spec explicitly rejects this shape. |

No other constitutional deviations.
