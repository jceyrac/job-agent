# 029 — CV + Cover Letter Agent (LangGraph, CLI V1)

## Input
Build a standalone LangGraph agent that takes a single **job reference** —
an existing `job_id`, a live job **URL**, or **pasted job text** — and produces
a tailored CV, and conditionally a cover letter and/or a recruiter message,
through a fixed pipeline with **two human-in-the-loop gates**, driven from the
**CLI**. The agent reuses the repo's LLM path (`llm.call`), the scorer's
extraction/evaluation, the deterministic `.cv_pipeline` render harness, and
`storage.py`. It runs **independently of the Streamlit tracker** — it is not
imported by it and does not require it.

## Motivation
CV/cover-letter tailoring is done manually in chat today: not repeatable, not
auditable, not linked to the job record. This agent makes the operation
one-command, deterministic where it must be, and records each application
against its job so the tracker can later show the full application history —
the stated end goal. It is also the project's **first real LangGraph agent** and
serves as a learning vehicle, hence the explicit pedagogical-comment
requirement (FR-017).

The topology (fixed pipeline + one bounded revise loop + two interrupts) is the
shape that justifies LangGraph — the checkpointer→interrupt→resume pattern here
is the reusable foundation for every future human-validated agent.

## Scope & version
- **V1 = CLI only.** Both gates are terminal interactions.
- **No tracker UI** in this spec (trigger button + manual enrichment = a later
  spec, subject to the mockup-before-implementation rule).
- **No agentic/LLM-driven web research** inside this agent (that is the future
  research agent, a.k.a. "agent 1"). This agent stays in **Option A**: context
  from the DB plus an optional deterministic refresh of a known URL.

## Context (verified against live source)
- **LLM path** — `llm.call(messages, *, json_mode=True, max_tokens, temperature,
  sleep_after, max_retries, retry_base_delay) -> str`, plus `llm.MODEL` and
  `llm.is_configured()` (`llm.py`). All model calls in the codebase go through
  `llm.call` (spec 013b). The agent MUST use it — **not** LangChain's
  `ChatOpenAI` (which `hf-agents-course/langgraph/rag_agent` used; that was a
  standalone exercise, not the repo convention).
- **Scorer** — `scorer.extract_job_fields(job: JobPosting)` and
  `scorer.evaluate_for_profile(job, profile)` already own field extraction and
  fit judgment (deterministic Tier-0 filters + small-model Tier-1). The URL/paste
  entry path MUST route through these. No fit scoring is re-implemented anywhere
  in this agent.
- **`company_researcher.py`** does **ATS / careers-page discovery** (find the
  board to monitor), **NOT** job/company description enrichment. It is therefore
  **not** the backend for `refresh_context` or `analyze_and_plan`. Description
  enrichment is the future research agent's responsibility.
- **Paths** — `paths.data_path(*parts)`, `paths.DB_PATH` (`paths.py`). The
  checkpointer DB lives at `paths.data_path("cv_agent_checkpoints.sqlite")`.
- **Storage** — `JobStorage` (`storage.py`). Known methods:
  `get_all_for_tracker`, `set_status(job_id, profile_id, status, notes=…)`,
  `get_stats`, `get_all_profiles`, `save_scored` (referenced by the scorer),
  `get_config`/`set_config`. The single-job read and the insert helper names are
  to be confirmed at plan stage (see Open questions).
- **Render harness** — `~/AI-Suite/.cv_pipeline/render_cv.js` (docx-js, Node
  project with its own `package.json`). It reads `cv_data_master.json` and
  per-application `cv_data_<slug>.json` files (e.g.
  `cv_data_senior-product-manager.json`) and emits a `.docx`; PDF via
  LibreOffice. The exact `render_cv.js` CLI argument convention is confirmed at
  plan stage. This harness is **not modified** by this spec.
- **Profiles** — `profiles.ALL_PROFILES`, `profiles.DEFAULT_PROFILE_ID`; the
  active profile is read via `db.get_config("active_profile_id",
  default=DEFAULT_PROFILE_ID)`.

## Design — package & topology
New package `job_agent/cv_agent/`:

```
cv_agent/
├── __init__.py
├── state.py      # CVAgentState (TypedDict) — the data model
├── prompts.py    # one prompt constant per LLM node
├── nodes.py      # node functions (the pipeline logic — heavily commented)
├── renderer.py   # deterministic render wrapper (imports NO llm) — factual firewall
├── graph.py      # StateGraph: nodes, edges, conditional edges, checkpointer, compile
└── cli.py        # human driver loop: run, handle the 2 interrupts, prompt/parse, resume
```

Reuses (imports, no duplication): `llm`, `storage`, `paths`, `profiles`, the
`.cv_pipeline` harness (subprocess). Does NOT reuse `company_researcher` for
enrichment.

Flow (deterministic = det, LLM = llm, human gate = gate):

```
resolve_reference (det)         # job_id | url | pasted text  → JobPosting
  → [url/paste] extract+score via scorer  → persist job record
  → refresh_context (det, OPTIONAL)        # re-fetch known url if stored desc thin; else stub/skip
  → extract_requirements (llm)             # needs, ATS keywords, letter/message expected?
  → analyze_and_plan (llm)                 # fit recap (from score) + angle + CH/FR proposal + gaps
  → analysis_gate (gate, interrupt)        # proceed / adjust(directives) / abort
        abort → mark skipped → END
  → tailor_cv (llm)                        # ALWAYS — re-angle master JSON → cv_data_<slug>.json content
  → draft_cover_letter / message (llm, CONDITIONAL)
  → self_critique (llm)                    # coverage · keywords · factuality → revise ≤ 3×
  → render (det, NO llm)                    # node render_cv.js <data> <outdir> → docx → PDF
  → publish (det, optional)                 # WebDAV PUT to Nextcloud (no-op if CV_NC_* unset)
  → approval_gate (gate, interrupt)        # approve / reject(notes → revise)
  → file_and_record (det)                  # save_application + set_status + record NC URLs
  → END
```

`CVAgentState` (TypedDict) carries at least: `reference`, `entry_kind`
(`id|url|paste`), `job` (JobPosting-shaped dict), `job_id`, `requirements`,
`letter_required`, `recruiter_contact`, `fit_analysis`, `proposed_profile`
(`swiss|french`), `profile_confidence`, `user_directives`, `decision`
(`proceed|abort`), `cv_json`, `cover_letter`, `critique`, `revision_count`,
`approved`, `output_paths`, `nextcloud_web_url`, `pdf_url`.

## Constitutional guardrails (MUST hold)
- **Scorer is the sole fit judge.** `analyze_and_plan` reads the existing
  score/reason; it MUST NOT emit a new fit score. The URL/paste entry routes
  through `scorer.extract_job_fields` + `scorer.evaluate_for_profile`. The human
  is the go/no-go decider at `analysis_gate`; the LLM never re-judges fit.
- **§IV Deterministic structure.** Orchestration and rendering are
  deterministic; the LLM only emits JSON/prose. `renderer.py` imports no `llm`.
  The revise loop is a hard integer bound (≤3), not model-decided.
- **§II Two paths.** The agent is code (dev → git → deploy). No new runtime
  config is invented; the active profile uses the existing `config` plumbing.
- **§V Surgical.** Add the `cv_agent/` package and one checkpoint DB. Do NOT
  modify the scorer, the DB schema, scrapers, the tracker, or the `.cv_pipeline`
  harness. Any missing storage read/insert is added as a thin, documented method
  — no schema change (an application↔job link should reuse existing
  status/notes; a new column/table is an Open question decided at plan).
- **§VI Empirical validation.** Validate on real jobs already in `jobs.db`, on at
  least one live careers/ATS URL, and on one pasted posting.
- **Factual integrity.** `self_critique` MUST verify the tailored content
  introduces no claim absent from `cv_data_master.json`. The interests-section
  rule and em-dash removal stay in the harness (unchanged). The generative node
  can only affect the tailored JSON/prose, which is critiqued before render.
- **No LLM tools in V1.** No `bind_tools`, no `tools.py`, no ReAct loop. The
  topology is fixed; every resource access is deterministic and node-local.

## Functional requirements
- **FR-001** `resolve_reference` entry node accepts `job_id | URL | pasted text`
  and normalises to a `JobPosting`-shaped state (`entry_kind` recorded).
- **FR-002** URL/paste entries are extracted + scored **through the scorer**
  (`extract_job_fields` + `evaluate_for_profile`) using the active profile. No
  scoring logic is duplicated.
- **FR-003** A URL/paste-entered job is **persisted** to the DB under the active
  profile before generation (dedupe by `url`; if it already exists, reuse the
  record). Rationale: the record you act on is what must be historised.
- **FR-004** `refresh_context` (OPTIONAL node): if the stored description is
  below a quality threshold AND a posting URL is available → deterministic
  re-fetch of that URL (`httpx`, no LLM). Else if a research agent is available →
  **delegation stub** (documented, not implemented in V1). Else skip. Fresh text
  is held in **state only (ephemeral)**; this node MUST NOT write enrichment to
  the DB (that is the research agent's job — see Dependencies / Trello #2171).
- **FR-005** `extract_requirements` (LLM via `llm.call`, `json_mode`): critical
  requirements, ATS keywords, and whether a cover letter and/or recruiter
  message is expected (`letter_required`, `recruiter_contact`).
- **FR-006** `analyze_and_plan` (LLM): from the existing score + extracted
  fields, produce a fit recap, a proposed angle, a proposed contact profile
  (`swiss|french`) with a confidence, and the gaps to address. MUST NOT produce
  a fit score.
- **FR-007** `analysis_gate` (interrupt): present the analysis in the CLI; user
  chooses `proceed` | `adjust` (free-text directives injected into state) |
  `abort`. Abort → mark the job reviewed/skipped and END. The contact-profile
  override happens here (no separate ask node), as do optional **Company/Title
  overrides** that patch the job downstream (slug, folder, LLM context) — the
  correction seam for unreliable closed-board extraction.
- **FR-008** `tailor_cv` (LLM): ALWAYS runs. Re-angles `cv_data_master.json` into
  `cv_data_<slug>.json` content per requirements + directives. Content only; it
  MUST NOT fabricate facts absent from the master. The `slug` is the **title
  alone** (e.g. `chief-product-officer-head-of-product-payments`), so the derived
  filename is `Jerome_Ceyrac_CV_<Title>` while the output folder stays
  `<Company> - <Title>`.
- **FR-009** `draft_cover_letter` / recruiter message (LLM, CONDITIONAL): only
  when `letter_required` OR the user passed `--letter`; the recruiter message
  only when a named `recruiter_contact` exists.
- **FR-010** `self_critique` (LLM): checks requirement coverage, keyword match,
  factual accuracy vs master, and tone; emits `needs_revision` + notes. Bounded
  revise loop back to `tailor_cv`, `revision_count` ≤ 3.
- **FR-011** `render` (deterministic, NO LLM): write `cv_data_<slug>.json` into
  the job's local working folder, shell out to `node render_cv.js <data> <outdir>`,
  produce `.docx` + PDF (LibreOffice). `renderer.py` imports no `llm`.
- **FR-012** `approval_gate` (interrupt): present the PDF path; user chooses
  `approve` | `reject` (notes → revise). The human reject loop is **unbounded**
  (each pass is human-validated, so the human decides the bound — §IV); every
  reject note is accumulated into `refine_notes` and re-injected in full into the
  next `tailor_cv`. Only the model-decided `route_after_critique` loop keeps the
  `REVISION_LIMIT` cap.
- **FR-013** publication (deterministic, optional): after render, a `publish`
  node pushes the local `.json`/`.docx`/`.pdf` to Nextcloud over WebDAV
  (MKCOL + PUT; `CV_NC_BASE_URL`/`CV_NC_USER`/`CV_NC_APP_PASSWORD` env vars — a
  clean no-op when unset, so it is host-agnostic and needs no desktop client).
  On approval, `file_and_record` records the Nextcloud URLs in the application
  analysis and updates status via `storage.set_status`, linking the application
  to the job record.
- **FR-014** All model calls go through `llm.call`. No LangChain `ChatOpenAI`, no
  `bind_tools`, no `tools.py`.
- **FR-015** State persistence via a `SqliteSaver` checkpointer at
  `paths.data_path("cv_agent_checkpoints.sqlite")`;
  `interrupt_before=["analysis_gate", "approval_gate"]`; `thread_id` = the
  `job_id` (or a URL-derived id for fresh entries).
- **FR-016** CLI: `python -m cv_agent.cli <job_id|url> [--letter]` and a
  `--paste` mode (read posting text from stdin). It drives `invoke`/resume around
  the two interrupts, printing the analysis report and the generated PDF path.
- **FR-017** Pedagogical comments (explicit, this is a learning vehicle): every
  module has a docstring (role + position in the flow); every node has a
  docstring stating what it reads from state, does, writes back, and which
  LangGraph concept it illustrates; inline comments cover the LangGraph mechanics
  (conditional edges, `interrupt_before`, resume via `invoke(None)`, state
  reducers); `graph.py` carries an ASCII flow header.
- **FR-018** Degrade gracefully: fall back to `DEFAULT_PROFILE_ID` when no active
  profile is set; run with whatever DB context exists (`refresh_context` is
  optional). No crash when `refresh_context`'s fetch fails — skip and continue.

## Clarifications

### Session 2026-09-10
- Q: LLM-driven web research inside this agent? → A: No (Option A). Context comes
  from the DB plus an optional deterministic refresh of a known URL; agentic
  research is the future research agent, reached via a delegation stub.
- Q: Interaction model at the gates? → A: V1 = structured one-round checkpoints
  in the CLI (report → decision + optional directives), not free-form multi-turn
  chat (that is V2).
- Q: `refresh_context` persistence? → A: Ephemeral (state only). DB enrichment
  persistence belongs to the research agent (tracked as Trello #2171).
- Q: URL/paste-entry persistence? → A: Persist the job record (insert under the
  active profile, dedupe by url). This differs from enrichment on purpose: you
  persist the record you act on, not the enrichment.
- Q: Closed job boards (LinkedIn/Indeed)? → A: URL fetch targets careers/ATS
  pages; for closed boards use `--paste`. No per-URL scraping reliability is
  promised.
- Q: Contact-profile determination? → A: `analyze_and_plan` proposes
  `swiss|french` with a confidence; resolved or overridden by the user at
  `analysis_gate`. No separate research/ask node.
- Q: A UI to trigger the agent or enrich data? → A: Out of scope here. The
  tracker UI is a later, mockup-first spec. This agent is tracker-independent.

## Acceptance scenarios
- Given a scored `job_id` in `jobs.db`, When `python -m cv_agent.cli <job_id>`
  runs and the user approves both gates, Then a tailored PDF is produced via the
  deterministic harness, published to Nextcloud over WebDAV, and the job's
  status is updated.
- Given a live careers/ATS URL, When `cli.py <url>` runs, Then the job is
  extracted+scored through the scorer, persisted under the active profile
  (deduped), and a tailored CV is produced.
- Given a closed job board, When the user runs `--paste` with the posting text,
  Then the agent proceeds identically from `extract_requirements` onward.
- Given a job the user judges a poor fit, When they choose `abort` at
  `analysis_gate`, Then the graph exits cleanly and the job is marked skipped;
  no documents are generated.
- Given a cover letter is neither required by the posting nor requested, Then the
  `draft_cover_letter` node is skipped and only the CV is produced.
- Given `self_critique` keeps flagging gaps, Then the revise loop stops after 3
  iterations and proceeds to render with the best draft.
- Given any run, Then no LLM call bypasses `llm.call`, and the tree contains no
  `bind_tools`/`tools.py`.
- Given a rendered CV, Then it contains no claim absent from
  `cv_data_master.json` (factual-integrity check).

## Non-goals
- No Streamlit tracker UI (trigger button or manual enrichment) — later,
  mockup-first spec.
- No agentic/LLM-driven web research inside this agent — the research agent owns
  it.
- No DB enrichment persistence from this agent — the research agent owns it
  (Trello #2171); `refresh_context` stays ephemeral.
- No re-scoring; the scorer stays the sole fit judge.
- No LangChain `ChatOpenAI`, no `bind_tools`, no `tools.py`.
- No changes to the `.cv_pipeline` render harness, the scorer, the scrapers, or
  the DB schema (beyond, if unavoidable, a thin application↔job link — flagged
  below, decided at plan).
- No multi-user, no backend API, no orchestrator (n8n), no CrewAI.

## Success criteria
- `python -m cv_agent.cli <job_id>` produces a tailored PDF through the
  deterministic harness after the two gates, publishes it to Nextcloud over
  WebDAV, and records the status.
- URL entry yields a scored, persisted job and a tailored CV; a closed board is
  handled via `--paste`.
- The revise loop is bounded at 3; `abort` at `analysis_gate` exits cleanly with
  the job marked skipped.
- Every model call goes through `llm.call`; no LangChain/`bind_tools` anywhere in
  the package.
- Factual integrity holds: the rendered CV contains no claim absent from
  `cv_data_master.json`.

## Dependencies & open questions
- Relates to **Trello #2171** (DB enrichment by the research agent + a manual
  enrichment UI). `refresh_context`'s delegation stub is the seam where the
  research agent plugs in later.
- Open (decide at plan): exact `storage` single-job read + insert method names;
  whether an application↔job link needs a new column/table (prefer reusing the
  existing status/notes); the exact `render_cv.js` CLI argument convention.
- Next SpecKit steps: `/plan` → `plan.md` (wiring, exact signatures, data-model,
  quickstart), then `/tasks` → `tasks.md`.
