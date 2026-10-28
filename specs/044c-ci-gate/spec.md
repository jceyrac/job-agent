# Feature Specification: CI gate — no staging, merge or deploy unless that SHA's CI is green

**Feature Branch**: `044c-ci-gate`

**Created**: 2026-10-29

**Status**: Ready for `/speckit.plan`

**Input**: Deferred follow-up recorded while spec 045 was mid switch-on (2026-10-29): "add a CI gate to the Claude Code quality gates (044b family) — a PreToolUse hook on Bash commands matching `git merge`, `git push … main`, `staging.sh up`, `deploy.sh`: resolve the target SHA, check `gh run list --commit <sha> --json status,conclusion`; block (exit 2) unless the latest run is completed + success; clear message for red / in progress / no run (not pushed). Tests for each case with `gh` mocked. One line in CLAUDE.md and docs/migration-checklist.md: no staging, merge or deploy unless the CI of that exact SHA is green."

Numbered `044c` because `044b` (dev quality gates) shipped and `045`/`046` are taken. Complements `044b` (which added the Stop/lint hooks, the GitHub Actions workflow, and secret scanning): 044b *produces* a green/red run per commit; 044c *consumes* it and refuses to promote a commit whose run is not green. Touches no application code path, no schema, no API.

---

## Context (verified against the local repo, 2026-10-29)

- `.claude/settings.json` (committed) already declares a `Stop` hook (`stop_tests.py`) and a `PostToolUse` hook (`lint_edited.py`) — the exact pattern this spec extends with a `PreToolUse` hook (matcher `Bash`).
- `.github/workflows/tests.yml` runs `ruff check .` + `python -m pytest tests/ -q` on every `push` and `pull_request` (044b FR-007). A commit's CI status is therefore queried, not computed.
- `gh` (GitHub CLI) is installed and authenticated on the dev Mac; the hook runs there. The release process (`docs/migration-checklist.md`, CLAUDE.md "Release process") is exactly: `git merge --ff-only <full-sha>` → `git push origin main` → on verva `./scripts/deploy.sh` (which `git pull`s and builds `HEAD`); `scripts/staging.sh up <commit-sha|branch>` stages the exact commit.
- `gh run list --commit <sha> --json status,conclusion` returns a JSON array, newest run first: `[{"conclusion":"success","status":"completed"},…]`, and `[]` when the commit has no run (not pushed / CI didn't trigger).

## The gate

A commit is *green* when the **latest** GitHub Actions run for that exact commit has `status == "completed"` and `conclusion == "success"`. Nothing may be staged, merged, or deployed unless the target commit is green.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — A red SHA cannot be merged, staged or deployed (Priority: P1)

As the user, I want Claude Code to refuse to run a release command when the target commit's CI is red, still running, or absent, so that only a green exact SHA is promoted.

**Independent Test**: Ask Claude Code to run `git merge --ff-only <red-sha>`; the `PreToolUse` hook exits 2, the command does not run, and the stderr explains the red run. Repeating with a green `<sha>` proceeds.

**Acceptance Scenarios**:

1. **Given** a Bash command that is none of the four gated forms (e.g. `git status`, `git merge --abort`, `git push origin feature`, `scripts/staging.sh down`), **Then** the hook exits 0 and does nothing.
2. **Given** `git merge --ff-only <sha>` (or `--no-ff`, or `-m "msg" <sha>`) whose latest run is completed+success, **Then** exit 0.
3. **Given** the latest run is completed with a non-success conclusion, **Then** exit 2 with a message naming the conclusion and telling the model to fix the red build.
4. **Given** the latest run is still running (`status != "completed"`), **Then** exit 2 with a "still in progress — wait" message.
5. **Given** the commit has no run (`[]`), **Then** exit 2 with a "not pushed / no CI run" message.
6. **Given** `git push origin main` (or `HEAD:main`, `-u origin main`), **Then** the target is the current `HEAD`; same pass/block behaviour.
7. **Given** `scripts/staging.sh up <ref>` and `./scripts/deploy.sh`, **Then** the target is `<ref>` (resolved via `git rev-parse`) and `origin/main` (fallback `main`, then `HEAD`) respectively; same pass/block behaviour.

### User Story 2 — The gate degrades to a loud warning, never a silent pass (Priority: P2)

As the user, I want the hook to never *silently* allow a release command when it cannot establish the CI status (missing `gh`, unparseable output, unresolvable target), so the gate is never mistaken for having checked.

**Acceptance Scenarios**:

1. **Given** `gh` is not on PATH, **Then** exit 3 (non-blocking) with "`gh` not found — cannot verify".
2. **Given** `gh run list` exits non-zero or returns non-JSON, **Then** exit 3 with "cannot verify".
3. **Given** the target ref cannot be resolved to a commit, **Then** exit 3 with "cannot resolve the target commit" (the command may still run; CI on push remains the backstop).

### User Story 3 — The written rules match the gate (Priority: P2)

As Claude Code (any model), I want the release checklist and CLAUDE.md to state the same rule the hook enforces.

**Acceptance Scenarios**:

1. **Given** `docs/migration-checklist.md`, **Then** the release-process section gains the rule: no staging, merge or deploy unless the CI of that exact SHA is green.
2. **Given** `CLAUDE.md`, **Then** the release-process paragraph gains the same one line.

---

## Edge Cases

- **Compound commands** — `git checkout main && git merge --ff-only <sha> && git push origin main` is one Bash call: the hook tokenizes on `&&`/`;`/`|` and gates the first gated segment.
- **`git merge --abort`/`--continue`** — no commit to promote; not gated.
- **`git -C /opt/job-agent merge …`** — git-level options (`-C`, `-c`, `--git-dir`, …) and their values are skipped before finding the subcommand.
- **User's own terminal** — manual commands outside Claude Code bypass the hook; GitHub Actions itself (and 044b secret scanning) remain the backstop, exactly as in 044b.
- **Stale `origin/main`** — `deploy.sh` gates on `origin/main` as known locally; after the release flow's `git push origin main`, that ref is current. A stale ref is the "cannot verify → non-blocking" path only if unresolvable, never a false green.
- **Multiple runs per commit** — the first (newest) element decides; an older red run behind a newer green run does not block.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `.claude/settings.json` (committed) gains a `PreToolUse` hook with matcher `Bash` → `.claude/hooks/ci_gate.py` (venv interpreter, `$CLAUDE_PROJECT_DIR`, timeout ≤ 30 s). Existing `Stop`/`PostToolUse` hooks unchanged.
- **FR-002**: `ci_gate.py` reads the hook JSON on stdin, takes `tool_input.command`, recognises the four release forms (`git merge <commit-ish>`, `git push … main`, `staging.sh up <ref>`, `deploy.sh`), and exits 0 for any other command.
- **FR-003**: For a recognised command it resolves the target SHA — explicit ref for merge/staging (`git rev-parse --verify <ref>^{commit}`), `HEAD` for push-to-main, `origin/main`→`main`→`HEAD` for deploy — then runs `gh run list --commit <sha> --json status,conclusion` and, taking the first (newest) element, blocks (exit 2) unless `status == "completed"` and `conclusion == "success"`, with a distinct message for red, in-progress, and no-run.
- **FR-004**: Non-blocking (exit 3) with an explicit message when the target cannot be resolved or the status cannot be verified (`gh` missing, non-zero exit, non-JSON/empty output). Never a silent pass.
- **FR-005**: `tests/test_ci_gate.py` covers every case above with the `gh`/git subprocesses mocked (no network, no real `gh`/`git`), following the `tests/test_dev_hooks.py` loader pattern.
- **FR-006**: `docs/migration-checklist.md` and `CLAUDE.md` each gain the one-line rule: no staging, merge or deploy unless the CI of that exact SHA is green.

### Non-goals

- No change to `staging.sh` / `deploy.sh` themselves (no automatic CI-status check on verva) — the gate lives in Claude Code, consistent with 044b's non-goal.
- No GitHub branch protection on `main` (already deferred in 044b).
- No new dependency: the hook is pure stdlib; it shells out to `git` and `gh` already present on the dev machine.
- No change to `.github/workflows/tests.yml`.

---

## Success Criteria *(mandatory)*

- **SC-001**: `ruff check .` and `python -m pytest tests/ -q` green, including `tests/test_ci_gate.py`.
- **SC-002**: Running the hook by hand against a known-red SHA blocks (exit 2) and prints the red-run message; against a known-green SHA it exits 0; against a not-pushed SHA it exits 2 with the "not pushed" message (see `quickstart.md`).
- **SC-003**: `git merge --abort` and an unrelated `git status` pass through (exit 0).
- **SC-004**: The one-line rule appears in both `CLAUDE.md` and `docs/migration-checklist.md`.
