# Quickstart — CI gate (spec 044c)

Validate the hook end-to-end. The exit-code contract is in `contracts/hooks.md`;
implementation detail in `tasks.md`.

## Prerequisites

- `gh` installed and authenticated on the dev Mac (`gh auth status`).
- Project venv (`.venv/bin/python`) — the hook runs under it.

## Local gates

```bash
ruff check .                     # E9/F63/F7/F82 clean
python -m pytest tests/ -q       # full suite green, incl. tests/test_ci_gate.py
python -m pytest tests/test_ci_gate.py -q
```

## Pipe-test by hand

Feed the hook the exact stdin Claude Code will send it, and inspect the exit
code + stderr:

```bash
# 1. An unrelated command passes (exit 0, silent).
echo '{"tool_input":{"command":"git status"}}' | .venv/bin/python .claude/hooks/ci_gate.py; echo "exit=$?"

# 2. A commit that was never pushed blocks (exit 2, "not pushed").
echo '{"tool_input":{"command":"git merge --ff-only 0000000000000000000000000000000000000000"}}' \
  | .venv/bin/python .claude/hooks/ci_gate.py; echo "exit=$?"

# 3. The current HEAD — green on main — passes (exit 0). Use your real HEAD.
echo "{\"tool_input\":{\"command\":\"git merge --ff-only $(git rev-parse HEAD)\"}}" \
  | .venv/bin/python .claude/hooks/ci_gate.py; echo "exit=$?"

# 4. gh unavailable → non-blocking (exit 3, "gh not found").
echo '{"tool_input":{"command":"./scripts/deploy.sh"}}' \
  | PATH=/usr/bin .venv/bin/python .claude/hooks/ci_gate.py; echo "exit=$?"
```

Scenario 3 expects 0 only if the CI run for your current HEAD is completed +
success; if it is still running, the hook correctly exits 2 ("in progress").
Scenario 4 hides `gh` from `PATH` so the hook takes its non-blocking "cannot
verify" path.

## In-turn check (once)

After `/hooks` (or a restart, if the settings watcher had not yet seen the new
hook), ask Claude Code to run `git merge --abort` — it should pass without
comment — then a real `git merge --ff-only <sha>` whose CI is not green — it
should be blocked with the red-run message.

## Definition of done

`ruff check .` green · `pytest tests/` green · pipe-test block/pass/non-blocking
paths confirmed · one-line rule present in `CLAUDE.md` and
`docs/migration-checklist.md`.
