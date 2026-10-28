#!/usr/bin/env bash
# Staging tracker + API + worker on verva (spec 033 FR-009/010/014;
# spec 041 FR-011; spec 043 US5; spec 045 US4/US5 — the worker/one-shot runs
# exercise the ingest-via-API path with a staging-minted platform token).
#
# Runs a candidate commit as a disposable tracker on port 8502, an API on
# 8602/10000, and a task worker against a copy of the latest backup, without touching
# production (its container, images, the `job_data` volume, or `/opt/job-agent`'s
# working tree).
#
# Usage:
#   scripts/staging.sh up <commit-sha | branch>
#   scripts/staging.sh run <command…>
#   scripts/staging.sh down
#
# See specs/033-installable-project/contracts/staging-cli.md.
#
# Prerequisite (once, on verva): `sudo tailscale set --operator=$(whoami)` — the
# `tailscale serve` calls below are non-root and need the operator role.
set -euo pipefail

REPO="/opt/job-agent"
# `/opt` is root-owned on verva — the deploy user cannot create a sibling of
# `/opt/job-agent` — so the staging worktree lives under $HOME.
WORKTREE="${HOME}/job-agent-staging"
IMAGE="job-agent:staging"
VOLUME="job_agent_staging_data"
CONTAINER="job-agent-staging"
PORT="8502"
API_CONTAINER="job-agent-staging-api"
API_PORT="8602"
# Tailscale HTTPS port for the staging API. Must never collide with prod
# (8443) or nginx (443 — Nextcloud): staging uses 10000.
API_HTTPS_PORT="10000"
WORKER_CONTAINER="job-agent-staging-worker"
# Dedicated bridge so the worker/one-shot `run` containers can reach the staging
# API by its compose-style service name `api` (spec 045 — the ingest client's
# default base URL is `http://api:8601`, the same as prod).
NETWORK="job-agent-staging"
# Path inside the staging volume holding the staging-minted platform ingest
# token (0600). Set on the worker + `run` as JOB_AGENT_API_TOKEN_FILE so the raw
# token is never printed and disappears with the volume at `down`.
INGEST_TOKEN_FILE="/app/data/.staging_ingest_token"
ENV_FILE="/opt/job-agent/.env"

die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARNING: $*" >&2; }

# Refuse to configure a `tailscale serve` entry on port 443 (owned by nginx —
# Nextcloud) or on a port another process already binds. `ss -ltn` lists every
# LISTEN socket; our own staging containers bind loopback only (127.0.0.1 /
# [::1]) and are ignored, so a non-loopback listener is a genuine collision
# (prod, nginx, or a leftover) (Nextcloud incident, 2026-10-24).
assert_serve_port_free() {
  local port="$1"
  [ "$port" != "443" ] || die "port 443 belongs to nginx — tailscale serve must never use 443"
  local listeners
  listeners="$(ss -ltn 2>/dev/null | awk -v p="$port" '
    $4 ~ ("[.:]" p "$") && $4 !~ /^127\.0\.0\.1:/ && $4 !~ /^\[::1\]:/ { print $4 }
  ' || true)"
  if [ -n "$listeners" ]; then
    die "port $port is already bound by another process (ss -ltn: $(echo "$listeners" | tr '\n' ' ')) — refusing to configure a tailscale serve entry on it"
  fi
}

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
  docker build --build-arg GIT_SHA="$sha" -t "$IMAGE" "$WORKTREE"

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

  # 4a. Mint a staging-only platform ingest token on the staging volume (spec 045,
  #     FR-007/US5). `scripts.api_token create` prints the raw token on line 1 and
  #     a summary on line 2; we keep only line 1 and write it (umask 077 → 0600)
  #     into the volume — never echoed to the host. The SHA-256 hash lives in the
  #     staging DB and the raw token in the volume file, both deleted at `down`.
  docker run --rm \
    -e JOB_AGENT_DATA_DIR=/app/data \
    -e JOB_AGENT_REQUIRE_DB=1 \
    -v "$VOLUME":/app/data \
    "$IMAGE" \
    sh -c 'umask 077; T=$(python -m scripts.api_token create --name staging-ingest --scopes ingest --user platform | head -n 1); [ -n "$T" ] || { echo "ERROR: empty ingest token" >&2; exit 1; }; printf "%s" "$T" > '"$INGEST_TOKEN_FILE"

  # 4b. The dedicated bridge the worker/run use to reach the API as `api`.
  docker network create "$NETWORK" >/dev/null

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
  assert_serve_port_free "$PORT"
  tailscale serve --bg "--tcp=${PORT}" "tcp://127.0.0.1:${PORT}"

  # 7. Start the API on the same image/volume/env (spec 041, FR-011). It joins
  #    the staging bridge under the alias `api` so the worker/run containers can
  #    reach it by the compose-style name `http://api:8601` (spec 045). uvicorn
  #    binds 0.0.0.0 inside the container — like the tracker's streamlit — so the
  #    port mapping reaches it; the host publishes loopback-only.
  docker run -d --name "$API_CONTAINER" \
    --network "$NETWORK" --network-alias api \
    -p "127.0.0.1:${API_PORT}:8601" \
    --env-file "$ENV_FILE" \
    -e JOB_AGENT_DATA_DIR=/app/data \
    -e JOB_AGENT_REQUIRE_DB=1 \
    -e GMAIL_APP_PASSWORD= \
    -e NOTIFY_TO= \
    -e JOPLIN_TOKEN= \
    -v "$VOLUME":/app/data \
    "$IMAGE" \
    uvicorn api.main:app --host 0.0.0.0 --port 8601

  # 8. Expose the API on the tailnet under /api (TLS, path-stripped).
  assert_serve_port_free "$API_HTTPS_PORT"
  tailscale serve --bg "--https=${API_HTTPS_PORT}" --set-path /api "http://127.0.0.1:${API_PORT}"

  # 9. Start the worker (task queue consumer) on the same image/volume/env. It
  #    shares /app/data with the tracker and API, so it sees the same queue and
  #    the same `.pipeline.lock` file (spec 043, FR-004). It joins the staging
  #    bridge and runs with ingest_via_api=on + the staging token file, so a
  #    `scrape`/`scrape_monitored` task goes through the API (spec 045, US4).
  docker run -d --name "$WORKER_CONTAINER" \
    --network "$NETWORK" \
    --env-file "$ENV_FILE" \
    -e JOB_AGENT_DATA_DIR=/app/data \
    -e JOB_AGENT_REQUIRE_DB=1 \
    -e JOB_AGENT_API_URL=http://api:8601 \
    -e JOB_AGENT_API_TOKEN= \
    -e JOB_AGENT_API_TOKEN_FILE="$INGEST_TOKEN_FILE" \
    -e INGEST_VIA_API=on \
    -e GMAIL_APP_PASSWORD= \
    -e NOTIFY_TO= \
    -e JOPLIN_TOKEN= \
    -v "$VOLUME":/app/data \
    "$IMAGE" \
    python -m core.worker

  trap - EXIT  # success — keep the staging environment running
  local tip="${TAILSCALE_IP:-$(tailscale ip -4 2>/dev/null || true)}"
  local api_name
  api_name="$(tailscale status --self --peers=false --json 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("Self",{}).get("DNSName","").rstrip("."))' 2>/dev/null || true)"
  echo "Staging tracker up at http://${tip:-<tailnet-ip>}:${PORT}"
  echo "Staging API up at https://${api_name:-<verva>.<tailnet>.ts.net}:${API_HTTPS_PORT}/api"
  echo "Staging worker up (${WORKER_CONTAINER})"
}

run() {
  [ "$#" -ge 1 ] || die "usage: scripts/staging.sh run <command…>"
  docker volume inspect "$VOLUME" >/dev/null 2>&1 || die "staging volume '$VOLUME' not found — run 'scripts/staging.sh up <ref>' first."
  docker network inspect "$NETWORK" >/dev/null 2>&1 || die "staging network '$NETWORK' not found — run 'scripts/staging.sh up <ref>' first."
  [ -f "$ENV_FILE" ] || die "env file $ENV_FILE not found."

  if is_live_command "$@"; then
    warn "this command may hit real sources or consume LLM credits."
  fi

  # Ingest plumbing (spec 045): default to the staging-minted token file and the
  # in-network API base, with ingest_via_api=on. The caller may override
  # JOB_AGENT_API_TOKEN / JOB_AGENT_API_TOKEN_FILE / INGEST_VIA_API in its own
  # environment — e.g. JOB_AGENT_API_TOKEN=invalid to prove the fail-loudly path.
  local api_token="${JOB_AGENT_API_TOKEN-}"
  local api_token_file="${JOB_AGENT_API_TOKEN_FILE:-$INGEST_TOKEN_FILE}"
  local ingest_via_api="${INGEST_VIA_API:-on}"

  docker run --rm \
    --network "$NETWORK" \
    --env-file "$ENV_FILE" \
    -e JOB_AGENT_DATA_DIR=/app/data \
    -e JOB_AGENT_REQUIRE_DB=1 \
    -e JOB_AGENT_API_URL=http://api:8601 \
    -e JOB_AGENT_API_TOKEN="$api_token" \
    -e JOB_AGENT_API_TOKEN_FILE="$api_token_file" \
    -e INGEST_VIA_API="$ingest_via_api" \
    -e GMAIL_APP_PASSWORD= \
    -e NOTIFY_TO= \
    -e JOPLIN_TOKEN= \
    -v "$VOLUME":/app/data \
    "$IMAGE" "$@"
}

down() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$API_CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$WORKER_CONTAINER" >/dev/null 2>&1 || true
  tailscale serve --bg "--tcp=${PORT}" off >/dev/null 2>&1 || true
  tailscale serve --bg "--https=${API_HTTPS_PORT}" off >/dev/null 2>&1 || true
  docker rmi "$IMAGE" >/dev/null 2>&1 || true
  docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  git -C "$REPO" worktree remove "$WORKTREE" --force >/dev/null 2>&1 || true
  git -C "$REPO" worktree prune

  # Verify nothing remains.
  local leftover=0
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "leftover: container $CONTAINER" >&2; leftover=1
  fi
  if docker ps -a --format '{{.Names}}' | grep -qx "$API_CONTAINER"; then
    echo "leftover: container $API_CONTAINER" >&2; leftover=1
  fi
  if docker ps -a --format '{{.Names}}' | grep -qx "$WORKER_CONTAINER"; then
    echo "leftover: container $WORKER_CONTAINER" >&2; leftover=1
  fi
  if docker images --format '{{.Repository}}:{{.Tag}}' | grep -qx "$IMAGE"; then
    echo "leftover: image $IMAGE" >&2; leftover=1
  fi
  if docker volume ls --format '{{.Name}}' | grep -qx "$VOLUME"; then
    echo "leftover: volume $VOLUME" >&2; leftover=1
  fi
  if docker network ls --format '{{.Name}}' | grep -qx "$NETWORK"; then
    echo "leftover: network $NETWORK" >&2; leftover=1
  fi
  if git -C "$REPO" worktree list --porcelain | grep -q "worktree $WORKTREE"; then
    echo "leftover: worktree $WORKTREE" >&2; leftover=1
  fi
  if tailscale serve status 2>/dev/null | grep -q "$PORT"; then
    echo "leftover: tailscale serve entry on $PORT" >&2; leftover=1
  fi
  if tailscale serve status 2>/dev/null | grep -q "$API_HTTPS_PORT"; then
    echo "leftover: tailscale serve entry on $API_HTTPS_PORT" >&2; leftover=1
  fi

  if [ "$leftover" -eq 0 ]; then
    echo "Staging cleaned up — no container, image, volume, network, worktree or tailscale serve entry on $PORT/$API_HTTPS_PORT remains."
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
