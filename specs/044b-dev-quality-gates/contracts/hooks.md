# Contract — Claude Code hook scripts (spec 044b)

The two hook scripts are pure-stdlib executables driven by a JSON object on
stdin, invoked by Claude Code. This contract is what makes them testable (FR-004)
and what the `PostToolUse`/`Stop` hooks rely on.

## Common

- Invoked by Claude Code with a single JSON object on **stdin** (no CLI args).
- Must finish within the hook timeout declared in `.claude/settings.json`.
- Exit codes:
  - **0** = pass (stdout, if any, is shown to Claude as context);
  - **2** = blocking failure (stderr shown to Claude; Claude continues to fix);
  - **any other non-zero** = non-blocking failure (message shown, no loop).
- The tests that cover them (FR-004) never run the real test suite recursively:
  subprocess calls are injected/mocked.

## `lint_edited.py` (PostToolUse)

- Input: stdin JSON with `tool_input.file_path` (string).
- Behaviour:
  1. `file_path` absent, not ending in `.py`, or outside the project → exit 0
     (no-op).
  2. `ruff` not importable/executable → non-blocking non-zero exit + a message
     (never a silent pass).
  3. Otherwise run `ruff check <file_path>` (selected rules E9/F63/F7/F82):
     - clean → exit 0;
     - findings → exit 2 with ruff's output on stderr.

## `stop_tests.py` (Stop)

- Input: stdin JSON with `stop_hook_active` (bool).
- Behaviour (US1 scenarios 1–6):
  1. Change detection: `git status --porcelain`; if no modified/untracked `.py`
     file → exit 0 (no pytest run).
  2. Run `python -m pytest tests/ -q -x --no-header -p no:cacheprovider` with the
     project venv interpreter.
  3. Pass → exit 0, nothing shown.
  4. Fail + `stop_hook_active == false` → exit 2 with a bounded failure summary
     (failing test ids + last assertion/traceback lines, truncated — e.g. last 60
     lines).
  5. Fail + `stop_hook_active == true` → non-blocking exit (message: "suite still
     red after one forced attempt — review needed"); no infinite loop.
  6. pytest missing / venv not found → non-blocking error (never a silent pass).

- **Budget & fallback**: the pytest step is budgeted at 60 s (the hook timeout).
  If the full suite exceeds it, fall back to the documented fast subset (see
  `quickstart.md`) and exit 0 with a "full suite deferred to CI" note (the subset
  still exits 2 on red). The subset is a *fallback* only — the default is the
  full suite, which is ~15 s today.
