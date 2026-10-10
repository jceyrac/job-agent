# Feature Specification: Remove Dead Files (roadmap step 1d)

**Feature Branch**: `035-remove-dead-files`

**Created**: 2026-10-10

**Status**: Clarified (2026-10-10) — ready for `/speckit.plan`

**Input**: Roadmap `docs/roadmap-api.md`, step 1d. "Remove the one-off files left at the repo root after the `core/` move. Delete them rather than archive them: git history keeps them, and an `archive/` folder is dead code that guards must ignore and nobody reads. No behaviour change. Release through the standard flow."

---

## Context (verified against source at `94cd58b`, 2026-10-10)

### The five files

| File | Lines | Last commit touching it | Nature |
|---|---|---|---|
| `migrate_expired_status.py` | 41 | `ba728df` (import rewrite) | One-shot migration, already executed (reclassify archived → expired) |
| `migrate_single_status.py` | 101 | `ba728df` | One-shot migration, already executed (application_status → status) |
| `migrate_profile_independent_tracking.py` | 145 | `ba728df` | One-shot migration, already executed (status/notes → `job_tracking`) |
| `tracker_legacy.py` | 586 | `ba728df` | Original single-page Streamlit tracker, "not in production" (CLAUDE.md) |
| `test_wellfound.py` | 31 | `8715e10` | Ad-hoc live-scrape test at the root (not under `tests/`, not collected by pytest) |

None of them is imported by any module (`grep` for `import migrate_|tracker_legacy|test_wellfound` → no hit). The schema migrations that still matter live inside `core/storage.py` (idempotent, run on open) and are **not** concerned.

Streamlit itself will be fully replaced (decision 2026-10-09), so `tracker_legacy.py` has no remaining purpose.

### References to update

| Where | What |
|---|---|
| `tests/test_installable_project.py:27–34` | Allow-list of the five root one-offs (root-module declaration guard) |
| `CLAUDE.md:105` | NEVER-modify bullet "Migration files (`migrate_*.py`) — one-shot scripts, already executed" |
| `CLAUDE.md:115` | Safe-to-modify bullet "`tracker_legacy.py` — legacy reference, not in production" |
| `docs/roadmap-api.md` | Step 1d row (already describes deletion) |
| `CONTEXT.md` | Removed via FR-005, recorded in `docs/history.md` |
| `prompts/*.md` | Historical specs (declared historical in CLAUDE.md) — **not rewritten** |

---

## Clarifications

### Session 2026-10-10

- Q: FR-005 — `CONTEXT.md`: delete and record in `docs/history.md`, or keep it with a "historical, not maintained" banner? → A: Delete and record in `docs/history.md`.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — A root that only contains what is used (Priority: P1)

As the maintainer, I want the repo root to contain only live entry points (`tracker.py`) and project files, so that anyone (me or Claude Code) reading the tree is not misled by obsolete code.

**Independent Test**: After the change, the root contains no `.py` file other than `tracker.py`; the root-module guard has an empty allow-list and still passes; the full suite is green.

**Acceptance Scenarios**:

1. **Given** the change, **Then** the five files are removed with `git rm` in one commit, and `ls *.py` at the root lists only `tracker.py`.
2. **Given** `tests/test_installable_project.py`, **Then** the one-off allow-list is removed (not left empty "just in case"), and the guard fails if any undeclared `.py` file appears at the root.
3. **Given** `CLAUDE.md`, **Then** the two bullets referencing these files are removed, and nothing else in `CLAUDE.md` points to them.
4. **Given** `docs/history.md` (new, short), **Then** it lists each removed file with one line on what it did and the last commit that contains it (`git show <sha>:<file>` to retrieve it).

### User Story 2 — Released through the standard flow, no behaviour change (Priority: P1)

**Independent Test**: Standard release flow on the exact SHA; parity identical.

**Acceptance Scenarios**:

1. **Given** `staging.sh up <exact SHA>`, **Then** the staging tracker serves the backup copy, health is OK, and parity (candidate vs current prod image, same backup copy, same `--as-of`) is identical. No button-by-button manual check is required for this spec (no runtime code changed), but a quick look at the feed is.
2. **Given** the ff-only merge and deploy, **Then** deployed SHA = staged SHA, health check OK, next scheduled cron run `success`.

---

### Edge Cases

- A removed file still referenced in a doc that Claude Code reads as current (e.g. `CONTEXT.md`) → misleading context; resolved by FR-005 (delete and record in `docs/history.md`).
- `fill_orp_pdf.py` is gitignored personal tooling, not tracked — **not concerned**.
- The Docker image copies the repo (`COPY . .`): removing files only shrinks it; no runtime path references them.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The five files listed in Context MUST be removed with `git rm` in a single commit.
- **FR-002**: The one-off allow-list in `tests/test_installable_project.py` MUST be removed; the root-module guard MUST then allow only `tracker.py` as a root module.
- **FR-003**: `CLAUDE.md` MUST no longer reference the removed files.
- **FR-004**: `docs/history.md` MUST record, for each removed file: name, one-line purpose, last commit containing it, and the retrieval command.
- **FR-005**: `CONTEXT.md` MUST be removed with `git rm` and recorded in `docs/history.md` (its role is now covered by `CLAUDE.md`, the constitution and `docs/roadmap-api.md`).
- **FR-006**: Release MUST follow the standard flow (branch → staging on exact SHA → ff-only merge → deploy → SHA check).

### Non-goals

- No change to runtime code, `core/`, `tracker.py`, `tracker_views/`, the schema or dependencies.
- No rewrite of `prompts/` (historical by declaration).
- No removal of the schema migrations inside `core/storage.py`.
- No `archive/` directory.

---

## Success Criteria *(mandatory)*

- **SC-001**: Root `.py` files = `{tracker.py}`; root-module guard green with no allow-list; guard fails if a stray root `.py` is added (negative fixture).
- **SC-002**: Full suite green.
- **SC-003**: `docs/history.md` lets each removed file be retrieved with one `git show` command (checked for one file).
- **SC-004**: Staging parity identical; deploy SHA = staged SHA; health OK; next scheduled cron `success`.

---

## Assumptions

- Spec 036 (`036-cv-agent-review-loop`) lives on its own branch and does not touch these files.
- Deploy avoids the cron windows (07:00, 11:00, 16:00) and the month-end status batch.
