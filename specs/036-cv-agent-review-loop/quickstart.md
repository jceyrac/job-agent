# Quickstart — spec 036 (CV agent review loop)

Runnable validation scenarios. Prereqs: editable install (`pip install -e ".[dev]"`),
`LLM_API_KEY` set, the `.cv_pipeline/` harness present (`paths.CV_PIPELINE_DIR`).

## 1. Unit tests (fast, no LLM/network)

```bash
python -m pytest tests/test_cv_agent_lint.py       # each of the 9 checks + its seeded defect
python -m pytest tests/test_cv_agent_boundary.py   # agent↔domain guard + negative fixtures
python -m pytest tests/test_cv_agent_gate.py       # --auto / --resume / review.json (unchanged gate)
python -m pytest tests/                            # full suite (SC-006)
```

## 2. `--auto` → review bundle (US1, US4, SC-001/003)

```bash
printf '%s\n' "<a real posting's text>" | \
  python -m core.cv_agent.cli --paste --auto --contact swiss
# → exits 0; last line: CV_AGENT_RESULT review <.../review.json> <thread_id>
```

Then inspect the bundle: `cat "<dir>/review.json"` — confirm the `job`, `requirements`,
`fit_analysis`, `cv` (no `photo`/`interests`), `critique`, `lint` (9 entries),
`lint_ok`, `thread_id` keys are present, and the file is ≤ 20 KB (SC-003).

## 3. `--resume … --approve` finishes the run (US2, SC-001)

```bash
python -m core.cv_agent.cli --resume <thread_id> --approve
# → CV_AGENT_RESULT ok <pdf_path>
```
Verify the application row exists and the job status is `ready` (unchanged
`file_and_record`), and that the whole `--auto` → `--resume --approve` pair used
zero `input()` calls (SC-001).

## 4. `--resume … --reject` re-enters the revise loop (US2)

```bash
python -m core.cv_agent.cli --resume <thread_id> --reject "tighten the angle"
# → CV_AGENT_RESULT review <.../review.json> <thread_id>   (re-stopped at approval_gate)
```

## 5. `--resume` error paths (US2-3)

```bash
python -m core.cv_agent.cli --resume deadbeefdeadbeefdead --approve
python -m core.cv_agent.cli --resume <thread_id>          # missing --approve/--reject
# → exit code 5 (or 2), CV_AGENT_RESULT error …
```

## 6. Interactive regression (SC-004)

```bash
python -m core.cv_agent.cli --paste --letter < posting.txt
```
Confirm both gates still prompt, `CV_AGENT_RESULT ok/aborted` unchanged, and
`tests/test_cv_agent_gate.py` passes (interactive mode byte-for-byte unchanged).

## 7. Empirical validation (SC-005, Constitution §VI)

Run `--paste --auto` then `--resume … --approve` on the three hand-done postings
(Pennylane, CRS Product Lead, FELFEL). For each, record whether the draft is
**ship / small patch / rewrite**, and whether `lint_ok` was true (a `false` on the
current master-derived CV is a documented real defect, per SC-002).

## 8. Boundary guard negative check (SC-006)

Temporarily add `from core import something_else` (or `import core.cv_agent`) to a
module the guard covers, confirm `tests/test_cv_agent_boundary.py` fails on the right
rule, then revert.
