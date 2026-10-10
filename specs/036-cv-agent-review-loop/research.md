# Research — spec 036 (CV agent review loop)

Phase 0 findings. Each topic: decision, rationale, alternatives considered.

## 1. PDF page-count library

- **Decision**: `pypdf` — `from pypdf import PdfReader; len(PdfReader(path).pages)`.
- **Rationale**: already declared (`requirements.txt` line 12, `pypdf>=3.0.0`), so no
  new dependency; pure-Python; one call. Presence in both dev and the Docker image is
  already guaranteed by the declared requirement (per the clarify session).
- **Alternatives**: `PyMuPDF`/`fitz` (AGPL, heavier, not declared); `PyPDF2` (stale fork
  of pypdf, not declared). Neither justified when `pypdf` is already committed to.

## 2. Where the lint runs (FR-005)

- **Decision**: a **new `lint` node between `render` and `publish`**, plus a
  `route_after_lint` conditional edge.
- **Rationale**: lint must see the **rendered** `cv_data_<slug>.json` + PDF (it checks
  exactly what the reviewer will see), and must run **before** `publish` so a failing CV
  is not uploaded to Nextcloud. A separate node (not code folded into `render`) keeps the
  deterministic `render` untouched and makes lint unit-testable without the graph.
- **Alternatives**: (a) inside `render` — rejected: couples the harness subprocess with
  the checks and makes seeded-defect tests harder; (b) after `publish` — rejected: would
  publish failing CVs.

## 3. Lint config split (clarify Q2)

- **Decision**: split. Generic style rules (banned phrases, em-dash/punctuation) live in a
  small tracked module `core/cv_agent/lint_config.py`. Personal data (allowed numeric
  tokens, employers, extra roles, in-progress courses, claims) is **not** committed —
  derived at runtime from `cv_data_master.json` (via `renderer.load_master()`), or read
  from a sibling file next to the master in `.cv_pipeline/`.
- **Rationale**: the repo is public on GitHub; personal CV facts must never land in it.
  The master is already the factual source of truth and already lives outside the repo.
- **Alternatives**: single tracked config (rejected — leaks personal data); full runtime
  derivation only (kept as the primary source; the `.cv_pipeline/` sidecar is the
  fallback for facts the master does not carry).

## 4. `review.json` writer placement (FR-006)

- **Decision**: a new `core/cv_agent/review.py` with `write_review(state) -> str`,
  called at the top of `approval_gate` **before** `interrupt()`.
- **Rationale**: every path that reaches `approval_gate` (interactive, `--auto`,
  `--resume --reject`) must leave a fresh bundle, and the bundle must reflect the final
  rendered + lint state — which is available only after `publish`. Writing it in the gate
  node means the CLI loop needs no special case: it is always present when the gate pauses.
- **Alternatives**: writing from the CLI after the pause — rejected: the CLI would have to
  re-read state and duplicate the schema; writing inside the node keeps the schema in one
  place and guarantees freshness.

## 5. `_dict_to_posting` relocation (FR-009, US6)

- **Decision**: move the function **verbatim** to `core/models.py` as
  `posting_from_dict(d: dict) -> JobPosting`; update the three import/call sites:
  `core/job_actions.py` (remove the def, import + call), `core/score.py` (drop the unused
  import, line 15), `core/cv_agent/nodes.py` (import + line-245 call).
- **Rationale**: `JobPosting` is owned by `core/models.py`; a `from_dict`-style
  constructor belongs next to the model. It is already imported by `core/cv_agent`, so the
  agent gains a **public** domain seam instead of reaching into a sibling's private helper.
- **Alternatives**: (a) keep it private and add `core.job_actions` to the allow-list —
  rejected: the whole point of US6 is to shrink the boundary, and the allow-list must be
  the short list of public modules; (b) a new shared module — rejected: overkill, the model
  module is the natural home.
- **No behaviour change**: the body is moved character-for-character; `tags=[]`/`salary=None`
  and the date-parsing guards are preserved.

## 6. Boundary guard shape (FR-010, US6)

- **Decision**: mirror `tests/test_installable_project.py` — an AST walk over git-tracked
  `*.py` (via `git ls-files`), with three rules and an explicit allow-list **in the test
  file**, one comment per entry stating why the agent needs it.
  - Rule A: no `core/cv_agent/**` imports a `core` module outside
    `{models, llm, paths, profiles, scorer, storage}`.
  - Rule B: no `core/cv_agent/**` imports a `_`-prefixed name from `core`.
  - Rule C: no `core/**` outside `core/cv_agent/` imports `core.cv_agent`; the entry-point
    allow-list is **empty** (the agent is launched only via `python -m core.cv_agent.cli`,
    and no domain module imports it — verified at `9fab5fb`).
- **Rationale**: AST + `git ls-files` is the established pattern in this repo (spec 033),
  so the guard is deterministic and ignores untracked/gitignored files (the personal
  `.cv_pipeline/` and `.venv/`).
- **Alternatives**: import-time runtime checks — rejected (a dev-only guard shouldn't ship
  in runtime code); regex on source — rejected (fragile, no name resolution).

## 7. `--auto` / `--resume` resume mechanics (FR-001/002/003)

- **Decision**: `thread_id` is added to the state (seeded by the CLI) so `review.json` can
  carry it and `--resume` can be stateless. `--auto` seeds the same state, invokes the
  graph, auto-resumes `analysis_gate` with `decision=proceed` + overrides
  (`--contact`→`proposed_profile`, `--directives`→`user_directives`, `--title`→`title_override`),
  then runs to the `approval_gate` interrupt and exits 0 (never auto-approving). `--resume
  <thread_id>` locates the checkpoint with `app.get_state(config)` and resumes the
  `approval_gate` with `Command(resume={approved: bool, approval_notes})`.
- **Rationale**: reuses the existing `interrupt()`/`Command(resume=…)` seam unchanged —
  the only new logic is which resume value the CLI supplies and when it stops. `--auto` is
  literally "the analysis gate answered `proceed` for you, then stop at approval".
- **Alternatives**: a separate "auto" graph without gates — rejected: would fork the
  topology and break the "checkpoint stays resumable" acceptance; auto-resume of the
  existing gate keeps one graph and one checkpoint format.

## 8. Lint → revise loop (US5, P2)

- **Decision**: the `lint` node appends failure summaries to `refine_notes` (the existing
  list reducer) when a **retryable** check fails; `route_after_lint` returns `tailor_cv`
  only for the four retryable ids (`facts_numbers`, `roles_match_master`, `em_dash`,
  `pages`) while `revision_count < REVISION_LIMIT`, else `publish`. The other five checks
  surface in `review.json` but never auto-revise (they are systematic/config defects fixed
  in code, not per-run LLM work).
- **Rationale**: reuses `tailor_cv`'s existing "revision notes" injection and the shared
  `revision_count` cap — so a lint-driven revise is counted against `REVISION_LIMIT` exactly
  as the spec requires, and the bound stays a code constant (§IV).
- **Alternatives**: a separate lint-revision counter — rejected: would let lint + critique
  together exceed the intended bound; a new `lint_revise` node — rejected: more topology for
  no benefit over re-entering `tailor_cv`.
