#!/bin/bash
# Copy local data/jobs.db into the Docker volume (Mac dev only)
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$PROJECT_DIR/data/jobs.db" ]; then
  echo "No data/jobs.db found — starting with empty DB."
  exit 0
fi

echo "Seeding Docker volume from data/jobs.db..."
docker run --rm \
  -v "${PWD}/data:/src" \
  -v "job_agent_job_data:/data" \
  alpine cp /src/jobs.db /data/jobs.db

echo "Done. Volume now contains:"
docker run --rm -v job_agent_job_data:/data alpine ls -lh /data/
