#!/bin/bash
# Run on Ubuntu server: pulls latest code and restarts tracker
set -e

DEPLOY_DIR="/opt/job-agent"
cd "$DEPLOY_DIR"

echo "=== Pulling latest code ==="
git pull origin main

echo "=== Seeding companies ==="
python seed.py

echo "=== Rebuilding image ==="
docker compose build

echo "=== Restarting tracker ==="
docker compose up -d tracker

echo "=== Deploy complete ==="
docker compose ps
