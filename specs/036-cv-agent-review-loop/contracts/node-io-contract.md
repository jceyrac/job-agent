# Contract — Node I/O & graph topology (V2)

Extends `specs/029-…/contracts/node-io-contract.md` (V1). One node is added (`lint`),
one conditional router is added (`route_after_lint`), and the topology is adjusted.
No existing node's behaviour changes except: `approval_gate` now writes `review.json`
before pausing, and `tailor_cv`'s revise notes may now also originate from lint.

## Node signatures — deltas only

| Node | Type | Reads state | Writes state |
|------|------|-------------|--------------|
| `lint` *(new)* | det | `output_paths` (json + pdf), `proposed_profile`, master + allowlist | `lint`, `lint_ok`; on a retryable failure also `refine_notes` (appends human-readable failure summaries via the existing list reducer) |
| `approval_gate` *(changed)* | gate | + `lint`, `lint_ok`, `critique`, `requirements`, `fit_analysis`, `revision_count`, `thread_id`, `job`, `cv` (from `output_paths.json`) | `approved`, `approval_notes`, `refine_notes` (append on reject); **writes `review.json` before `interrupt()`** |

`lint` is a pure-Python node: no LLM, no network, no subprocess except reading the
PDF page count with `pypdf`. It checks the rendered `cv_data_<slug>.json` (from
`output_paths.json`) and the PDF (from `output_paths.pdf`).

## Lint checks (US3)

| id | Reads | Fails when |
|----|-------|-----------|
| `pages` | PDF | page count > 2 (via `pypdf`) |
| `em_dash` | rendered JSON text fields | any `—` (U+2014) present; en-dash `–` in dates allowed |
| `facts_numbers` | profile/competencies/bullets | a numeric token not in the master or allowlist |
| `roles_match_master` | roles | a role's `dates`/`sub` (employer + location) not matching a master role (allowlisted extras OK) |
| `current_role_first` | roles | a role ending `Present` is not first |
| `contact_consistency` | profile/relocation vs `contact` | base contradicts the contact profile (e.g. "Based in Lausanne" + `FR`) |
| `in_progress_courses` | education | an allowlisted in-progress course lacks the "in progress" label |
| `banned_phrases` | all text fields | a configured banned phrase present |
| `interests_present` | rendered JSON | `interests` section absent |

## Topology (ASCII — V2)

```
START
  → resolve_reference → refresh_context → extract_requirements
  → analyze_and_plan → analysis_gate  ◄── interrupt()
      ├─ abort ─► mark_skipped ─► END
      └─ proceed/adjust
  → tailor_cv → draft_cover_letter → draft_recruiter_message → self_critique
      ├─ needs_revision && revision_count < 3 ─► tailor_cv   (revision_count += 1)
      └─ else
  → render → lint  ◄── deterministic, no LLM
      ├─ retryable fail && revision_count < 3 ─► tailor_cv   (revision_count += 1; lint note in refine_notes)
      └─ else
  → publish → approval_gate  ◄── interrupt(); writes review.json first
      ├─ approve ─► file_and_record ─► END
      └─ reject (unbounded) ─► tailor_cv   (revision_count += 1; refine_notes += note)
```

## LangGraph mechanics (delta)

- `route_after_lint(state)` returns `tailor_cv` **only** when some check in the
  retryable set (`facts_numbers`, `roles_match_master`, `em_dash`, `pages`) is
  `fail` **and** `revision_count < REVISION_LIMIT`; otherwise `publish`. The retryable
  set and the bound are code constants (§IV).
- The two revise loops share the single `revision_count` reducer, so a lint-driven
  revise is counted against `REVISION_LIMIT` exactly as a critique-driven one.
- `thread_id` is seeded in state by the CLI (`_thread_id(reference)`, or the
  `--resume` argument) so `approval_gate` can embed it in `review.json`.
- `approval_gate` calls `write_review(state)` (from `core/cv_agent/review.py`) **before**
  `interrupt(payload)`, so a fresh bundle is always on disk whenever the gate pauses.
- `render` / `publish` / `file_and_record` / `self_critique` are unchanged; the
  `_dict_to_posting` import in `nodes.py` becomes `from core.models import posting_from_dict`.
