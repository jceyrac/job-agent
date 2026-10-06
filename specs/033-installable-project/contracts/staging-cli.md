# Contract: `scripts/staging.sh` CLI (US3, FR-009/010)

The staging tool is a Bash script run **on verva** via SSH. It is the only
supported way to exercise a candidate commit before it replaces production.

## Commands

### `up <ref>`

```
scripts/staging.sh up <commit-sha | branch>
```

1. Checkout `ref` into a separate worktree at `/opt/job-agent-staging`
   (`git worktree add`, or fetch + detached checkout of the SHA).
2. Build the image: `docker build -t job-agent:staging /opt/job-agent-staging`.
3. Locate the **latest** backup in the live volume
   (`docker exec job-tracker ls -1 /app/data/backups/jobs_*.db | sort | tail -1`
   and the matching `cv_agent_checkpoints_*.sqlite` if present).
4. Create the staging volume `job_agent_staging_data` and copy the backup into it
   as `jobs.db` / `cv_agent_checkpoints.sqlite`.
5. Start the tracker:
   ```
   docker run -d --name job-agent-staging \
     -p 127.0.0.1:8502:8501 \
     -e JOB_AGENT_DATA_DIR=/app/data -e JOB_AGENT_REQUIRE_DB=1 \
     -v job_agent_staging_data:/app/data \
     job-agent:staging \
     streamlit run tracker.py --server.port 8501 --server.address 127.0.0.1 \
       --server.fileWatcherType none
   ```
6. Configure tailnet access: `tailscale serve --bg --tcp=8502 tcp://127.0.0.1:8502`.

**Preconditions / errors**:
- No backup present → exit non-zero with an explicit message; create nothing.
- `ref` already checked out or the worktree exists → refuse (tell the operator to
  `down` first).

### `run <command…>`

```
scripts/staging.sh run python -m main
scripts/staging.sh run python -m scrape --source linkedin
scripts/staging.sh run python -m scripts.fingerprint …
```

Runs a one-shot container on the staging volume:

```
docker run --rm \
  -e JOB_AGENT_DATA_DIR=/app/data -e JOB_AGENT_REQUIRE_DB=1 \
  -v job_agent_staging_data:/app/data \
  job-agent:staging <command…>
```

Prints a warning when `<command…>` starts with `python -m main`, `scrape` or
`score` (real sources / LLM credits). Fails if the staging container is not up
(no volume).

### `down`

```
scripts/staging.sh down
```

Tears down every staging artifact, in order: `docker rm -f job-agent-staging` →
`tailscale serve --bg --tcp=8502 off` → `docker rmi job-agent:staging` →
`docker volume rm job_agent_staging_data` → `git worktree remove
/opt/job-agent-staging --force`. Leaves no residue (SC-005).

## Invariants (FR-010)

- Never invokes `docker compose` against the production project.
- Never references `job_data`, `job-agent-tracker`, `job-agent-agent`,
  `job-agent-email-monitor`, or writes inside `/opt/job-agent`.
- All staging names are prefixed `job-agent-staging*`.

## Environment

- `TAILSCALE_IP` (optional): the tailnet IP to advertise; defaults to
  `tailscale ip -4` when unset.
