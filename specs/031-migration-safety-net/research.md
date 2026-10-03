# Research: Migration Safety Net

Phase 0 decisions. Each entry: decision, rationale, alternatives considered.

## R1 — Consistent backup mechanism

**Decision**: Python `sqlite3.Connection.backup()` (stdlib), never a raw file copy.

**Rationale**: the DB runs in WAL mode, so committed transactions still in `jobs.db-wal` are not
in the main file. A raw copy taken while the tracker or cron writes can be stale or inconsistent.
The online backup API snapshots a consistent, self-contained file (no `-wal`/`-shm` needed) while
the tracker keeps running.

**Alternatives**: `VACUUM INTO` (also valid, but takes a write lock and rewrites the file);
raw file copy (rejected — inconsistent under WAL); stopping the tracker (rejected — must stay
operational, Principle XI).

## R2 — Backup execution on verva

**Decision**: a single `scripts/backup_db.py` (stdlib) run on demand via
`docker exec job-tracker python scripts/backup_db.py`; `deploy.sh` copies the freshly-pulled
script into the running container (`docker cp`) and runs it **before** rebuilding.

**Rationale**: code is baked into the image (not a bind mount), so the running image may predate
the script on the very first deploy. Copying the just-pulled script covers that case: the backup of
the *pre-change* DB still happens even though the old image has no `backup_db.py`.

**Alternatives**: a one-shot `docker run --rm -v job_data:/app/data …` container (works, but more
moving parts than `docker cp` + `docker exec`); bind-mounting `scripts/` into the container
(rejected — changes the compose topology for a diagnostic).

## R3 — Retention (FR-006)

**Decision**: keep the **last 5** backups, pruning only files inside `/app/data/backups/`.

**Rationale**: clarification answer "keep the last 5 backups, no tiers". Pruning is scoped to the
backup directory so the manual `jobs_backup_manual.db` (2026-10-03, at `/app/data/`, outside the
dir) is never touched.

**Alternatives**: daily/weekly tiers (rejected — minimum scope); pruning by age (rejected — count
is simpler and matches the answer).

## R4 — Fingerprint determinism / date pinning

**Decision**: the fingerprint pins `datetime.date.today` (and the freshness window config) to the
`--as-of` date in its own process, and serialises JSON with sorted keys.

**Rationale**: `get_freshness_days()` and any `date.today()`-relative filter make reads depend on
the day they run. Pinning the date removes the time dependency so two runs with the same DB, code
and `--as-of` are byte-identical (SC-003).

**Alternatives**: passing an explicit "as of" through every read (rejected — would require touching
`storage.py`, a non-goal); accepting non-determinism (rejected — breaks SC-003).

## R5 — Fingerprint computes app behaviour through `JobStorage`

**Decision**: feed / scores / dashboard / applications / companies / contacts / lifecycle sections
call the `JobStorage` public read methods the tracker uses (`get_stats`, `get_all_for_tracker`,
`get_dashboard_data`, `get_all_applications`, `get_companies`, `get_all_contacts`,
`get_lifecycle_summary`, `get_last_run`). Only table row counts use raw `mode=ro` (FR-014).

**Rationale**: parity must measure what the app actually shows (FR-013), which is exactly those
read methods — not raw SQL that may diverge from the app layer.

**Alternatives**: fingerprinting raw SQL (rejected — would not catch an app-layer regression).

## R6 — Regression cases source

**Decision**: a single versioned file `scripts/regression_cases.json`, seeded with the two known
cases — FELFEL (title-gate regression) and the Lausanne job wrongly excluded by `LIKE '%usa%'`.

**Rationale**: Constitution VI requires empirical validation against known regressions. One
versioned, diff-able file keeps the list reviewable and lets old snapshots remain usable
(absent case → recorded "absent", not an error — FR-015 / acceptance scenario 4).

**Alternatives**: hardcoding in `fingerprint.py` (rejected — not reviewable as data); a DB table
(rejected — unnecessary state for a static list).

## R7 — Restore drill (US2 / FR-011)

**Decision**: `docs/restore-procedure.md` describes two modes — **drill** (throwaway container on
another port with a separate volume, live untouched) and **live** (stop writers, set the current
live DB aside under an explicit name, swap, restart, verify). The drill is executed once on verva
as part of this spec; its measured duration is recorded in the procedure.

**Rationale**: a backup that has never been restored is not a safety net (Constitution XI).
Live mode never deletes the replaced DB (FR-010).

**Alternatives**: document-only, no drill (rejected — violates FR-011 and SC-002).

## R8 — Health endpoint

**Decision**: use Streamlit's built-in `/_stcore/health` endpoint (returns `ok`).

**Rationale**: FR-018 needs tracker health; the endpoint already exists so no app code change is
needed (non-goal: no UI change).

**Alternatives**: scraping the Jobs page (rejected — brittle); adding an app route (rejected — UI
change).

## R9 — No new dependency

**Decision**: stdlib `sqlite3`, `json`, `argparse`, `datetime`, `urllib.request` (health check)
only.

**Rationale**: constraint "no new dependency"; the existing `python:3.11-slim` image already has
everything.

**Alternatives**: `rich`/`click` for CLI ergonomics (rejected — new dependency, minimum scope).
