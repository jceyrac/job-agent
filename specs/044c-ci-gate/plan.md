# Implementation Plan — CI gate (spec 044c)

## Technical context

- **Language / runtime**: Python 3.11, pure stdlib (the hook must load with zero deps — it runs in the project venv, same as `stop_tests.py` / `lint_edited.py`).
- **Harness contract**: Claude Code `PreToolUse` on matcher `Bash`; stdin carries `{"tool_input":{"command": str}}`; exit 0 = pass, 2 = blocking (stderr shown to Claude), other non-zero = non-blocking. Declared in `.claude/settings.json`.
- **External processes**: `git rev-parse --verify <ref>^{commit}` (SHA resolution) and `gh run list --commit <sha> --json status,conclusion` (CI status). Both are already present on the dev Mac; neither is a new dependency.

## Constitution check

- **V (surgical)**: three net-new small files + two one-line doc edits; no application code touched. ✓
- **VI (empirical)**: every exit branch has a test; the gh/git side-effects are mocked so the tests are deterministic and offline. ✓
- **VII (security, public repo)**: the hook prints only a truncated SHA and CI status — never a token, URL, or body. ✓
- **XI (definition of done)**: `ruff check .` + full `pytest` green; no deploy of this spec (it changes no deployed artefact — the hook runs on the Mac only). ✓

## Design

One module, four pure helpers around `run(payload)`, mirroring the 044b hook structure so tests can drive it with monkeypatched subprocesses:

- `_tokens` / `_segments` / `_classify` / `_gate_target` — split the command on shell control operators (`&&`, `;`, `|`, `&`), classify each simple command, return the first `(kind, ref)` or `None`.
- `_rev_parse` / `_resolve_sha` — full-SHA resolution: explicit ref for merge/staging, `HEAD` for push-to-main, `origin/main`→`main`→`HEAD` for deploy.
- `_gh_runs(sha) -> (code, stdout)` — `(None, "")` signals "gh not installed"; otherwise the CompletedProcess code + stdout.
- `run` — glue: no gated target → 0; unresolvable/unverifiable → 3 with a message; `[]` → 2 ("not pushed"); `status != completed` → 2 ("in progress"); `conclusion != success` → 2 ("red"); else 0.

The newest-run-wins rule is `runs[0]` (gh lists newest first).

## Phases

1. **Hook** — write `.claude/hooks/ci_gate.py`.
2. **Declare** — add the `PreToolUse` (matcher `Bash`) entry to `.claude/settings.json`.
3. **Tests** — write `tests/test_ci_gate.py` (target-detection parametrisation + pass/block/non-blocking branches + resolution targets).
4. **Docs** — one line each in `CLAUDE.md` and `docs/migration-checklist.md`.
5. **Validate** — `ruff check .` + `python -m pytest tests/ -q` green; pipe-test the hook by hand (see `quickstart.md`).

## Files

| Path | Action |
|---|---|
| `.claude/hooks/ci_gate.py` | new |
| `.claude/settings.json` | add PreToolUse hook |
| `tests/test_ci_gate.py` | new |
| `CLAUDE.md` | add one line |
| `docs/migration-checklist.md` | add one line |
| `specs/044c-ci-gate/{spec,plan,tasks,quickstart}.md`, `contracts/hooks.md` | this spec |
