# Tasks — CI gate (spec 044c)

## Phase 1 — Hook

- [X] T001 Write `.claude/hooks/ci_gate.py` — pure stdlib; `run(payload)` reads `tool_input.command`, detects the four release forms, resolves the target SHA, checks `gh run list --commit <sha> --json status,conclusion`, and returns 0 / 2 / 3 with distinct messages (FR-002, FR-003, FR-004).

## Phase 2 — Declare the hook

- [X] T002 Add the `PreToolUse` (matcher `Bash`) entry pointing at `.claude/hooks/ci_gate.py` (venv interpreter, timeout 20) to `.claude/settings.json`, preserving the existing `Stop`/`PostToolUse` hooks (FR-001).

## Phase 3 — Tests

- [X] T003 Write `tests/test_ci_gate.py` — target-detection parametrisation (`_gate_target`) plus pass / block (red, in-progress, no-run) / non-blocking (gh missing, gh failure, bad JSON, unresolvable) and resolution-target tests, all with `git`/`gh` mocked (FR-005).

## Phase 4 — Docs

- [X] T004 Add the one-line rule "no staging, merge or deploy unless the CI of that exact SHA is green" to `CLAUDE.md` (release-process paragraph) and `docs/migration-checklist.md` (release-process section) (FR-006).

## Phase 5 — Validate

- [X] T005 `ruff check .` and `python -m pytest tests/ -q` green; pipe-test the hook by hand against a not-pushed SHA (block) and an unrelated command (pass) per `quickstart.md`.
