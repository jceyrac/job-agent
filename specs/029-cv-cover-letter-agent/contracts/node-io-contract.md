# Contract — Node I/O & graph topology

The pipeline is a fixed LangGraph `StateGraph`. Every node reads from and writes
back to `CVAgentState` (see `data-model.md` §1). "det" = deterministic (no LLM);
"llm" = one `llm.call(..., json_mode=True)`; "gate" = `interrupt()`.

## Node signatures (logical)

| Node | Type | Reads state | Writes state |
|------|------|-------------|--------------|
| `resolve_reference` | det | `reference`, `profile_id` | `entry_kind`, `job`, `job_id`, `description_text`, `score`, `score_reason` |
| `refresh_context` | det, optional | `entry_kind`, `job`, `description_text` | `description_text` (ephemeral, never DB) |
| `extract_requirements` | llm | `job`, `description_text` | `requirements`, `letter_required`, `recruiter_contact` |
| `analyze_and_plan` | llm | `score`, `score_reason`, `requirements`, `job` | `fit_analysis`, `proposed_profile`, `profile_confidence` |
| `analysis_gate` | gate | `fit_analysis`, `proposed_profile`, `profile_confidence`, `job` | `decision`, `user_directives` (override `proposed_profile` here; optional `company`/`title` → patch `job`; optional `title_override` → deterministic header title) |
| `mark_skipped` | det | `job_id` | (writes `archived` status) |
| `tailor_cv` | llm | `job`, `requirements`, `fit_analysis`, `user_directives`, `refine_notes` | `cv_content`, `slug` |
| `draft_cover_letter` | llm, conditional | `job`, `requirements`, `fit_analysis`, `user_directives` | `cover_letter` |
| `draft_recruiter_message` | llm, conditional | `recruiter_contact`, `job`, `fit_analysis` | `recruiter_message` |
| `self_critique` | llm | `cv_content`, `cover_letter`, `recruiter_message`, `requirements`, master | `critique`, `needs_revision` |
| `render` | det | `cv_content`, `proposed_profile`, `slug`, `job`, `title_override` | `output_paths` (renders .json/.docx/.pdf into the neutral local working dir; header `title` = `title_override` else master) |
| `publish` | det, optional | `output_paths.local_dir`, `job` | `nextcloud_web_url`, `pdf_url` (`None` when `CV_NC_*` unset) |
| `approval_gate` | gate | `output_paths`, `nextcloud_web_url`, `refine_notes` | `approved`, `approval_notes`, `refine_notes` (append on reject) |
| `file_and_record` | det | `output_paths`, `nextcloud_web_url`, `pdf_url`, `job_id`, `cover_letter`, `recruiter_message`, `fit_analysis`, `slug` | (DB writes: `save_application` + `set_status`, records NC URLs in `analysis`) |

## Topology (ASCII, also reproduced in `graph.py` header — FR-017)

```
START
  │
  ▼
resolve_reference ──(url/paste)──► [extract+score via scorer, save_scored]
  │
  ▼
refresh_context (optional; state-only)
  │
  ▼
extract_requirements
  │
  ▼
analyze_and_plan
  │
  ▼
analysis_gate  ◄── interrupt()
  │   ├─ abort ──► mark_skipped ──► END
  │   └─ proceed/adjust
  ▼
tailor_cv ──(conditional: letter_required OR --letter)──► draft_cover_letter
  │                                                        (─ recruiter_contact ─► draft_recruiter_message)
  ▼
self_critique
  │   ├─ needs_revision && revision_count < 3 ──► tailor_cv  (revision_count += 1)
  │   └─ else
  ▼
render (det; node render_cv.js + soffice)
  │
  ▼
publish (det, optional; WebDAV MKCOL+PUT to Nextcloud)
  │
  ▼
approval_gate  ◄── interrupt()
  │   ├─ approve ──► file_and_record ──► END
  │   └─ reject (unbounded) ──► tailor_cv (revision_count += 1; refine_notes += note)
```

## LangGraph mechanics (the reusable foundation — FR-017)

- `StateGraph(CVAgentState)`; nodes registered in the order above.
- `SqliteSaver.from_conn_string(paths.data_path("cv_agent_checkpoints.sqlite"))`
  is a context manager — open it with `with … as saver:` and pass it to
  `compile(checkpointer=saver)`. No `interrupt_before`: the two gates pause via
  `interrupt()` only (combining both mechanisms would double-pause).
- `thread_id` = `job_id` (or a URL-derived id for a fresh entry whose id is
  computed after fetch — use the deterministic `JobPosting.id`).
- Gates call `from langgraph.types import interrupt(payload)`; the CLI renders the
  payload, collects the decision, then resumes with
  `graph.invoke(Command(resume=<decision>), config)` where
  `config = {"configurable": {"thread_id": job_id}}`. The value passed to
  `Command(resume=…)` is returned from `interrupt()` as the resume input.
- Two conditional revise loops: `route_after_critique` reads `needs_revision` +
  `revision_count` and keeps the hard `REVISION_LIMIT` cap (model-decided, §IV);
  `route_after_approval` reads `approved` only — a human **reject** always returns
  to `tailor_cv` with no cap (the human decides the bound). Each reject appends
  its note to `refine_notes` (a list reducer), re-injected in full on the next
  `tailor_cv`.

## Render subprocess contract (deterministic — FR-011)

1. Merge `cv_content` with fixed fields from `cv_data_master.json`
   (`photo`, `relocation`) plus derived `contact` (`CH`/`FR` ← `proposed_profile`)
   and `filename` (`Jerome_Ceyrac_CV_<Title>` — title-only slug; the folder stays
   `<Company> - <Title>`).
2. Write `cv_data_<slug>.json` into the job's local working folder (all three
   deliverables share one folder: `<Company> - <Title>`).
3. Resolve the local working root: `CV_OUTPUT_DIR` env → config `cv.output_dir` →
   default `<data>/cv_outputs` (host-agnostic, no host-specific path).
4. `node render_cv.js <abs path to cv_data_<slug>.json> <output_dir>` →
   `<output_dir>/<filename>.docx`.
5. `soffice --headless --convert-to pdf --outdir <output_dir> <docx>` → PDF.
6. `renderer.py` imports **no** `llm` (factual firewall).

## Publication contract (optional — FR-013)

The `publish` node calls `nextcloud_publish.publish_application(local_dir,
company, title)` → `dict | None`:
- `MKCOL` each level of `Documents/01 Job/Job applications/<Company> - <Title>`
  (201/405/301 = OK), then `PUT` each `.json`/`.docx`/`.pdf` under the user's
  `remote.php/dav/files/<user>/` WebDAV root (BasicAuth from `CV_NC_*` env).
- Returns `None` (and logs "publish disabled") when all three `CV_NC_*` env vars
  are absent; raises on a partial set (misconfiguration).
- `nextcloud_publish.py` imports **no** `llm` and never logs the app-password.
