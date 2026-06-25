#!/bin/bash
# Run on Ubuntu server: pulls latest code and restarts tracker
set -e

DEPLOY_DIR="/opt/job-agent"
cd "$DEPLOY_DIR"

echo "=== Pulling latest code ==="
git pull origin main

echo "=== Rebuilding image ==="
docker compose build

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
