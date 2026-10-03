# Restore procedure — job_agent (verva)

Spec 031, FR-009..011. Restore a backup of `jobs.db` (and, when present,
`cv_agent_checkpoints.sqlite`). Two modes:

- **Drill** — restore into a throwaway container on another port with a separate
  volume. The live tracker is never touched. Run this as practice and after any
  change to this procedure.
- **Live** — the real incident path: stop writers, set the current DB aside,
  swap in the backup, restart. The replaced DB is **never deleted** (FR-010).

Backups live in `/app/data/backups/` on the `job_data` volume, named
`jobs_<YYYYMMDD_HHMMSS>.db`, `cv_agent_checkpoints_<YYYYMMDD_HHMMSS>.sqlite`
(optional) and `manifest_<YYYYMMDD_HHMMSS>.json`. The newest manifest's stem is
the newest backup.

---

## Prerequisites

- SSH access to verva; repo at `/opt/job-agent`.
- Docker access on verva (`docker ps` works).
- A backup file (see "pick a backup" below).

## Pick a backup

List the manifests (newest last):

```bash
docker exec job-tracker ls -1 /app/data/backups/manifest_*.json
```

The stem is the part between `manifest_` and `.json` (e.g. `20261003_151552`).
To confirm a backup is valid before restoring, read its manifest:

```bash
docker exec job-tracker cat /app/data/backups/manifest_<STEM>.json
```

The manifest records `git_ref`, `files.jobs.integrity` (must be `ok`) and the
`counts` you can cross-check against the live DB after the swap.

---

## Drill mode (safe — run this regularly)

Targets: a throwaway tracker on port **8502** serving a copy of the backup, live
tracker on 8501 untouched.

```bash
# 0. Clean any leftover drill state
docker rm -f job-tracker-drill 2>/dev/null || true
rm -rf /tmp/job-drill && mkdir -p /tmp/job-drill

# 1. Copy the chosen backup out of the live volume into a scratch dir
docker cp job-tracker:/app/data/backups/jobs_<STEM>.db /tmp/job-drill/jobs.db
docker cp job-tracker:/app/data/backups/cv_agent_checkpoints_<STEM>.sqlite /tmp/job-drill/ 2>/dev/null || true

# 2. Start the throwaway tracker (separate volume = /tmp/job-drill, port 8502)
docker run -d --rm --name job-tracker-drill \
  -p 8502:8501 \
  --env-file /opt/job-agent/.env \
  -v /tmp/job-drill:/app/data \
  job-agent-tracker \
  streamlit run tracker.py --server.port 8501 --server.address 0.0.0.0 --server.fileWatcherType none

# 3. Wait for it to come up (health endpoint), then confirm the restored DB
timeout 60 bash -c 'until curl -sf localhost:8502/_stcore/health; do sleep 1; done'
docker exec job-tracker-drill python -c \
  "import sqlite3;print('jobs:',sqlite3.connect('/app/data/jobs.db').execute('SELECT COUNT(*) FROM jobs').fetchone()[0])"

# 4. Open http://<verva>:8502 in a browser and check the Jobs page feed.

# 5. Tear down (live is untouched)
docker rm -f job-tracker-drill
rm -rf /tmp/job-drill

# 6. Confirm the live tracker is still healthy
curl -sf localhost:8501/_stcore/health
```

**Duration (measured 2026-10-03):** **6 s** (cold start of the throwaway
container to health-OK; restored DB served 3503 jobs, live untouched).

> The drill above was performed with the 2026-10-03 manual backup
> (`/app/data/jobs_backup_manual.db`) because `/app/data/backups/` did not yet
> exist. Once the backup script has produced backups there, use one of those
> `jobs_<STEM>.db` files instead — the restore steps are identical.

---

## Live mode (incident — swap the live DB)

Stop writers, set the current DB aside, swap, restart. The replaced DB is kept
under an explicit timestamped name next to the backups (never deleted).

```bash
cd /opt/job-agent

# 1. Stop writers: comment the cron line that runs the agent, then confirm
#    nothing is still running. (email-monitor is not used in normal operation.)
#    In crontab:  # docker compose run --rm agent
docker ps --format '{{.Names}}' | grep -E '^job-agent$|^job-email-monitor$' && \
  echo "WARNING: a writer is still running — stop it first" || true

# 2. Stop the tracker
docker compose stop tracker

# 3. Set the current live DB aside under a timestamped name
STAMP=$(date +%Y%m%d_%H%M%S)
docker run --rm -v job_data:/app/data python:3.11-slim sh -c \
  "cd /app/data \
   && mv jobs.db jobs.db.pre_$STAMP \
   && mv jobs.db-wal jobs.db-wal.pre_$STAMP 2>/dev/null || true \
   && mv jobs.db-shm jobs.db-shm.pre_$STAMP 2>/dev/null || true \
   && mv cv_agent_checkpoints.sqlite cv_agent_checkpoints.sqlite.pre_$STAMP 2>/dev/null || true"

# 4. Copy the chosen backup into place as the live DB
docker run --rm -v job_data:/app/data python:3.11-slim sh -c \
  "cd /app/data \
   && cp backups/jobs_<STEM>.db jobs.db \
   && (cp backups/cv_agent_checkpoints_<STEM>.sqlite cv_agent_checkpoints.sqlite 2>/dev/null || true)"

# 5. Restart the tracker
docker compose up -d tracker

# 6. Verify
curl -sf localhost:8501/_stcore/health
docker exec job-tracker python -c \
  "import sqlite3;print('jobs:',sqlite3.connect('/app/data/jobs.db').execute('SELECT COUNT(*) FROM jobs').fetchone()[0])"

# 7. Re-enable the writer cron (uncomment the agent line)
```

If the restored DB is wrong, the previous DB is still at
`/app/data/jobs.db.pre_$STAMP` — swap it back with the same steps in reverse.

---

## Verification checklist

- [ ] `/_stcore/health` returns `ok` on the target port.
- [ ] Jobs page loads and shows a non-empty feed.
- [ ] Row counts match the backup manifest's `counts` (`jobs`, `job_scores`, `job_tracking`).
- [ ] (Live only) the writer cron is re-enabled.
