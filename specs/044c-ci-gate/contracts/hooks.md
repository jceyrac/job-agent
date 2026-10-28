# Hook contract — `.claude/hooks/ci_gate.py` (spec 044c)

A Claude Code `PreToolUse` hook on matcher `Bash`. It runs before every Bash
command, gates the four release commands, and blocks a promotion whose CI is not
green. (This file documents the *new* hook; the shared exit-code contract of the
044b family lives in `specs/044b-dev-quality-gates/contracts/hooks.md`.)

## Input (stdin JSON)

```json
{"tool_name": "Bash", "tool_input": {"command": "<the full bash command>"}}
```

The hook reads only `tool_input.command`. Any other payload shape (missing
command, non-dict input) is treated as "nothing to gate".

## Exit codes

| Code | Meaning | Effect in Claude Code |
|---|---|---|
| 0 | pass — not a gated command, or the target's latest CI run is completed + success | command runs |
| 2 | block — CI red, in progress, or no run for the target SHA | command does not run; stderr shown to Claude |
| 3 | non-blocking — cannot resolve the target or cannot verify CI (`gh` missing, `gh` error, non-JSON) | command runs; stderr shown as a warning |

## Target resolution

| Command form | Target SHA |
|---|---|
| `git merge <commit-ish>` (any of `--ff-only`/`--no-ff`/`-m "msg"`) | `git rev-parse --verify <commit-ish>^{commit}` |
| `git push … main` (incl. `HEAD:main`, `-u origin main`) | `HEAD` |
| `scripts/staging.sh up <ref>` | `git rev-parse --verify <ref>^{commit}` |
| `scripts/deploy.sh` | `origin/main`, then `main`, then `HEAD` |

`git merge --abort`/`--continue` carry no commit → not gated. `git -C <path>` and
other git-level value flags are skipped before the subcommand is read. Compound
commands (`&&`, `;`, `|`, `&`) are split and the first gated segment wins.

## CI check

`gh run list --commit <full-sha> --json status,conclusion` → a JSON array, newest
run first. The first element decides:

- `[]` → block: "no GitHub Actions run … not pushed".
- `status != "completed"` → block: "still in progress".
- `conclusion != "success"` → block: "fix the red build".
- `status == "completed"` and `conclusion == "success"` → pass.

An empty/non-JSON response, a non-zero `gh` exit, or `gh` missing → non-blocking
(3) with an explicit "cannot verify" message — never a silent pass.
