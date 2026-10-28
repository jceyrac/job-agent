# Feature Specification: Development quality gates — make the existing test suite mandatory for Claude Code and for every push

**Feature Branch**: `044b-dev-quality-gates`

**Created**: 2026-10-27

**Status**: Clarified — ready for `/speckit.plan`

**Input**: Review of Claude Code tooling (chat, 2026-10-27). Constitution: V (surgical changes), VI (empirical validation), VII (security — public repo), XI (definition of done). "The repo already has ~40 offline tests, including guard tests that enforce the constitution. Nothing forces them to run: no hook, no CI, no linter. Claude Code runs through the DeepSeek proxy and follows written instructions less reliably than deterministic gates. Make the gates deterministic."

Numbered `044b` because `045-ingestion-via-api` is being written. Independent of 044/045: touches no application code path, no schema, no API.

---

## Clarifications

### Session 2026-10-27

- Q: Committed vs local hooks? → A: **Committed** — the two hooks live in `.claude/settings.json` (versioned, reproducible, reviewed in git); the `.gitignore` change in US5-3 enables this. `.claude/settings.local.json` keeps permissions only.
- Q: Stop-hook retries? → A: **One forced fix attempt**, then hand back to the user (non-blocking when `stop_hook_active` is `true`), per US1 scenarios 2–3.
- Q: Branch protection on `main` requiring the CI check? → A: **Defer** — enable after a few weeks of green CI runs; secret scanning + push protection (US4) is enabled immediately.
- Q: Narrow the broad local permissions (`.claude/settings.local.json`)? → A: **Separate follow-up spec** to narrow them (prompt-injection path to verva); out of scope for 044b.

## Context (verified against the local repo and `github.com/jceyrac/job-agent` `main`, 2026-10-27)

### What exists

| Asset | State |
|---|---|
| `tests/` | 42 files; `pytest` configured in `pyproject.toml` (`testpaths = ["tests"]`); dev extra `dev = ["pytest"]` |
| Guard tests | `test_guard_db_access`, `test_guard_llm_provider`, `test_cv_agent_boundary`, `test_network_exposure`, `test_deploy_guard`, `test_migration_safety_net`, `test_api_contract`, `test_api_openapi`, `test_tracker_no_subprocess` … — the constitution is already partly enforced as code |
| Network in tests | The files that mention HTTP (`test_cv_agent_api`, `test_joinup`, `test_freshness`, `test_geo_ch`, `test_network_exposure`) use `TestClient`/ASGI transport, fixtures or monkeypatching — no live network. Live scraper checks live in `tests/run_all.py` + `tests/scraper_checks.py`, which pytest does not collect (no `test_` prefix) |
| `CLAUDE.md` | Rich: never-modify list, `BEGIN IMMEDIATE`, no SQL outside `JobStorage`, mockup rule |
| `.claude/skills/` | SpecKit skills only, including `speckit-analyze` and `speckit-checklist` |

### What is missing

| Gap | Consequence |
|---|---|
| No hooks (`.claude/settings.local.json` holds permissions only; no `.claude/settings.json`) | Claude Code can finish a task with a red suite |
| No CI (`.github/` absent) | A red commit can reach `main`, then `staging.sh`/`deploy.sh` |
| No linter | Undefined names, syntax errors in rarely-executed branches (scrapers, error paths) are found in production by cron |
| Narrow test rule | Constitution "Development Workflow" step 3 and the "Tests" paragraph require `pytest` **only** when `core/storage.py` or `core/models.py` is touched; `CLAUDE.md` "Testing" says the same. Principle VI's rationale says tests "cover storage" only — outdated |
| `.gitignore` contains `.claude/` | Any new project-level Claude Code config would not be versioned. (The SpecKit skills on GitHub were committed before the ignore rule.) |

### Claude Code hook behaviour relied on (docs: hooks reference)

- Hooks are configured in `~/.claude/settings.json`, `.claude/settings.json` (project, committed) or `.claude/settings.local.json` (local, not committed).
- `PostToolUse` takes a `matcher` on tool names (e.g. `Write|Edit|MultiEdit`); exit code 2 shows stderr to Claude (the tool already ran).
- `Stop` takes no matcher; exit code 2 **blocks the stop** and shows stderr to Claude. Its input carries `stop_hook_active: true` when Claude is already continuing because of a stop hook (loop protection).
- Other non-zero exit codes are non-blocking.
- Hooks run in the Claude Code harness, independent of the model behind the proxy.

The plan MUST re-check these semantics against the installed Claude Code version (`claude --version`, `/hooks`) before implementing.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Claude Code cannot finish with a red suite (Priority: P1)

As the user, I want Claude Code to run the test suite automatically when it finishes a turn that changed Python code, and to keep fixing if it fails, so that "done" means "green" regardless of the model behind the proxy.

**Independent Test**: In a scratch branch, ask Claude Code to make a change that breaks a guard test (e.g. add `import sqlite3` in `tracker_views/jobs.py`). The turn does not end silently: the failure is fed back, Claude Code reverts or fixes, and the turn ends with a green suite.

**Acceptance Scenarios**:

1. **Given** a turn that leaves modified or untracked `.py` files in the working tree (`git status --porcelain`), **When** Claude Code stops, **Then** the `Stop` hook runs `python -m pytest tests/ -q -x --no-header -p no:cacheprovider` with the project venv interpreter.
2. **Given** the suite fails and `stop_hook_active` is `false`, **Then** the hook exits 2 with a short failure summary (the failing test ids and the last assertion/traceback lines, truncated to a bounded size) on stderr; Claude Code continues.
3. **Given** the suite still fails and `stop_hook_active` is `true`, **Then** the hook does **not** block again (non-blocking exit, message to the user: "suite still red after one forced attempt — review needed"). No infinite loop, bounded LLM spend.
4. **Given** no `.py` file changed (doc-only turn, spec writing, conversation), **Then** the hook exits 0 immediately without running pytest.
5. **Given** the suite passes, **Then** exit 0, nothing shown.
6. **Given** pytest is missing or the venv is not found, **Then** non-blocking error with an explicit message (never a silent pass).
7. **Given** the Stop hook runs pytest, **Then** it removes `LLM_API_KEY` and `DEEPSEEK_API_KEY` from the subprocess environment, so a forgotten real LLM call raises instead of spending money (CI has no secret; the suite is offline and enforced).

---

### User Story 2 — Hard errors are caught at edit time (Priority: P1)

As the user, I want every Python file Claude Code writes to be checked immediately for errors that are always bugs (syntax errors, undefined names), so that they are fixed in the same step rather than discovered by cron.

**Acceptance Scenarios**:

1. **Given** `ruff` added to the `dev` extra and a `[tool.ruff.lint]` section in `pyproject.toml` selecting **only** `E9`, `F63`, `F7`, `F82` (syntax/IO errors, invalid comparisons, statement errors, undefined names), **Then** `ruff check .` is the project lint command.
2. **Given** a `PostToolUse` hook with matcher `Write|Edit|MultiEdit`, **When** the edited file ends in `.py`, **Then** the hook runs `ruff check <that file>`; on findings it exits 2 with ruff's output on stderr; otherwise exit 0. Non-`.py` files are ignored.
3. **Given** the rule set, **Then** no style rule, no unused-import rule, no formatter is enabled (Principle V: no diff noise, no opportunistic refactor).
4. **Given** the baseline run of `ruff check .` on the current tree, **Then** the tree is clean before the hooks are enabled. The single baseline finding (`F821` in `core/prepare.py`, research R2) was fixed by a separate hotfix on `main`, not in this spec — 044b makes no application-code change. A future false positive would be excluded by a narrow `per-file-ignores` entry with a comment.

---

### User Story 3 — Nothing red reaches staging or main (Priority: P1)

As the user, I want GitHub to run lint and tests on every push and pull request, so that the staging-first flow (spec 033) only ever deploys a green SHA, including commits I make by hand outside Claude Code.

**Acceptance Scenarios**:

1. **Given** `.github/workflows/tests.yml`, **When** any branch is pushed or a PR is opened, **Then** a job on `ubuntu-latest`, Python 3.11, runs `pip install -e ".[dev]"`, `ruff check .`, `python -m pytest tests/ -q`.
2. **Given** the workflow, **Then** it declares `permissions: contents: read`, uses no secret, pins third-party actions to a full commit SHA (with the version in a comment), and sets a job timeout (≤ 15 min).
3. **Given** the workflow, **Then** no test reaches the network beyond `pip install` (tests are offline today; a test that needs the network is a bug in the test).
4. **Given** a red run on a branch, **Then** the release checklist forbids `staging.sh` on that SHA and merging to `main`.
5. **Given** the README, **Then** it shows the workflow status badge.

---

### User Story 4 — Secrets cannot be pushed to the public repo (Priority: P1)

As the user (public repo, prior cyberattack), I want GitHub to block a push that contains a secret.

**Acceptance Scenarios**:

1. **Given** the repository settings, **Then** secret scanning and push protection are enabled (manual step by the user in GitHub → Settings → Code security; documented in `quickstart.md`; no code).
2. **Given** a test push of a fake but well-formed provider key on a scratch branch, **Then** the push is rejected. (Use a pattern GitHub documents for testing; never a real key.)

---

### User Story 5 — The written rules match the gates (Priority: P2)

As Claude Code (any model), I want the constitution and `CLAUDE.md` to state the same test rule the hooks and CI enforce, so that instructions and gates never disagree.

**Acceptance Scenarios**:

1. **Given** the constitution, **Then** it is amended to **v2.1.0** (MINOR: material expansion of the testing rule), with a Sync Impact Report:
   - Development Workflow, code path step 3 → "`ruff check .` and `python -m pytest tests/` green before every commit that touches Python code" (instead of storage/models only).
   - New rule: every bug fix MUST ship with a regression test that fails before the fix, unless the bug is only observable against live sources (then the known-case check of Principle VI applies and the spec says why).
   - Spec path: `/speckit.analyze` MUST run between `/speckit.tasks` and `/speckit.implement`.
   - "Tests" paragraph and Principle VI rationale updated to describe the current suite (storage, API contract/auth/isolation, guards, scrapers on fixtures) and the gates (hooks, CI). `tests/run_all.py` stays intentional-only.
2. **Given** `CLAUDE.md`, **Then** "Commands" gains `ruff check .`; "Testing" is aligned with the amended constitution and mentions that the Stop hook will block on a red suite.
3. **Given** `.gitignore`, **Then** `.claude/` is replaced by rules that version `.claude/settings.json` and `.claude/hooks/` and keep everything else under `.claude/` ignored — in particular `.claude/settings.local.json` (local permissions) stays ignored. (Git cannot re-include a file inside an ignored directory: the rule must be `.claude/*` + `!` exceptions.)

---

### Edge Cases

- Long suite: the Stop hook times the pytest run; above 60 s it prints a non-blocking warning ("suite exceeded 60 s budget — consider a subset spec"). No subset is implemented (the suite is ~15 s today; YAGNI). Decision recorded in `research.md` with the measured time.
- A test is flaky: the hook must not be weakened; the flaky test is fixed or marked with a reason in the same spec. No `--lf`/retry plugin.
- Claude Code is used for a doc-only task (specs, CLAUDE.md): no `.py` change → no pytest run (US1-4).
- Untracked scratch `.py` files in the repo trigger the run; acceptable — the run is cheap and such files should not be there.
- The user's own manual commits bypass Claude Code hooks: CI (US3) catches them.
- Hook script crash (bug in the hook itself): non-blocking, explicit message; covered by tests (FR-004).
- Claude Code version changes hook semantics: `quickstart.md` lists `/hooks` and a one-line manual check to re-run after Claude Code upgrades.

---

## Requirements *(mandatory)*

### Functional Requirements

**Hooks**

- **FR-001**: `.claude/settings.json` (committed) declares exactly two hooks: `PostToolUse` (matcher `Write|Edit|MultiEdit`) → `.claude/hooks/lint_edited.py`; `Stop` → `.claude/hooks/stop_tests.py`. Commands use `"$CLAUDE_PROJECT_DIR"` and the project venv interpreter (`.venv/bin/python`). No permissions block in this file (permissions stay in the local, ignored file).
- **FR-002**: `lint_edited.py` reads the hook JSON on stdin, takes `tool_input.file_path`, ignores non-`.py` paths and paths outside the project, runs `ruff check <path>`, exits 2 with ruff output on findings, 0 otherwise, and a non-blocking non-zero code with a message if ruff is not installed.
- **FR-003**: `stop_tests.py` implements US1 scenarios 1–7: change detection via `git status --porcelain`, pytest invocation **with `LLM_API_KEY`/`DEEPSEEK_API_KEY` removed from the subprocess environment**, output truncation (bounded, e.g. last 60 lines), `stop_hook_active` loop guard, and a timed run that prints a non-blocking warning above 60 s (no subset — the suite is ~15 s). Overall timeout below the hook timeout set in `settings.json`.
- **FR-004**: Both hook scripts are pure stdlib Python, testable by feeding JSON on stdin; `tests/test_dev_hooks.py` covers: non-`.py` ignored, ruff finding → exit 2, clean file → 0, no changes → no pytest, red suite + `stop_hook_active=false` → 2, red suite + `stop_hook_active=true` → non-blocking, missing tool → non-blocking. Subprocess calls are injected/mocked; the test never runs the real suite recursively. Offline is also enforced: an autouse fixture in `tests/conftest.py` makes `socket.connect` to any non-loopback address raise (stdlib `socket`, no new dependency; `TestClient`/ASGI transport on loopback keeps working), and `tests/test_offline_guard.py` proves a non-loopback connect fails.

**Lint**

- **FR-005**: `ruff` added to `[project.optional-dependencies] dev` with a version range; `[tool.ruff]` `target-version = "py311"`; `[tool.ruff.lint] select = ["E9", "F63", "F7", "F82"]`. Justification for the new dependency: dev-only, not installed in the Docker image, catches always-bug classes (Constitution, Dependencies).
- **FR-006**: Baseline `ruff check .` clean, as in US2-4.

**CI**

- **FR-007**: `.github/workflows/tests.yml` as in US3 (triggers `push` on all branches and `pull_request`; read-only permissions; SHA-pinned actions; pip cache allowed; timeout).
- **FR-008**: README badge for the workflow.

**Repository and docs**

- **FR-009**: `.gitignore` change as in US5-3; `git check-ignore -v` evidence in `research.md` that `settings.local.json` is still ignored and `settings.json` + `hooks/` are not.
- **FR-010**: Constitution v2.1.0 and `CLAUDE.md` updates as in US5; commit message `docs: amend constitution to v2.1.0 (test gates, regression tests, analyze step)`.
- **FR-011**: `specs/044b-dev-quality-gates/quickstart.md`: enabling secret scanning + push protection, how to see hooks (`/hooks`), how to run the gates by hand, re-check after Claude Code upgrade, and a note that branch protection on `main` is deferred (enable after a few weeks of green CI runs).

### Non-goals

- No application code change (the baseline `F821` in `core/prepare.py` was fixed by a separate hotfix on `main`, out of this spec's scope — research R2).
- No style lint, no formatter (`ruff format`), no import sorting, no type checker (mypy/pyright) — candidates for a later spec once the gates are proven.
- No additional skills, no reviewer subagent, no `/security-review` automation.
- No change to `staging.sh` / `deploy.sh` (no automatic CI-status check on verva); the green-SHA rule is a checklist rule in this spec.
- No pre-commit framework: the Stop hook covers Claude Code, CI covers everything else.
- No change to `tests/run_all.py` or live scraper checks.
- No tightening of `.claude/settings.local.json` permissions — deferred to a separate follow-up spec (prompt-injection path to verva).

---

## Success Criteria *(mandatory)*

- **SC-001**: On a scratch branch, a deliberately broken guard (e.g. `import sqlite3` in a tracker view) is caught by the Stop hook and fixed by Claude Code before the turn ends, with Claude Code running through the DeepSeek proxy.
- **SC-002**: A `.py` file with an undefined name written by Claude Code is flagged by the PostToolUse hook in the same step.
- **SC-003**: GitHub Actions is green on `044b-dev-quality-gates`; a deliberately red commit on a scratch branch turns it red.
- **SC-004**: A push containing a test secret is rejected by push protection.
- **SC-005**: Constitution v2.1.0, `CLAUDE.md` and the gates state the same rule (`/speckit.analyze` finds no inconsistency).
- **SC-006**: Stop-hook overhead on a typical code turn ≤ 60 s (measured); above 60 s the hook warns non-blocking (no subset).

