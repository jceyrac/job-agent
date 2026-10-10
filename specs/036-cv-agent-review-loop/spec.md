# Feature Specification: CV agent review loop (auto mode, deterministic lint, review bundle)

**Feature branch**: `036-cv-agent-review-loop`
**Status**: Draft (2026-10-09) — renumbered from 035 to 036 on 2026-10-10 (035 = roadmap step 1d)
**Scope**: `core/cv_agent/` only. **Not part of the API roadmap** (`docs/roadmap-api.md`).

---

## Relationship to the API roadmap (read first)

This spec is a small, self-contained improvement to the existing spec-029 CV
agent. It is deliberately kept **separate from the API migration** in progress
(specs 031–034, roadmap steps 0–1c, and later steps up to 9 "Chat CV agent").

- It is **not a roadmap step** and does not renumber, replace or pre-empt any of them.
- It **does not** add an API, endpoints, a `web/` client, a chat, tracker UI, or any DB schema change.
- **Sequencing**: spec 034 (move into `core/`) is in progress on branch
  `034-core-package` and moves `cv_agent` into `core/cv_agent/`. This spec is
  written against that layout and MUST be branched from `main` **after 034 is
  merged and deployed**, so the two never share a branch or conflict.
  *(2026-10-09: 034 is merged and deployed at `ba728df`; branch from `main`.)*
  *(2026-10-10: order agreed — spec 035 (roadmap 1d) → this spec → roadmap step 2 →
  step 3. This spec MUST be merged before roadmap step 3 starts: step 3 adds
  `user_id` to `job_applications`, which `file_and_record` writes.)*
- **Forward compatibility, not implementation**: the non-interactive resume
  (thread id + gate decision) and the `review.json` bundle are the same seams a
  future API / chat (roadmap step 9) will need. Design them cleanly, but build
  nothing API-side here.

## Motivation

Today CVs are tailored manually in claude.ai chat, which re-reads the master CV,
the rules and the rendered pages for every application: expensive in tokens
and not repeatable. The spec-029 agent already generates CVs cheaply (DeepSeek
via `llm.call`), but it can only be driven interactively (two `input()` gates)
and its output is spread across logs and files.

Target workflow: **the agent writes, a reviewer (Claude in chat, or the human)
reads one compact bundle and decides**. Systematic defects are fixed in the
agent (prompts/nodes) via Claude Code, not patched by hand each time.

The self-critique node uses the same model that wrote the draft, so it is weak
at catching its own factual drift. Hard rules are therefore moved into a
**deterministic, zero-LLM lint**.

## Context (verified against source at `9fab5fb`, 2026-10-10)

- `core/cv_agent/cli.py` — `run()` invokes the graph, then loops on pending
  interrupts: `analysis_gate` → `prompt_analysis_gate()` (proceed / adjust /
  abort, with directives, `proposed_profile` swiss|french, company/title fixes,
  `title_override`); `approval_gate` → `prompt_approval_gate()` (approve /
  reject + notes). Both read `input()`. Final line: `CV_AGENT_RESULT ok <pdf>`.
  Thread id = `_thread_id(reference)`; checkpointer =
  `SqliteSaver` at `paths.data_path("cv_agent_checkpoints.sqlite")`.
- Topology (`graph.py` / `nodes.py`): … `tailor_cv` → drafts → `self_critique`
  → (`route_after_critique`: back to `tailor_cv` while `needs_revision` and
  `revision_count < REVISION_LIMIT`) → `render` → `publish` (WebDAV, no-op when
  `CV_NC_*` unset) → `approval_gate` → (`route_after_approval`) →
  `file_and_record` (saves application, sets status `ready`).
- `renderer.render_documents()` writes `cv_data_<slug>.json`, `.docx`, `.pdf`
  into `output_paths.local_dir`; `contact` and `filename` are derived in code,
  never by the LLM.
- Existing test: `tests/test_cv_agent_gate.py`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Run to review with zero terminal interaction (Priority: P1)

As the operator (or Claude driving the CLI through a shell tool), I run one
command and get a finished draft plus a review bundle, without answering any
prompt.

**Acceptance**
1. `python -m core.cv_agent.cli --paste --auto [--contact swiss|french] [--directives "..."] [--title "..."] < posting.txt`
   resumes `analysis_gate` automatically with `decision=proceed` and the given
   overrides, runs to `approval_gate`, writes `review.json`, and exits 0 with
   the checkpoint **left pending** at `approval_gate`.
2. Last stdout line: `CV_AGENT_RESULT review <review.json path> <thread_id>`.
3. `--auto` never auto-approves: nothing is recorded and the job status is not
   changed until an explicit approve (Story 2).
4. Without `--auto`, behaviour is unchanged (interactive gates).

### User Story 2 — Approve or reject a pending run non-interactively (Priority: P1)

**Acceptance**
1. `python -m core.cv_agent.cli --resume <thread_id> --approve` resumes
   `approval_gate` with `approved=True` → `file_and_record` runs → prints
   `CV_AGENT_RESULT ok <pdf>`.
2. `python -m core.cv_agent.cli --resume <thread_id> --reject "notes"` resumes with
   `approved=False` and the notes → revise loop → stops again at
   `approval_gate`, rewrites `review.json`, prints the `review` result line.
3. `--resume` on an unknown thread, or one not paused at `approval_gate`, exits
   non-zero with a clear `CV_AGENT_RESULT error …` line.

### User Story 3 — Deterministic lint of the rendered CV (Priority: P1)

After `render`, a pure-Python lint (no LLM, no network) checks the final
`cv_data_<slug>.json` and PDF against the master and the house rules. Each
check yields `pass` / `fail` with the offending text.

Checks (each individually unit-tested with a seeded defect):

| id | Rule |
|----|------|
| `pages` | PDF has ≤ 2 pages |
| `em_dash` | No `—` (U+2014) in any text field (title, profile, competencies, role title/sub/bullets, education). En-dash `–` in dates is allowed |
| `facts_numbers` | Every numeric token in profile/competencies/bullets (e.g. `30%`, `£22M`, `~70%`, `1,000+`, `5–10`) appears in `cv_data_master.json` or in an allowlist |
| `roles_match_master` | Every role's `dates` and `sub` (employer + location) match a master role exactly; allowlisted extra roles (e.g. the job-agent personal project) permitted |
| `current_role_first` | A role whose dates end in `Present` is first |
| `contact_consistency` | Profile/relocation text does not claim a base contradicting the contact profile (e.g. "Based in Lausanne" with `FR`) |
| `in_progress_courses` | Any allowlisted in-progress course (Hugging Face AI Agents) is labelled "in progress" |
| `banned_phrases` | None of a configurable list (e.g. "fully willing to relocate") |
| `interests_present` | Interests section present |

Allowlists and banned phrases live in one small config file (location decided at plan stage).

### User Story 4 — One compact review bundle (Priority: P1)

`review.json` is written to `output_paths.local_dir` at every stop at
`approval_gate`, containing only:

- `job`: id, title, company, url, entry_kind
- `requirements`: critical requirements + ATS keywords (from `extract_requirements`)
- `fit_analysis`: fit_recap, angle, gaps, proposed_profile
- `cv`: the final rendered data (`cv_data_<slug>.json` content) minus `photo` and `interests`
- `critique`: the `self_critique` output
- `lint`: list of `{id, status, detail}`; plus `lint_ok` (all pass)
- `revision_count`, `refine_notes`, `output_paths`, `thread_id`

Target size ≤ 20 KB (≈ 5k tokens), so a reviewer never needs the master CV,
the logs or page images.

### User Story 5 — Lint failures feed the existing revise loop (Priority: P2)

A `fail` on `facts_numbers`, `roles_match_master`, `em_dash` or `pages` routes
back to `tailor_cv` with the lint details as revision notes, **counted against
the existing `REVISION_LIMIT`** (bound decided in code, Constitution §IV). If
the limit is reached, the run still stops at `approval_gate` with `lint_ok=false`
visible in `review.json`. Implement only if it fits cleanly in the graph;
otherwise defer.

### User Story 6 — Explicit boundary between agents and the domain (Priority: P2)

As the maintainer, I want the CV agent to depend on the domain only through a
short list of public modules, so that it can later talk to the domain through
the API instead of imports (roadmap "Agents as API clients"), and could then
be moved to its own repo at no cost if that ever becomes worth it.

**Context (verified at `ba728df`, 2026-10-09)**: `core/cv_agent/` imports
`core.storage`, `core.models`, `core.profiles`, `core.scorer`, `core.llm`,
`core.paths`, and the **private** `core.job_actions._dict_to_posting`.

**Acceptance**
1. `_dict_to_posting` becomes a public function in the module that owns the
   `JobPosting` model (e.g. `core.models.posting_from_dict`), used by both
   `core.job_actions` and `core.cv_agent`; the private name is removed.
2. A guard test (AST) fails if any module under `core/cv_agent/` imports a
   `core` module outside an explicit allow-list (`core.models`, `core.llm`,
   `core.paths`, `core.profiles`, `core.scorer`, `core.storage`), or imports any
   name starting with `_` from `core`.
3. The same test fails if any `core` module outside `core/cv_agent/` imports
   `core.cv_agent` (the domain never depends on an agent). Allow-list for the
   entry points that legitimately launch the agent, if any, is explicit and
   justified.
4. Each rule has a negative fixture proving it fails.

## Requirements

- **FR-001** `--auto` flag with `--contact`, `--directives`, `--title` mapping onto the existing `analysis_gate` resume fields (`proposed_profile`, `user_directives`, `title_override`).
- **FR-002** `--auto` stops at `approval_gate` and exits 0; the checkpoint stays resumable.
- **FR-003** `--resume <thread_id>` with exactly one of `--approve` / `--reject "notes"`.
- **FR-004** New deterministic module (e.g. `core/cv_agent/lint.py`) — no LLM calls, no network, unit-testable without the graph.
- **FR-005** Lint runs after `render` (new node or inside `render`; decided at plan stage) and its result is in state.
- **FR-006** `review.json` writer per User Story 4, rewritten on every stop at `approval_gate`.
- **FR-007** Interactive mode, `file_and_record`, `publish`, the scorer and the DB schema are unchanged.
- **FR-008** Pedagogical comments on any new graph edge/node, consistent with spec 029 (FR-017).
- **FR-009** `_dict_to_posting` replaced by a public function owned by the `JobPosting` model module; no behaviour change (US6-1).
- **FR-010** Agent ↔ domain import-boundary guard test per US6-2/3/4. The allow-list lives in the test file, with one comment per entry saying why the agent needs it; adding an entry is a reviewed change.

## Non-goals

- Anything API / `web/` / chat / tracker UI (roadmap steps 2–9).
- DB schema changes; recording to the Live DB.
- An LLM-as-judge inside the agent; the reviewer (Claude in chat) stays outside the agent.
- A golden-set regression runner (next spec, once this one is validated).
- Changes to Nextcloud publication or to the scorer.

## Success criteria

- **SC-001** A full run (`--paste --auto` then `--resume … --approve`) completes with zero `input()` calls.
- **SC-002** Each lint check has a unit test with a seeded defect that fails it, and the current master-derived CV passes (or the failure is a documented real defect).
- **SC-003** `review.json` ≤ 20 KB on a real posting.
- **SC-004** `tests/test_cv_agent_gate.py` still passes; interactive mode unchanged.
- **SC-005** Empirical validation (§VI): run on 3 postings already done by hand (Pennylane, CRS Product Lead, FELFEL); record for each whether the draft was ship / small patch / rewrite.
- **SC-006** Boundary guard green on the final code; each rule fails on its negative fixture; full suite green.

## Open questions (for `/speckit.clarify`)

1. PDF page-count dependency: which library is already available in `.venv` and the Docker image (avoid adding one if possible)?
2. Lint config location: next to the master in `~/AI-Suite/.cv_pipeline/` or in `core/cv_agent/`?
3. `cv_data_master.json` role titles contain em-dashes (`Product Manager — Banking API`); fix the master once, or have the lint ignore master-inherited role titles?
4. The job-agent personal-project role and its numbers (e.g. "11 sources") are not in the master: add them to the master, or to the allowlist?
5. On the Mac, `--paste` persists the job into the local DEV DB (Live is on verva). Acceptable for this spec (recording to Live is out of scope)?
