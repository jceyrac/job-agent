# Contract — CLI (V2)

Extends `specs/029-…/contracts/cli-contract.md` (V1). V1 invocations, gates and the
interactive loop are **unchanged**; V2 adds non-interactive modes and two result
lines. All new flags are opt-in — without them the CLI behaves exactly as V1.

## Invocation (V2 additions)

```bash
# zero-interaction draft → review bundle
python -m core.cv_agent.cli --paste --auto [--contact swiss|french] [--directives "..."] [--title "..."] < posting.txt
python -m core.cv_agent.cli <job_id|url> --auto [--contact …] [--directives …] [--title …]

# finish a paused run
python -m core.cv_agent.cli --resume <thread_id> --approve
python -m core.cv_agent.cli --resume <thread_id> --reject "notes"
```

- `--auto`: resume `analysis_gate` automatically with `decision=proceed` (and any
  `--contact`/`--directives`/`--title` overrides), run to `approval_gate`, write
  `review.json`, and exit 0 — **never** auto-approves, records nothing, changes no
  job status.
- `--contact swiss|french` → `proposed_profile` override (only meaningful with `--auto`).
- `--directives "…"` → `user_directives` (with `--auto`).
- `--title "…"` → `title_override` (with `--auto`).
- `--resume <thread_id>`: resume a checkpoint paused at `approval_gate`. Requires
  exactly one of `--approve` / `--reject "notes"`.
  - `--approve` → `approved=True` → `file_and_record` → END.
  - `--reject "notes"` → `approved=False` + notes → revise loop → re-stops at
    `approval_gate` (rewriting `review.json`).

## Exit codes (V2)

| Code | Meaning |
|------|---------|
| 0 | ran to END (approved, aborted, or `--auto`/`--resume --reject` stopped at `approval_gate`) |
| 2 | bad arguments / unreadable reference |
| 3 | LLM not configured (`llm.is_configured()` false) |
| 4 | unexpected pause at a non-gate node |
| 5 | `--resume` on an unknown thread, or one not paused at `approval_gate` |

## Stdout contract (V2 — stable for scripting)

Every run ends with exactly one result line:

```
CV_AGENT_RESULT ok        <pdf_path>
CV_AGENT_RESULT aborted   <job_id>
CV_AGENT_RESULT review    <review.json path>  <thread_id>
CV_AGENT_RESULT error     <message>
```

- `ok` — interactive approve, or `--resume --approve`.
- `aborted` — analysis gate chose abort (interactive only).
- `review` — `--auto`, or `--resume --reject` after the revise loop re-stops at
  `approval_gate`. `--auto` prints the `thread_id` so the reviewer can later
  `--resume <thread_id> --approve`.
- `error` — any failure (including code 5 resume-target errors).
