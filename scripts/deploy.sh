#!/bin/bash
# Run on Ubuntu server: pulls latest code and restarts tracker
set -e

DEPLOY_DIR="/opt/job-agent"
cd "$DEPLOY_DIR"

echo "=== Pulling latest code ==="
git pull origin main

# Re-exec once so changes to this very script take effect on the same deploy
# (the pull above may have just replaced deploy.sh). Guarded so we only do it once.
if [ "${DEPLOY_REEXEC:-0}" != "1" ]; then
    export DEPLOY_REEXEC=1
    exec bash "$0" "$@"
fi

echo "=== Backing up live DB (pre-deploy safety net) ==="
# Copy the freshly-pulled stdlib backup script into the running container (the
# running image may predate this script on the first deploy) and take a verified
# backup BEFORE rebuilding. Abort the deploy if the backup fails (FR-005).
GIT_REF=$(git rev-parse --short HEAD)
docker cp scripts/backup_db.py job-tracker:/tmp/backup_db.py
if ! docker exec job-tracker python /tmp/backup_db.py \
      --db /app/data/jobs.db \
      --checkpoints /app/data/cv_agent_checkpoints.sqlite \
      --backup-dir /app/data/backups \
      --ref "$GIT_REF"; then
    echo "ERROR: pre-deploy backup failed — aborting deploy."
    exit 1
fi

echo "=== Rebuilding images (all services) ==="
# Build every service by name — not by profile.
# Profiles (e.g. "manual" on agent/email-monitor) bypass `docker compose build`
# with no args, so listing names explicitly guarantees nothing is skipped.
docker compose build tracker agent email-monitor

echo "=== Verifying images are current ==="
# Check that the agent image was built within the last 5 minutes (this run).
# If it's older, something went wrong — abort before restarting.
AGENT_CREATED=$(docker image inspect job-agent-agent --format '{{.Created}}' 2>/dev/null)
if [ -z "$AGENT_CREATED" ]; then
    echo "ERROR: job-agent-agent image not found after build!"
    exit 1
fi
echo "  agent image built: $AGENT_CREATED"
TRACKER_CREATED=$(docker image inspect job-agent-tracker --format '{{.Created}}' 2>/dev/null)
echo "  tracker image built: $TRACKER_CREATED"

echo "=== Restarting tracker ==="
docker compose up -d tracker

echo "=== Seeding companies ==="
# seed.py must run INSIDE the container because the DB lives in a Docker volume
# (.dockerignore excludes data/ and the volume overlays /app/data, so
#  running seed.py on the host writes to a different DB than the tracker serves)
docker cp data/companies.json job-tracker:/app/data/companies.json
docker exec job-tracker python seed.py

echo "=== Deploy complete ==="
docker compose ps
