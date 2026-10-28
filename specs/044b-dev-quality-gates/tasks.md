# Tasks: Development quality gates

**Input**: Design documents from `/specs/044b-dev-quality-gates/`

**Prerequisites**: `plan.md`, `spec.md` (user stories P1/P1/P1/P1/P2), `research.md` (R1–R7), `data-model.md` (no entities), `contracts/hooks.md` (hook stdin/exit-code contract), `quickstart.md`.

**Tests**: Requested by the spec (FR-004 — `tests/test_dev_hooks.py`, plus `tests/test_offline_guard.py`). Test tasks are written first and expected to fail before their implementation.

**Cross-spec dependency**: T011 (baseline clean) depends on the separate hotfix that removes the two `preferred_models=PREPARE_LIGHT_MODELS` kwargs in `core/prepare.py` (see `research.md` R2). 044b makes no application-code change; its `ruff check .` baseline is only clean after that hotfix lands on `main` and this branch is rebased onto it.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add the linter dependency and config that both the PostToolUse hook and CI depend on.

- [X] T001 [P] Add `ruff` to `[project.optional-dependencies] dev` (with a version range) and a `[tool.ruff]` section in `pyproject.toml` — `target-version = "py311"` and `[tool.ruff.lint] select = ["E9", "F63", "F7", "F82"]` (FR-005; no style/unused-import/formatter rules)
- [X] T002 Install the dev extras (`pip install -e ".[dev]"`) and confirm `ruff --version` and `ruff check .` run

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Make the committed hook files versionable, and make offline a hard guarantee before any gate relies on it.

- [X] T003 Update `.gitignore` — replace the whole-dir `.claude/` rule with `.claude/*` plus `!` exceptions for `.claude/settings.json` and `.claude/hooks/` (`.claude/settings.local.json` stays ignored); record the `git check-ignore -v` evidence in `specs/044b-dev-quality-gates/research.md` (FR-009 / US5-3)
- [X] T004 Add an **autouse** fixture to `tests/conftest.py` that makes `socket.connect` to any non-loopback address raise (stdlib `socket` only — no new dependency; loopback and `TestClient`/ASGI transport keep working), and `tests/test_offline_guard.py` proving a non-loopback connect fails (FR-004). Verify the full existing suite still passes with the fixture active.

**Checkpoint**: hooks are versionable and the suite cannot touch the network; user stories can begin.

---

## Phase 3: User Story 1 — Claude Code cannot finish with a red suite (Priority: P1) 🎯 MVP

**Goal**: The `Stop` hook runs the suite when a turn changed Python and blocks once on red — with LLM keys stripped.

**Independent Test**: On a scratch branch, break a guard (e.g. `import sqlite3` in `tracker_views/jobs.py`), stop — the hook blocks once, Claude Code reverts/fixes, the turn ends green.

### Tests for User Story 1

> **NOTE: write these first, ensure they fail before implementation.**

- [X] T005 [P] [US1] Write the `stop_tests.py` cases in `tests/test_dev_hooks.py` (FR-004): no `.py` change → exit 0 without pytest; red suite + `stop_hook_active=false` → exit 2 with a bounded summary; red suite + `stop_hook_active=true` → non-blocking exit; missing pytest/venv → non-blocking error; **`LLM_API_KEY`/`DEEPSEEK_API_KEY` are stripped from the pytest subprocess environment**. Inject/mock subprocess calls — never run the real suite recursively.

### Implementation for User Story 1

- [X] T006 [US1] Implement `.claude/hooks/stop_tests.py` per `contracts/hooks.md` (US1 scenarios 1–7, FR-003): `git status --porcelain` change detection, `.venv/bin/python -m pytest tests/ -q -x --no-header -p no:cacheprovider`, **remove `LLM_API_KEY`/`DEEPSEEK_API_KEY` from the subprocess environment**, output truncated to a bounded tail (e.g. last 60 lines), `stop_hook_active` loop guard, and a timed run that prints a non-blocking warning above 60 s (**no subset**).
- [X] T007 [US1] Declare the `Stop` hook in `.claude/settings.json` (FR-001) — command uses `"$CLAUDE_PROJECT_DIR"` and `.venv/bin/python`; no permissions block in this file.

**Checkpoint**: US1 works — Claude Code can no longer end a code turn silently red, and a forgotten LLM call fails instead of spending money.

---

## Phase 4: User Story 2 — Hard errors are caught at edit time (Priority: P1)

**Goal**: The `PostToolUse` hook runs `ruff` on every edited `.py` file in the same step.

**Independent Test**: Write a `.py` file with an undefined name and save it — the hook flags it immediately (SC-002).

### Tests for User Story 2

- [X] T008 [P] [US2] Write the `lint_edited.py` cases in `tests/test_dev_hooks.py` (FR-004): non-`.py` path ignored → exit 0; ruff finding → exit 2 with ruff's output on stderr; clean file → exit 0; ruff not installed → non-blocking non-zero with a message.

### Implementation for User Story 2

- [X] T009 [US2] Implement `.claude/hooks/lint_edited.py` per `contracts/hooks.md` (FR-002): read `tool_input.file_path` from stdin JSON, ignore non-`.py`/out-of-project paths, run `ruff check <path>`.
- [X] T010 [US2] Declare the `PostToolUse` hook (matcher `Write|Edit|MultiEdit`) in `.claude/settings.json` (FR-001) — confirm the matcher/tool names against `/hooks` first (research R1).
- [X] T011 [US2] Verify `ruff check .` is clean on this branch (FR-006 / US2-4). **Depends on the separate `core/prepare.py` hotfix landing on `main` and a rebase**; do not enable the hooks until the tree is clean.

**Checkpoint**: US2 works — syntax/undefined-name bugs are caught at write time, not by cron.

---

## Phase 5: User Story 3 — Nothing red reaches staging or main (Priority: P1)

**Goal**: GitHub Actions runs lint + tests on every push/PR, including manual commits.

**Independent Test**: Push a deliberately red commit on a scratch branch — the workflow turns red (SC-003).

- [X] T012 [US3] Create `.github/workflows/tests.yml` (FR-007): triggers `push` (all branches) + `pull_request`; `ubuntu-latest`, Python 3.11; steps `pip install -e ".[dev]"`, `ruff check .`, `python -m pytest tests/ -q`; `permissions: contents: read`; no secrets; actions pinned to full commit SHA (version in a comment); `timeout-minutes: 15`; pip cache allowed.
- [X] T013 [US3] Add the workflow status badge to `README.md` (FR-008).

**Checkpoint**: US3 works — a green SHA is required before staging/merge.

---

## Phase 6: User Story 4 — Secrets cannot be pushed to the public repo (Priority: P1)

**Goal**: Push protection blocks secrets on the public repo (manual GitHub setting, documented).

**Independent Test**: Push a fake, well-formed provider key on a scratch branch — the push is rejected (SC-004).

- [X] T014 [US4] Finalize `specs/044b-dev-quality-gates/quickstart.md` (FR-011): enabling secret scanning + push protection (Settings → Code security), how to see hooks (`/hooks`), how to run the gates by hand, re-check after a Claude Code upgrade, and the note that branch protection on `main` is deferred. The actual GitHub setting is a manual step by the user (no code).

**Checkpoint**: US4 documented; enabling + validation is a manual user step.

---

## Phase 7: User Story 5 — The written rules match the gates (Priority: P2)

**Goal**: Constitution v2.1.0 and `CLAUDE.md` state the same rule the hooks/CI enforce.

**Independent Test**: `/speckit.analyze` finds no inconsistency (SC-005).

- [X] T015 [US5] Amend `.specify/memory/constitution.md` to **v2.1.0** (FR-010 / US5-1) with a Sync Impact Report: Development Workflow step 3 → `ruff check .` and `python -m pytest tests/` green before every commit touching Python; new rule that every bug fix ships with a regression test (unless live-source-only, then Principle VI known-case); spec path adds `/speckit.analyze` between `/speckit.tasks` and `/speckit.implement`; "Tests" paragraph + Principle VI rationale updated to the current suite. Commit as `docs: amend constitution to v2.1.0 (test gates, regression tests, analyze step)`.
- [X] T016 [US5] Update `CLAUDE.md` (FR-010 / US5-2): add `ruff check .` to "Commands"; align "Testing" with the amended constitution and note the Stop hook will block on a red suite.

**Checkpoint**: US5 done — written rules and gates agree.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T017 Run `/speckit.analyze` (US5-1 spec-path requirement; SC-005) — confirm constitution, `CLAUDE.md` and the gates state the same rule.
- [X] T018 Validate SC-001…SC-006 end-to-end per `quickstart.md` (break-a-guard, undefined-name flag, CI green/red, secret push rejection, consistency, ≤60 s) and mark the tasks `[X]`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup — blocks all user stories.
- **US1 (Phase 3)**: after Foundational.
- **US2 (Phase 4)**: after Foundational; T011 additionally depends on the separate `core/prepare.py` hotfix on `main`.
- **US3/US4 (Phases 5–6)**: after Foundational; independent of US1/US2.
- **US5 (Phase 7)**: after the gates exist (so the docs describe what is actually enforced).
- **Polish (Phase 8)**: after all stories.

### Within Each User Story

- Tests written first (T005, T008), expected to fail, then implementation (T006/T009).
- `stop_tests.py`/`lint_edited.py` are independent files → US1 and US2 are parallelizable once Foundational is done.
- `tests/test_dev_hooks.py` is shared by US1 and US2 — write its two halves in their own phases (sequential, no conflict).

### Parallel Opportunities

- T001 ∥ T003 ∥ T004 (different files: `pyproject.toml` vs `.gitignore` vs `tests/conftest.py`).
- US1 ∥ US2 (distinct hook scripts + distinct test halves), and ∥ US3/US4.
- T012 ∥ T013 within US3 (workflow vs README — different files).
- T015 ∥ T016 within US5 (constitution vs CLAUDE.md — different files).

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 Setup → Phase 2 Foundational (incl. the offline socket fixture).
2. Phase 3 US1 (Stop hook) — **STOP and VALIDATE**: break a guard, confirm the hook blocks once and Claude Code recovers.
3. Then US2 (edit-time lint), US3 (CI), US4 (secrets, manual), US5 (docs).

### Incremental Delivery

Each story is independently verifiable and adds a gate without breaking the previous ones. US4 is a manual GitHub setting with no code; US5 aligns the prose.

---

## Notes

- `[P]` = different files, no dependencies.
- The two hooks are pure-stdlib Python and testable via stdin JSON (FR-004) — never run the real suite recursively in their tests.
- Offline is **enforced**: the autouse socket fixture (T004) and the Stop hook's LLM-key stripping (T006); the 60 s budget is a timed non-blocking warning, not a subset (research R3).
- Confirm hook matcher/tool names and `$CLAUDE_PROJECT_DIR` against `/hooks` before writing `settings.json` (research R1).
- No application-code change in this spec — the `core/prepare.py` F821 is a separate hotfix (research R2).
