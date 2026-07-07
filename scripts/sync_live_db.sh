#!/bin/bash
# sync_live_db.sh — Pull a read-only copy of the Live DB over Tailscale.
#
# Purpose: test against real production data on Dev without ever developing
# directly against Live. One-way (Live → Dev only). The reverse direction
# must never be attempted this way.
#
# Usage:
#   bash scripts/sync_live_db.sh
#
# After the pull, manually swap in the snapshot:
#   cp data/jobs.db data/jobs_dev_backup.db
#   cp data/jobs_live_snapshot.db data/jobs.db
#
# storage.py migrations are additive/idempotent — opening the snapshot with
# current Dev code is safe.

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────
TAILSCALE_IP="100.74.139.28"
SSH_USER="${VERVA_USER:-root}"
CONTAINER="job-tracker"
REMOTE_DB="/app/data/jobs.db"
LOCAL_SNAPSHOT="data/jobs_live_snapshot.db"

# Confirm Tailscale connectivity
if ! ping -c1 -W2 "${TAILSCALE_IP}" >/dev/null 2>&1; then
    echo "❌  Cannot reach ${TAILSCALE_IP} — is Tailscale connected?"
    exit 1
fi

echo "=== Pulling Live DB from verva (Tailscale) ==="
echo "    Source:  ${SSH_USER}@${TAILSCALE_IP} (container ${CONTAINER}:${REMOTE_DB})"
echo "    Target:  ${LOCAL_SNAPSHOT}"

# Ensure target directory exists
mkdir -p "$(dirname "${LOCAL_SNAPSHOT}")"

# Pull the DB via ssh + docker cp pipe
# docker cp <container>:<path> -  writes to stdout as a tar stream,
# but for a single SQLite file it's functionally a raw copy piped through tar.
ssh "${SSH_USER}@${TAILSCALE_IP}" \
    "docker cp ${CONTAINER}:${REMOTE_DB} - | tar xO" \
    > "${LOCAL_SNAPSHOT}"

# Verify we got a valid SQLite file
if [ ! -s "${LOCAL_SNAPSHOT}" ]; then
    echo "❌  Snapshot is empty — pull failed."
    exit 1
fi

if ! sqlite3 "${LOCAL_SNAPSHOT}" "SELECT COUNT(*) FROM jobs;" >/dev/null 2>&1; then
    echo "❌  Snapshot is not a valid SQLite DB — pull may be corrupted."
    exit 1
fi

JOB_COUNT=$(sqlite3 "${LOCAL_SNAPSHOT}" "SELECT COUNT(*) FROM jobs;")
FILE_SIZE=$(du -h "${LOCAL_SNAPSHOT}" | cut -f1)
echo "✅  Snapshot pulled: ${JOB_COUNT} jobs, ${FILE_SIZE}"

# ── Swap-in instructions ──────────────────────────────────────────────────
echo ""
echo "=== Manual swap-in steps ==="
echo ""
echo "  # 1. Back up your current Dev DB:"
echo "  cp data/jobs.db data/jobs_dev_backup.db"
echo ""
echo "  # 2. Swap in the Live snapshot:"
echo "  cp ${LOCAL_SNAPSHOT} data/jobs.db"
echo ""
echo "  # 3. Launch tracker locally:"
echo "  streamlit run tracker.py"
echo ""
echo "  # 4. When done testing, restore your Dev DB:"
echo "  cp data/jobs_dev_backup.db data/jobs.db"
echo ""
echo "⚠️   This is ONE-WAY (Live → Dev only). Never push Dev data to Live."
echo "    storage.py migrations are additive/idempotent — safe to open."
