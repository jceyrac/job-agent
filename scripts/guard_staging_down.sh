#!/bin/bash
# Guard for scripts/deploy.sh: refuse to deploy while any job-agent-staging*
# container is running — a deploy would race the staging environment (shared
# tailnet + repo). Container names arrive on stdin (one per line, from
# `docker ps --format '{{.Names}}'`) so the guard is testable without Docker.
if grep -q '^job-agent-staging'; then
  echo "ERROR: staging containers are running — run 'scripts/staging.sh down' first." >&2
  exit 1
fi
