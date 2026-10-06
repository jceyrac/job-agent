#!/usr/bin/env bash
# Staging tracker on verva (spec 033, FR-009/010/014).
#
# Runs a candidate commit as a disposable tracker on port 8502 against a copy of
# the latest backup, without touching production (its container, images, the
# `job_data` volume, or `/opt/job-agent`'s working tree).
#
# Usage:
#   scripts/staging.sh up <commit-sha | branch>
#   scripts/staging.sh run <command…>
#   scripts/staging.sh down
#
# See specs/033-installable-project/contracts/staging-cli.md.
set -euo pipefail

REPO="/opt/job-agent"
WORKTREE="/opt/job-agent-staging"
IMAGE="job-agent:staging"
VOLUME="job_agent_staging_data"
CONTAINER="job-agent-staging"
PORT="8502"
ENV_FILE="/opt/job-agent/.env"

die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARNING: $*" >&2; }

# `run` may hit real sources / spend LLM credits; warn for main/scrape/score.
is_live_command() {
  local cmd="${1:-}"
  if [ "$cmd" = "python" ] && [ "${2:-}" = "-m" ]; then
    cmd="${3:-}"
  fi
  case "$cmd" in
    main|scrape|score) return 0 ;;
    *) return 1 ;;
  esac
}

up() {
  [ "$#" -eq 1 ] || die "usage: scripts/staging.sh up <commit-sha | branch>"
  local ref="$1"

  [ -e "$WORKTREE" ] && die "worktree $WORKTREE already exists — run 'scripts/staging.sh down' first."
  docker volume inspect "$VOLUME" >/dev/null 2>&1 && die "staging volume '$VOLUME' already exists — run 'scripts/staging.sh down' first."
  [ -f "$ENV_FILE" ] || die "env file $ENV_FILE not found — cannot start the staging tracker without prod config."

  # Any failure past this point tears down whatever `up` has created so far.
  trap 'echo "ERROR: staging up failed — cleaning up." >&2; down' EXIT

  # 1. Resolve the ref to a commit on origin — never stage the local checkout.
  git -C "$REPO" fetch origin --quiet
  local sha
  if git -C "$REPO" rev-parse --verify "origin/$ref" >/dev/null 2>&1; then
    sha="$(git -C "$REPO" rev-parse "origin/$ref")"
  else
    sha="$(git -C "$REPO" rev-parse --verify "$ref^{commit}" 2>/dev/null)" \
      || die "cannot resolve '$ref' to an origin branch or commit SHA"
  fi
  git -C "$REPO" worktree add --detach "$WORKTREE" "$sha"
  echo "Staging commit $sha"

  # 2. Build the staging image.
  docker build -t "$IMAGE" "$WORKTREE"

  # 3. Locate the latest backup in the live volume (read-only).
  local jobs_backup checkpoints_backup
  jobs_backup="$(docker exec job-tracker sh -c "ls -1 /app/data/backups/jobs_*.db 2>/dev/null | sort | tail -1" || true)"
  [ -n "$jobs_backup" ] || die "no backup found in the live volume — create one (scripts/backup_db.py) before staging."
  checkpoints_backup="$(docker exec job-tracker sh -c "ls -1 /app/data/backups/cv_agent_checkpoints_*.sqlite 2>/dev/null | sort | tail -1" || true)"

  # 4. Create the staging volume and copy the backup into it as jobs.db.
  docker volume create "$VOLUME" >/dev/null
  local tmp
  tmp="$(mktemp -d)"
  docker cp "job-tracker:$jobs_backup" "$tmp/jobs.db"
  if [ -n "$checkpoints_backup" ]; then
    docker cp "job-tracker:$checkpoints_backup" "$tmp/cv_agent_checkpoints.sqlite" 2>/dev/null || true
  fi
  docker run --rm \
    -v "$VOLUME":/app/data \
    -v "$tmp":/backup \
    python:3.11-slim sh -c 'cp /backup/jobs.db /app/data/jobs.db; if [ -f /backup/cv_agent_checkpoints.sqlite ]; then cp /backup/cv_agent_checkpoints.sqlite /app/data/cv_agent_checkpoints.sqlite; fi'
  rm -rf "$tmp"

  # 5. Start the tracker. Streamlit binds 0.0.0.0 inside the container (same as
  #    the prod compose command) so the port mapping reaches it; the host
  #    publishes loopback-only (never 0.0.0.0). Side-effect env vars are blanked
  #    so staging never sends mail or pushes to Joplin.
  docker run -d --name "$CONTAINER" \
    -p "127.0.0.1:${PORT}:8501" \
    --env-file "$ENV_FILE" \
    -e JOB_AGENT_DATA_DIR=/app/data \
    -e JOB_AGENT_REQUIRE_DB=1 \
    -e GMAIL_APP_PASSWORD= \
    -e NOTIFY_TO= \
    -e JOPLIN_TOKEN= \
    -v "$VOLUME":/app/data \
    "$IMAGE" \
    streamlit run tracker.py --server.port 8501 --server.address 0.0.0.0 --server.fileWatcherType none

  # 6. Expose it on the tailnet only.
  tailscale serve --bg "--tcp=${PORT}" "tcp://127.0.0.1:${PORT}"

  trap - EXIT  # success — keep the staging environment running
  local tip="${TAILSCALE_IP:-$(tailscale ip -4 2>/dev/null || true)}"
  echo "Staging tracker up at http://${tip:-<tailnet-ip>}:${PORT}"
}

run() {
  [ "$#" -ge 1 ] || die "usage: scripts/staging.sh run <command…>"
  docker volume inspect "$VOLUME" >/dev/null 2>&1 || die "staging volume '$VOLUME' not found — run 'scripts/staging.sh up <ref>' first."
  [ -f "$ENV_FILE" ] || die "env file $ENV_FILE not found."

  if is_live_command "$@"; then
    warn "this command may hit real sources or consume LLM credits."
  fi

  docker run --rm \
    --env-file "$ENV_FILE" \
    -e JOB_AGENT_DATA_DIR=/app/data \
    -e JOB_AGENT_REQUIRE_DB=1 \
    -e GMAIL_APP_PASSWORD= \
    -e NOTIFY_TO= \
    -e JOPLIN_TOKEN= \
    -v "$VOLUME":/app/data \
    "$IMAGE" "$@"
}

down() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  tailscale serve --bg "--tcp=${PORT}" off >/dev/null 2>&1 || true
  docker rmi "$IMAGE" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  git -C "$REPO" worktree remove "$WORKTREE" --force >/dev/null 2>&1 || true
  git -C "$REPO" worktree prune

  # Verify nothing remains.
  local leftover=0
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "leftover: container $CONTAINER" >&2; leftover=1
  fi
  if docker images --format '{{.Repository}}:{{.Tag}}' | grep -qx "$IMAGE"; then
    echo "leftover: image $IMAGE" >&2; leftover=1
  fi
  if docker volume ls --format '{{.Name}}' | grep -qx "$VOLUME"; then
    echo "leftover: volume $VOLUME" >&2; leftover=1
  fi
  if git -C "$REPO" worktree list --porcelain | grep -q "worktree $WORKTREE"; then
    echo "leftover: worktree $WORKTREE" >&2; leftover=1
  fi
  if tailscale serve status 2>/dev/null | grep -q "$PORT"; then
    echo "leftover: tailscale serve entry on $PORT" >&2; leftover=1
  fi

  if [ "$leftover" -eq 0 ]; then
    echo "Staging cleaned up — no container, image, volume, worktree or tailscale serve entry on $PORT remains."
  else
    echo "ERROR: staging artifacts remain (see above)." >&2
    return 1
  fi
}

case "${1:-}" in
  up)   shift; up "$@" ;;
  run)  shift; run "$@" ;;
  down) down ;;
  *)    echo "usage: scripts/staging.sh {up <ref> | run <command…> | down}" >&2; exit 1 ;;
esac
