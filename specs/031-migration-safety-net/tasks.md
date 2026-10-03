# Tasks: Migration Safety Net (spec 031)

**Input**: `specs/031-migration-safety-net/` — `spec.md` (4 user stories), `plan.md`, `research.md`, `data-model.md`, `contracts/cli.md`, `quickstart.md`.

**Tests**: OPTIONAL here (spec 031 does not mandate TDD), but the high-value logic is covered — SC-003 (byte-identical fingerprint) and SC-004 (compare detects a change) are test-shaped, plus the prune/manifest and health-age rules. They land in a **new** file `tests/test_migration_safety_net.py` (never `tests/test_storage.py`, which is reserved for storage).

**Organization**: tasks grouped by user story. US3 and US4 are independent of US1/US2 and can run in parallel; US2 depends on US1 (restore needs a backup).

**Constitution guardrails (spec 031)**: in-scope = new files under `scripts/` (`backup_db.py`, `fingerprint.py`, `compare_fingerprints.py`, `health_check.py`, `regression_cases.json`), one edit to `scripts/deploy.sh` (FR-005), new docs under `docs/` (`restore-procedure.md`, `migration-checklist.md`), new tests under `tests/`. **NEVER touch** `storage.py`, `models.py`, `profiles.py`, `scrape.py`, `scorer.py`, `main.py`, `llm.py`, `scrapers/`, `tracker_views/`, `tracker.py`, `cv_agent/`, `tracker_legacy.py`, `email_monitor.py`, `migrate_*.py`, or `scripts/sync_live_db.sh` (non-goal — minimum scope, no change).

---

## Phase 1: Setup

**Purpose**: green baseline before any change.

- [x] T001 Confirm working branch `031-migration-safety-net` (off `main`, which now carries Constitution v2.0.0), then run `python -m pytest tests/` and record the green baseline (235 tests). No code changes.

---

## Phase 2: User Story 1 — Consistent backup of the live data (Priority: P1) 🎯 MVP

**Goal**: a consistent online backup of `jobs.db` (+ `cv_agent_checkpoints.sqlite` when present) on verva, verified, with a manifest, pruned to last 5; `deploy.sh` backs up before rebuild and aborts on failure.

**Independent Test** (SC-001): run the backup on a scratch DB while it's open; the produced file passes `PRAGMA integrity_check` and its core table counts match.

### Tests (write first)

- [x] T002 [P] [US1] In `tests/test_migration_safety_net.py`, add tests for `backup_db.py`'s **prune-to-last-5** rule (only touches the backup dir; never deletes a file outside it) and the **manifest JSON shape** (`timestamp`, `git_ref`, `files.jobs`/`files.checkpoints`, `counts` keys). Factor `prune_backups()` and `build_manifest()` as importable pure functions so they're testable.

### Implementation

- [x] T003 [US1] Implement `scripts/backup_db.py` (stdlib `sqlite3`, `json`, `argparse`) per `contracts/cli.md`: `sqlite3.Connection.backup` for `jobs.db` + `cv_agent_checkpoints.sqlite` (skip with a notice when absent — FR-004), `PRAGMA integrity_check` per file (FR-001/003), manifest JSON (FR-003), prune-to-last-5 (FR-006), exit non-zero on any failure. No `import streamlit`.
- [x] T004 [US1] Modify `scripts/deploy.sh` to take a verified backup **before** rebuilding: after `git pull`, `docker cp scripts/backup_db.py job-tracker:/tmp/` then `docker exec job-tracker python /tmp/backup_db.py`, and **abort** (non-zero) if the backup or its integrity check fails (FR-005). Preserve the existing build/verify/seed steps verbatim.
- [x] T005 [US1] Run the T002 tests and a manual backup against a scratch DB (open a second connection to prove it runs while "live"); confirm integrity + counts match (SC-001).

**Checkpoint**: US1 works — a consistent, verified, self-contained backup with manifest + retention exists; deploy.sh is guarded.

---

## Phase 3: User Story 2 — Restore drill (Priority: P1)

**Goal**: a written restore procedure (drill + live) and one executed drill with a measured duration.

**Independent Test** (SC-002): follow the procedure in drill mode → a throwaway tracker serves the restored DB on a different port, live untouched; live-restore duration documented < 15 min.

### Implementation

- [x] T006 [US2] Write `docs/restore-procedure.md` with two modes: **drill** (throwaway container on another port with a separate volume, live untouched) and **live** (stop writers — `agent` cron + `email-monitor` — set the current live DB aside under an explicit timestamped name, swap, restart tracker, verify). Live mode must never delete the replaced DB (FR-010).
- [x] T007 [US2] Execute the drill once on verva (Claude Code SSH access allowed, per spec assumptions) and record the measured duration in the procedure (FR-011, SC-002). Do not touch the live volume.

**Checkpoint**: US2 works — the drill has actually run; the procedure is not hypothetical.

---

## Phase 4: User Story 3 — Parity fingerprint and compare (Priority: P2)

**Goal**: a deterministic, key-sorted JSON fingerprint of app-visible state (computed through `JobStorage` reads on a temp copy, date-pinned) plus a compare command that diffs two fingerprints and exits non-zero on any difference.

**Independent Test** (SC-003/004): fingerprint twice on the same snapshot with the same code + pinned date → byte-identical; break a filter → compare reports it (non-zero).

### Tests (write first)

- [x] T008 [P] [US3] In `tests/test_migration_safety_net.py`, add tests: (a) fingerprint **determinism** — run `fingerprint.py` twice on the same scratch DB + `--as-of` and assert byte-identical output; (b) **compare detects a diff** — `compare_fingerprints.py` on two intentionally-different fingerprints exits non-zero and names the differing section (SC-003/004). Build the scratch DB via `storage.py` against a temp file.

### Implementation

- [x] T009 [P] [US3] Create `scripts/regression_cases.json` (versioned), seeded with the two known cases — FELFEL (title-gate regression, specs 013/028) and the Lausanne job wrongly excluded by `LIKE '%usa%'` (spec 020) — each with `id`, `match` (`title` or `title`+`company`), `expected_presence`, `expected_score_band` (FR-015).
- [x] T010 [US3] Implement `scripts/fingerprint.py` per `contracts/cli.md`: copy the DB to a temp location and open via `JobStorage` (never mutate the source; migrations never alter a backup — FR-012), pin `datetime.date.today` + freshness window to `--as-of` (research R4), compute app-level sections through `JobStorage` public reads (`get_stats`, `get_all_for_tracker`, `get_dashboard_data`, `get_all_applications`, `get_companies`, `get_all_contacts`, `get_last_run` — FR-013), table counts via raw `mode=ro` (FR-014), regression-case presence/score with `"absent"` allowed (FR-015), key-sorted JSON output.
- [x] T011 [US3] Implement `scripts/compare_fingerprints.py` per `contracts/cli.md`: diff two fingerprints section by section, print a readable report, exit non-zero on any difference (FR-016).
- [x] T012 [US3] Run the T008 tests; validate SC-003 (byte-identical) and SC-004 (detect a deliberate filter change) on a scratch snapshot.

**Checkpoint**: US3 works — parity can prove "same data, old vs new code" is a no-op (roadmap step-1 need).

---

## Phase 5: User Story 4 — Definition-of-done check after a deploy (Priority: P3)

**Goal**: a single health-check command (tracker health + last `runs` row) and a written DoD checklist.

**Independent Test**: run the check on verva after a deploy; it reports tracker health + last run status/type/age and exits non-zero on failure or a run older than 26 h.

### Tests (write first)

- [x] T013 [P] [US4] In `tests/test_migration_safety_net.py`, add a unit test for the health-check **age threshold** (26 h) logic — a stale `runs` row flags non-zero, a fresh one passes (factor the age computation as an importable function).

### Implementation

- [x] T014 [US4] Implement `scripts/health_check.py` per `contracts/cli.md`: GET `/_stcore/health` (research R8), read the last `runs` row via `JobStorage.get_last_run()`, report status/`run_type`/age, exit non-zero on health failure or age > 26 h (FR-018). No `import streamlit`.
- [x] T015 [US4] Write `docs/migration-checklist.md` listing the full Principle-XI definition of done: backup taken, nightly cron OK, health check OK, manual smoke test (feed, status change, job detail), parity green (FR-019).

**Checkpoint**: US4 works — post-deploy DoD is a single command + a tickable checklist.

---

## Phase 6: Polish & cross-cutting

- [x] T016 Run `python -m pytest tests/` — full suite green (235 existing + new `test_migration_safety_net.py`).
- [x] T017 Walk `quickstart.md` end-to-end (backup, fingerprint determinism, compare, health check) on dev against a snapshot.
- [x] T018 Validate SC-001–SC-006: backup + integrity on verva, restore drill duration < 15 min, byte-identical fingerprint, compare detects a change, next `deploy.sh` creates a verified backup, live app unchanged.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: none.
- **US1 (P2)**: depends on Setup. BLOCKS US2.
- **US2 (P3)**: depends on US1 (restore needs a backup).
- **US3 (P4)**: depends on Setup only — independent of US1/US2.
- **US4 (P5)**: depends on Setup only — independent.
- **Polish (P6)**: depends on all.

### Parallel opportunities

- US3 (T008–T012) and US4 (T013–T015) can run in parallel with each other and with US1/US2 (different files, no cross-dependencies).
- Within US1: T002 (test) precedes T003; T004 (deploy.sh) depends on T003.

---

## Implementation Strategy

1. **MVP first** = Setup + US1 (backup is the one thing every later roadmap step needs to be able to go back).
2. **Incremental**: US2 (restore drill) → US3 (parity) → US4 (health check) → Polish. US3/US4 may be interleaved.
3. Deploy only after all phases pass and SC-001–SC-006 are validated. No schema change, so a deploy here is low-risk (scripts + docs only).
