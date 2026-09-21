# Contract — CLI (V1)

`cv_agent` is a **CLI-only** package, run from the repo root, independent of the
Streamlit tracker.

## Invocation

```bash
python -m cv_agent.cli <job_id>            # existing scored job
python -m cv_agent.cli <url>               # live careers/ATS URL (fresh entry)
python -m cv_agent.cli <url> --letter      # force a cover letter
python -m cv_agent.cli --paste --letter    # pasted posting text from stdin
python -m cv_agent.cli <job_id> --profile unified_jc   # override active profile
```

- `--paste`: read the posting text from **stdin** (EOF-terminated) and proceed as
  a `paste` entry.
- `--letter`: force `draft_cover_letter` even when the posting doesn't ask for one.
- `--profile <id>`: optional; defaults to the active profile via
  `load_active_profile(db)` → `DEFAULT_PROFILE_ID` fallback (FR-018).

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | ran to `END` (approved, or aborted at a gate) |
| 2 | bad arguments / unreadable reference |
| 3 | LLM not configured (`llm.is_configured()` false) — abort before any node |

## Human-in-the-loop gates (both are terminal interactions)

### Gate 1 — `analysis_gate` (after `analyze_and_plan`)
Prints the fit recap, angle, proposed contact profile (`swiss`/`french`) +
confidence, and gaps. Prompts:
```
[1] proceed   [2] adjust (directives)   [3] abort
```
- `adjust` reads one or more free-text directives, merged into
  `user_directives`, then re-enters `tailor_cv` (not a re-plan).
- `abort` marks the job `archived` and the graph exits; no documents.

### Gate 2 — `approval_gate` (after `render`)
Prints the generated PDF path. Prompts:
```
[1] approve   [2] reject (notes → revise)
```
- `approve` → `file_and_record` → END.
- `reject` with notes → append to directives and re-enter the revise loop
  (bounded by `revision_count ≤ 3`).

## Stdout contract (stable for scripting)

Every run ends with exactly one of:
```
CV_AGENT_RESULT ok        <pdf_path>
CV_AGENT_RESULT aborted   <job_id>
CV_AGENT_RESULT error     <message>
```
(Intermediate node logs are human-readable prose; this last line is the machine
signal.)
