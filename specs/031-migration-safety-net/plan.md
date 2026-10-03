# Implementation Plan: Migration Safety Net

**Branch**: `031-migration-safety-net` | **Date**: 2026-10-03 | **Spec**: `specs/031-migration-safety-net/spec.md`

**Input**: Feature specification from `specs/031-migration-safety-net/spec.md`

## Summary

Add the roadmap step-0 safety net before any schema migration: a consistent **online backup**
of `jobs.db` + `cv_agent_checkpoints.sqlite` on verva (stdlib `sqlite3.Connection.backup`), an
**executed restore drill** with a written procedure, a deterministic **parity fingerprint**
(computed through `JobStorage` reads) with a compare command, and a **post-deploy health check**.

Everything is additive scripts + docs. No schema change, no `storage.py`/`models.py`/scraper
change, no UI change, no new dependency. The live app stays operational throughout.

> **Sequencing note**: this spec's rationale is Constitution v2.0.0 Principle XI, which currently
> lives on the `api-creation` branch (commit `d3385b5`, not yet on `main`). This branch is based on
> `main`; merge `api-creation` → `main` before (or rebase this branch onto) implementation so the
> constitution referenced by the Constitution Check is present.

## Technical Context

**Language/Version**: Python 3.11 (stdlib `sqlite3`, `json`, `argparse`, `datetime`, `urllib`, `hashlib`).

**Primary Dependencies**: none new — stdlib only; runs in the existing `job-agent-tracker` image
(`python:3.11-slim`). Streamlit's built-in `/_stcore/health` endpoint for the health check.

**Storage**: SQLite in WAL mode, Docker named volume `job_data` mounted at `/app/data/`
(`jobs.db`, `cv_agent_checkpoints.sqlite`). Backups stored in `/app/data/backups/` (local only).

**Testing**: `tests/test_storage.py` (unchanged, 235 tests); new scripts get cheap smoke/unit
coverage where high-value (fingerprint determinism, manifest shape, prune rule). The restore drill
is executed manually on verva once (FR-011).

**Target Platform**: Ubuntu server (verva), Docker Compose; scripts also run on macOS dev against
a snapshot.

**Project Type**: CLI scripts + docs (no UI change).

**Performance Goals**: backup of the 160+ MB DB in seconds via the online backup API; no tracker
downtime; fingerprint runs on a temporary copy (never on a backup file).

**Constraints**: no new dependency; no raw `sqlite3` outside `storage.py` except the FR-014
diagnostic exception; backups local-only; keep last 5.

**Scale/Scope**: ~5 backups × ~165 MB < 1 GB; one active profile; single node.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **II. Two improvement paths** — PASS: pure code path (scripts), shipped via git + `scripts/deploy.sh`.
- **V. Surgical change** — PASS: additive scripts + docs only; no refactor of existing code.
- **VI. Empirical validation** — PASS: parity fingerprint + regression cases (FELFEL, Lausanne) enforce this.
- **VII. Security** — PASS: local-only backups, no secrets, no network egress, no off-machine copy.
- **IX. Scoring optional** — PASS: fingerprint reads are read-only; the app stays usable.
- **XI. Migration progressive, always operational** — PASS: this spec *is* the safety net (backup + restore drill + parity + DoD check).
- **Architecture: "DB access via JobStorage"** — PARTIAL: FR-013 mandates app-level fingerprint sections through `JobStorage`; FR-014 allows raw read-only SQL for table counts → justified in Complexity Tracking.
- **Architecture: no new dependency** — PASS: stdlib only.

## Project Structure

### Documentation (this feature)

```text
specs/031-migration-safety-net/
├── plan.md              # this file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── cli.md           # CLI + fingerprint JSON schema
└── tasks.md             # /speckit-tasks (not created by this command)
```

### Source Code (repository root)

```text
scripts/
├── backup_db.py            # consistent backup + manifest + prune (FR-001..008)
├── fingerprint.py          # deterministic parity fingerprint (FR-012..015)
├── compare_fingerprints.py # diff two fingerprints (FR-016)
├── health_check.py         # post-deploy DoD check (FR-018)
├── regression_cases.json   # versioned known-regression list (FR-015)
└── deploy.sh               # (modified) backup before rebuild, abort on failure (FR-005)

docs/
├── restore-procedure.md    # drill + live restore (FR-009..011)
└── migration-checklist.md  # DoD checklist (FR-019)
```

**Structure Decision**: keep scripts in the existing flat `scripts/` dir (stdlib, one file each);
new docs in `docs/`. No package, no new directory layout — consistent with the existing repo.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| FR-014: raw `sqlite3` (`mode=ro`) for table row counts | A per-table row count is a diagnostic invariant across *every* table; there is no `JobStorage` method exposing all of them, and enumerating them through app APIs is impossible without a schema/storage change | Adding a `get_table_counts()` method to `storage.py` is a storage change (explicit non-goal) and would couple the diagnostic to the app layer; a read-only count is observation, not app behaviour |
