# Research: Remove Dead Files (spec 035)

No `NEEDS CLARIFICATION` remains after the spec's Context section (verified against
source at `94cd58b`). This file records the three decisions the change rests on.

## Decision 1 — The six files are safe to delete

**Decision**: Remove all six with `git rm`; nothing imports them.

**Rationale**: `grep` for `import migrate_|tracker_legacy|test_wellfound` across the tree
returns no hit. The three `migrate_*.py` are one-shot scripts already executed against the
live DB; the schema migrations that still matter live inside `core/storage.py` (idempotent,
run on open) and are out of scope. `tracker_legacy.py` is declared "not in production" in
`CLAUDE.md`, and Streamlit is slated for full replacement (decision 2026-10-09).
`test_wellfound.py` sits at the root (not under `tests/`, never collected by pytest) and is
an ad-hoc live-scrape test.

## Decision 2 — `git rm`, not an `archive/` directory

**Decision**: Delete outright; no `archive/` folder.

**Rationale**: Git history already preserves each file; an `archive/` folder is dead code
that the root-module guard would have to ignore and nobody reads. `docs/history.md` records
the last commit containing each file so it stays retrievable via `git show <sha>:<path>`.

## Decision 3 — `CONTEXT.md` is deleted (FR-005)

**Decision**: Delete and record in `docs/history.md`.

**Rationale**: Its current role is covered by `CLAUDE.md`, the constitution and
`docs/roadmap-api.md`; a stale reference document still describing Groq and the pre-`core/`
tree actively misleads agents that read it as current. Clarified 2026-10-10.
