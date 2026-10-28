# Implementation Plan: Development quality gates

**Branch**: `044b-dev-quality-gates` | **Date**: 2026-10-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/044b-dev-quality-gates/spec.md`

## Summary

Make the existing ~700-test offline suite and a minimal linter deterministic
gates: a Claude Code `PostToolUse` hook lints every edited `.py` file, a `Stop`
hook runs the suite when a turn changed Python and blocks once on red, and a
GitHub Actions workflow runs lint + tests on every push/PR so nothing red reaches
staging or `main`. Adds `ruff` (dev-only, E9/F63/F7/F82) and amends the
constitution to v2.1.0 so the written rule and the enforced rule agree. No
application-code change — the F821 lint baseline (`core/prepare.py`) is fixed by
a separate hotfix, out of 044b scope.

## Technical Context

- **Language/Version**: Python 3.11.
- **Primary Dependencies**: `ruff` (dev-only, new — justified below); `pytest`
  (existing); Claude Code hooks (stdlib Python); GitHub Actions.
- **Storage**: N/A — no schema/data change.
- **Testing**: `pytest` (698 tests, ~15 s); new `tests/test_dev_hooks.py` (FR-004)
  drives the hook scripts by feeding JSON on stdin.
- **Target Platform**: dev Mac (hooks, lint) + GitHub-hosted CI (ubuntu-latest) +
  GitHub repo settings (secret scanning).
- **Project Type**: dev-tooling / CI for a Python repo (no runtime service change).
- **Performance Goals**: Stop-hook overhead ≤ 60 s (measured: full suite ~15 s —
  SC-006); `ruff check <file>` < 1 s on a single edited file. If the full suite
  ever exceeds the 60 s budget, the hook falls back to a documented fast subset
  (see `contracts/hooks.md`) — a stop is never blocked for >60 s.
- **Constraints**: offline (tests never hit the network beyond `pip install`);
  dev-only dependency (not installed in the Docker image); no formatter/style
  lint (Principle V).
- **Scale/Scope**: one repo, one workflow, two hook scripts, ~40 test files.

## Constitution Check

*GATE — re-checked after Phase 1 design (see research.md).*

- **V (surgical changes)**: PASS — non-goals forbid style lint, formatter, import
  sorting, type checker; 044b makes no application-code change (the F821 baseline
  is a separate hotfix).
- **VI (empirical validation)**: PASS — SC-001…SC-006 are concrete, runnable
  acceptance gates (break-a-guard, undefined-name, red CI, secret push).
- **VII (security)**: PASS — `permissions: contents: read`, no CI secrets, push
  protection on the public repo; hooks are deterministic harness gates, not model
  behaviour.
- **XI (definition of done)**: PASS — the feature *is* a strengthening of
  "done = green" (constitution "Development Workflow" + "Tests" expanded).
- **Dependencies (Architecture Constraints)**: `ruff` is a new dependency —
  JUSTIFIED: dev-only (`[project.optional-dependencies] dev`), not in the Docker
  image, catches always-bug classes (E9/F63/F7/F82) that cron currently finds in
  prod. No other new dependency.
- **Amendment**: the plan amends the constitution v2.0.4 → **v2.1.0** (MINOR —
  material expansion of the testing rule, a new regression-test rule, and the
  `/speckit.analyze` gate), per Governance; executed in FR-010 with the required
  Sync Impact Report and commit message.

**Result**: no unjustified violations. Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/044b-dev-quality-gates/
├── plan.md              # this file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (no new data model)
├── quickstart.md        # Phase 1 output (validation guide)
├── contracts/
│   └── hooks.md         # Phase 1 output (hook stdin/exit-code contract)
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
.claude/
├── settings.json        # NEW (committed) — the two hook declarations
└── hooks/
    ├── lint_edited.py   # NEW — PostToolUse lint hook
    └── stop_tests.py    # NEW — Stop test hook
.github/workflows/
└── tests.yml            # NEW — lint + pytest CI
pyproject.toml           # add ruff to dev extra + [tool.ruff] section
.gitignore               # .claude/* with ! exceptions (version settings + hooks)
tests/test_dev_hooks.py  # NEW — FR-004 hook script tests
```

**Structure Decision**: single-project layout (existing repo); the feature adds
only the `.claude/` hooks, the GitHub workflow, and one new test module, and
touches `pyproject.toml`/`.gitignore` config. No `src/` restructuring.

## Complexity Tracking

*(empty — no unjustified constitution violations; the `ruff` dependency and the
constitution v2.1.0 amendment are both justified above.)*
