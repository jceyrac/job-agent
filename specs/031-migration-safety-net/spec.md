# Feature Specification: Migration Safety Net (backup, restore drill, parity check)

**Feature Branch**: `031-migration-safety-net`

**Created**: 2026-10-03

**Status**: Ready for plan — clarified 2026-10-03.

**Input**: Roadmap `docs/roadmap-api.md`, step 0. "Before any migration step touches code or schema, I need a safety net: a verified way to back up and restore the live DB, and a parity script that proves the app behaves the same before and after a change. The app must stay operational at every step because I use it daily for my job search." (Constitution v2.0.0, Principle XI.)

---

## Clarifications

### Session 2026-10-03

Guiding answer from Jean Claude: the data is not highly critical — **do the minimum**.

- Q: Retention (FR-006)? → A: **Keep the last 5 backups**, no daily/weekly tiers.
- Q: Backup location (FR-007)? → A: **Local only on verva**, in a dedicated directory of the `job_data` volume. No off-machine copy, no Proton Drive. Accepted risk: no protection against disk loss; backups protect against migration/deploy errors only.
- Q: Scheduled nightly backups (FR-008)? → A: **No.** Backups are taken by `deploy.sh` and on demand only.
- Note: a manual consistent backup (`sqlite3.Connection.backup`) was taken by Jean Claude on 2026-10-03 at `/app/data/jobs_backup_manual.db`. The implementation MUST NOT delete it (it is outside the pruning scope unless moved into the backup directory by hand).

---

## Context (verified against source, 2026-10-03)

- **Constitution amendment already done.** v2.0.0 (user_id seam, shared catalogue, migration principle XI) was written on 2026-10-03; it is no longer part of this step's scope.
- **No automated backup of `jobs.db` exists in the repo.** The live DB lives in the Docker named volume `job_data`, mounted at `/app/data/` in container `job-tracker` (`docker-compose.yml`). No script in `scripts/` creates a backup. verva's known nightly backup (rclone + systemd, 02:03 UTC) syncs **Nextcloud Documents** to Proton Drive — not the `job_data` volume (to be confirmed, see FR-007).
- **`scripts/sync_live_db.sh` is not a backup.** It pulls a one-way snapshot Live → Mac with `docker cp job-tracker:/app/data/jobs.db - | tar xO`, which copies only the main DB file. The DB runs in **WAL mode**: committed transactions still in `jobs.db-wal` are not in the copy. A file copy taken while the tracker or the cron is writing can be stale or inconsistent.
- **Other state in the same volume.** `data/` also holds `cv_agent_checkpoints.sqlite` (LangGraph `SqliteSaver`, spec 029) — in-progress CV sessions. It needs the same protection.
- **Size.** `jobs.db` is 160+ MB.
- **Known regression cases.** FELFEL (`tests/test_title_gate.py`, specs 013/028) and the Lausanne job wrongly excluded by `LIKE '%usa%'` (spec 020).
- **Public read paths used by the UI.** `JobStorage.get_stats(profile_id)`, `get_all_for_tracker(profile_id)`, `get_dashboard_data(...)`, `get_all_applications()`, `get_companies(...)`, `get_all_contacts()`, `get_lifecycle_summary(...)`, `get_last_run()`. These are what "the app behaves the same" means in practice.
- **Time-dependent reads.** The freshness window (`get_freshness_days()`, spec 028) and any `date.today()`-relative filter make reads depend on the day they run.
- **Deploy.** `scripts/deploy.sh` (on verva, `/opt/job-agent`) pulls, rebuilds all images and restarts — it takes no backup today.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Consistent backup of the live data (Priority: P1)

Before any risky change, I can take a consistent backup of the live DB (and the CV agent checkpoint DB) on verva while the tracker keeps running, and I know it is valid.

**Why this priority**: Every later roadmap step (the schema migration of step 3 especially) relies on being able to go back. Without it, nothing else in the roadmap is safe.

**Independent Test**: Run the backup command on verva while the tracker is up; the produced file passes an integrity check and its row counts match the live DB at backup time.

**Acceptance Scenarios**:

1. **Given** the tracker is running and the DB is in WAL mode, **When** I run the backup, **Then** a single self-contained file is produced (no `-wal`/`-shm` needed) that passes `PRAGMA integrity_check` = `ok`.
2. **Given** a backup just completed, **When** I compare row counts of `jobs`, `job_scores`, `job_tracking`, `interactions`, `contacts`, `companies` between the backup and the live DB, **Then** they are identical (absent writes in between).
3. **Given** `scripts/deploy.sh` runs, **When** it starts, **Then** it takes a backup first and **aborts the deploy** if the backup or its integrity check fails.
4. **Given** backups accumulate, **When** a new one is taken, **Then** older ones are pruned according to the retention rule.

---

### User Story 2 — Restore drill (Priority: P1)

I have actually restored a backup once, end to end, and I have a written procedure with a known duration — so restoring under stress is a routine, not an improvisation.

**Why this priority**: A backup that has never been restored is not a safety net (Constitution XI).

**Independent Test**: Follow the documented procedure to restore a backup into a **separate** tracker instance (not the live one), open it, and check the feed shows the expected jobs.

**Acceptance Scenarios**:

1. **Given** a backup file, **When** I follow `docs/restore-procedure.md` in drill mode, **Then** a throwaway tracker container serves the restored DB on a different port without touching the live volume.
2. **Given** the drill tracker is up, **When** I open the Jobs page, **Then** the feed count and the presence of the regression jobs match the parity fingerprint of the backup.
3. **Given** a real incident, **When** I follow the procedure in live mode, **Then** the live DB is swapped for the backup, the previous live DB is kept aside (not deleted), and the tracker restarts — within the target duration.

---

### User Story 3 — Parity fingerprint and compare (Priority: P2)

I can capture a deterministic fingerprint of what the app shows from a given DB, and compare two fingerprints, so I can prove a code change did not alter behaviour.

**Why this priority**: Needed from roadmap step 1 onward (the monorepo move must be a no-op). Backup/restore come first because they protect data; parity protects behaviour.

**Independent Test**: Run the fingerprint twice on the same DB snapshot with the same code and the same pinned date → zero diff. Then deliberately break a filter on a branch → the compare reports it.

**Acceptance Scenarios**:

1. **Given** a DB snapshot and a pinned "as of" date, **When** I run the fingerprint twice with the same code, **Then** the outputs are byte-identical.
2. **Given** the same snapshot, **When** I run the fingerprint with the code before and after a change (two git refs), **Then** the compare lists every difference by section (counts, feed, scores, regression cases) and exits non-zero if any exist.
3. **Given** the fingerprint, **Then** it includes at least: row counts per table; tracker feed size per status and per score band for the active profile; score distribution; dashboard figures; for each regression case, presence in the feed and its score.
4. **Given** the regression cases list, **When** a job is not found in the snapshot, **Then** the fingerprint records it as "absent" (not an error), so old snapshots remain usable.

---

### User Story 4 — Definition-of-done check after a deploy (Priority: P3)

After each roadmap deploy, a single command tells me whether the live app is healthy: tracker responds, last pipeline run succeeded.

**Why this priority**: Formalises the definition of done of Principle XI; useful, but each item can be checked by hand meanwhile.

**Independent Test**: Run the check on verva the morning after a deploy; it reports tracker health, the last `runs` row status and age, and exits non-zero on any failure.

**Acceptance Scenarios**:

1. **Given** the tracker container is up, **When** the check runs, **Then** it reports the Streamlit health endpoint as OK.
2. **Given** the nightly cron ran, **When** the check runs, **Then** it reports the last run's status, type and age, and flags a failure or a run older than 26 h.
3. **Given** the check passes, **Then** the remaining manual smoke test (feed, status change, job detail) is listed as a short checklist in `docs/migration-checklist.md`.

---

### Edge Cases

- Backup taken while the nightly cron is writing → must still be consistent (online backup API, not a file copy).
- Disk space on verva too low for a 160+ MB backup (plus retention) → backup fails loudly before writing, deploy aborts.
- `cv_agent_checkpoints.sqlite` does not exist on verva yet → skipped with a notice, not an error.
- Fingerprint run on a day where the freshness window shifts → avoided by the pinned "as of" date.
- Fingerprint on a snapshot older than the current schema → `storage.py` migrations are additive/idempotent and run on open; the fingerprint MUST run on a **copy**, never on the backup file itself, so migrations never alter a backup.
- A regression job purged by `purge_stale_jobs` → reported "absent".
- Restore in live mode while the cron is running → the procedure stops (or waits for) the `agent` run before swapping.

---

## Requirements *(mandatory)*

### Functional Requirements

**Backup**

- **FR-001**: Backups MUST use SQLite's online backup mechanism (Python `sqlite3.Connection.backup` or `VACUUM INTO`), never a raw file copy, so WAL content is included and the result is self-contained.
- **FR-002**: The backup MUST run against the `job_data` volume on verva (e.g. `docker exec` or a one-shot container on the volume) without stopping the tracker.
- **FR-003**: Each backup MUST be verified — `PRAGMA integrity_check` = `ok` — and accompanied by a small JSON manifest: timestamp, git ref, file sizes, integrity result, core table counts.
- **FR-004**: The backup MUST include `cv_agent_checkpoints.sqlite` when present.
- **FR-005**: `scripts/deploy.sh` MUST take a verified backup before rebuilding, and MUST abort the deploy if it fails.
- **FR-006**: Backups MUST be pruned to keep the **last 5**; pruning only touches files in the backup directory.
- **FR-007**: Backups MUST be stored **locally on verva**, in a dedicated directory of the `job_data` volume (e.g. `/app/data/backups/`). No off-machine copy.
- **FR-008**: Backups are taken by `deploy.sh` and **on demand** only — no scheduled backup.

**Restore**

- **FR-009**: `docs/restore-procedure.md` MUST describe two modes: **drill** (restore into a throwaway container/volume on another port, live untouched) and **live** (stop writers, set the current live DB aside, swap, restart, verify).
- **FR-010**: Live mode MUST never delete the replaced DB; it is kept next to the backups under an explicit name.
- **FR-011**: The drill MUST be performed once on verva as part of this spec, and its measured duration recorded in the procedure.

**Parity**

- **FR-012**: A fingerprint command MUST take a DB path and an "as of" date, work on a **temporary copy** of the DB, and write a deterministic, key-sorted JSON fingerprint.
- **FR-013**: Application-level sections of the fingerprint MUST be computed through `JobStorage` public methods (the reads the tracker uses), so parity measures app behaviour, not raw SQL.
- **FR-014**: Table row counts MAY use a read-only connection (`mode=ro`) from the script, as an explicit diagnostic exception to the "DB access via JobStorage" constraint, justified in the plan's Complexity Tracking.
- **FR-015**: Regression cases MUST be declared in one versioned file (job identifier or title+company pattern, expected presence, expected score band), starting with FELFEL and the Lausanne case.
- **FR-016**: A compare command MUST diff two fingerprints by section, print a readable report and exit non-zero on any difference.
- **FR-017**: A convenience mode SHOULD run the fingerprint for two git refs on the same snapshot (e.g. via `git worktree`), since "same data, old vs new code" is the core parity use.

**Post-deploy check**

- **FR-018**: A health-check command MUST report tracker health (Streamlit health endpoint) and the last `runs` row (status, type, age), and exit non-zero on failure or a run older than 26 h.
- **FR-019**: `docs/migration-checklist.md` MUST list the full definition of done of Principle XI (backup taken, nightly cron OK, health check OK, manual smoke test — feed, status change, job detail — and parity green), to be ticked for every roadmap step.

### Non-goals

- No change to the DB schema, `storage.py`, `models.py`, or any scraper.
- No change to the tracker UI (no mockup required).
- No monorepo restructuring (step 1), no API (step 5+).
- No change to `sync_live_db.sh` behaviour, beyond optionally using the new consistent backup as its source (decide at plan time).
- No automation of the manual smoke test (no UI testing framework) — checklist only.
- No modification of verva's existing Nextcloud/Proton Drive backup service itself.
- No off-machine copy, no scheduled backups, no compression or encryption of backups (minimum scope, see Clarifications).

### Key Entities

- **Backup**: a self-contained SQLite file (plus optional checkpoint DB) with its manifest.
- **Fingerprint**: a deterministic JSON description of what the app exposes from a DB at an "as of" date.
- **Regression case**: a known job (identifier or pattern) with its expected presence and score band.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A backup of the live DB is produced with the tracker running, passes integrity check, and its counts match live.
- **SC-002**: The restore drill has been executed once on verva; the documented live-restore duration is under 15 minutes.
- **SC-003**: Two fingerprints of the same snapshot with the same code and date are byte-identical.
- **SC-004**: A deliberately introduced filter change on a test branch is detected by the compare (non-zero exit, difference shown in the feed section).
- **SC-005**: The next `deploy.sh` run after this spec creates a verified backup before rebuilding.
- **SC-006**: The live app shows no behaviour change after this spec is deployed (health check OK, nightly cron OK).

---

## Assumptions

- verva has enough free disk for 5 backups (~160 MB each, < 1 GB total).
- Python's `sqlite3` module in the existing image is sufficient (no new dependency).
- Scripts are developed on the Mac (dev/live separation); Claude Code may use its SSH access to verva for the drill and diagnostics.
- Fingerprints use the active (unified) profile by default.
- Deploys for this spec avoid the month-end status batch.
