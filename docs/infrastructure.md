# Infrastructure — network exposure (spec 033, FR-013)

The production tracker has **no authentication**, so it is reachable **only over
the Tailscale network**, never from the LAN or any other interface. The compose
publish binds to loopback; `tailscale serve` forwards the tailnet port to that
loopback port.

## Port map (verva)

| Port | Owner | Type | Notes |
|---|---|---|---|
| 443 | **nginx (Nextcloud)** | HTTPS | `tailscale serve` must **never** use it |
| 8501 | tracker (prod) | TCP | `tailscale serve --tcp=8501` |
| 8443 | API (prod) | HTTPS under `/api` | `tailscale serve --https=8443 --set-path /api` |
| 8502 | tracker (staging) | TCP | `scripts/staging.sh` |
| 10000 | API (staging) | HTTPS under `/api` | `scripts/staging.sh` |

## How it works

- `docker-compose.yml` publishes the tracker loopback-only:
  `"127.0.0.1:8501:8501"` — the port is unreachable from outside the host.
- `tailscale serve` (a persistent tailscaled setting) forwards tailnet TCP 8501
  to `127.0.0.1:8501`. It survives a reboot of verva with no manual step.

## Prerequisite (run once on verva)

`tailscale serve` is a privileged operation. On a fresh install it needs root, so
grant your user the operator role once:

```bash
sudo tailscale set --operator=$(whoami)
```

After this, the non-root `tailscale serve` calls below run without `sudo`. (The
alternative — prefixing each `tailscale serve` with `sudo` — needs a password on
every call and is not suitable for `scripts/staging.sh`.)

## Enable (run once on verva)

```bash
tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501
```

## Verify

```bash
tailscale serve status
# expect a TCP forward for 8501 → tcp://127.0.0.1:8501
```

From a tailnet device, `http://100.74.139.28:8501` (and `/job_detail?id=<id>`)
load; from a non-tailnet LAN device, `http://<verva LAN IP>:8501` is refused.

## Undo

```bash
tailscale serve --bg --tcp=8501 off
```

## Deploy order (FR-013)

To avoid tailnet downtime, apply the switch in this exact order:

1. Deploy the compose change (`"127.0.0.1:8501:8501"`).
2. **Immediately** run `tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501`.
3. Verify with `tailscale serve status` (and the tailnet URL).

The in-container health check (`localhost:8501`) keeps working unchanged
throughout.

---

# API exposure — tailnet HTTPS under `/api` (spec 041, FR-016)

The read-only API (`api/` service) listens on `127.0.0.1:8601` only, and is
served over the tailnet with **TLS under a path** (not a raw TCP forward like
the tracker). Tailscale terminates TLS and proxies
`https://<verva>.<tailnet>.ts.net:8443/api/…` → `http://127.0.0.1:8601/…`, stripping
the `/api` prefix before forwarding. The app sets `root_path="/api"` so generated
URLs (OpenAPI, `/docs`) carry the prefix.

> **Decision amendment (2026-10-24).** The original spec-041 decision used
> `--https=443`. Port 443 belongs to **nginx** on verva (Nextcloud), so the API
> moved to **8443** and staging to **10000** — `tailscale serve` must never use
> 443 (see the port map above).

## Prerequisite (one-time, Tailscale admin console)

`--https` needs **MagicDNS** and **HTTPS certificates** enabled for the tailnet.
Without them `tailscale serve --https` refuses to start (no certificate). MagicDNS
also supplies the stable `https://<verva>.<tailnet>.ts.net` name. (The `sudo
tailscale set --operator=$(whoami)` step above still applies.)

## Enable (run once on verva)

```bash
tailscale serve --bg --https=8443 --set-path /api http://127.0.0.1:8601
```

The tracker keeps its existing `tcp 8501` exposure unchanged.

## Verify

```bash
tailscale serve status
# expect an HTTPS forward for /api → http://127.0.0.1:8601 on 8443
```

From a tailnet device, `https://<verva>.<tailnet>.ts.net:8443/api/health` returns
`200 {"status":"ok","version":"<sha>"}` with a valid certificate (no `-k`).
From a non-tailnet LAN device, `http://<verva LAN IP>:8601` is refused (the
compose publish is loopback-only).

## Configuration

Clients and scripts read the API base URL from `JOB_AGENT_API_URL` (the cv_agent
defaults to the prod URL — FR-016):

- from the tailnet: `https://<verva>.<tailnet>.ts.net:8443/api`
- on the host: `http://127.0.0.1:8601` (the compose publish is loopback-only)
- in-container: `http://api:8601` (the compose service name — the host's
  `127.0.0.1` is **not** the `api` container's loopback)

`scripts/health_check.py --api-url` defaults to `$JOB_AGENT_API_URL`, falling back
to `http://127.0.0.1:8601` for a host-side run. The `tracker` service sets
`JOB_AGENT_API_URL=http://api:8601` in compose, so the post-deploy check (run via
`docker exec job-tracker …`) reaches the API by service name without an explicit
`--api-url`.

## Forwarded headers

Only the loopback proxy (tailscaled on the same host) may set forwarded headers.
The `api` container binds `127.0.0.1:8601`, so the app does **not** enable
`--forwarded-allow-ips=*`.

## Undo

```bash
tailscale serve --bg --https=8443 off
```

---

# Ingestion via API — platform identity (spec 045)

When `ingest_via_api=on`, the `agent` cron run and the `worker`'s scrape tasks
stop writing the catalogue directly and instead POST the collected postings to
`POST /v1/ingest/batch` as the **platform principal** — a non-human `users` row
(`is_service=1`) that owns ingest-only tokens.

- **Token**: created once on the host:
  `python -m scripts.api_token create --name platform-ingest --user platform --scopes ingest`
  (the raw token is printed once, then only its SHA-256 hash is stored). It is
  provided to the `agent` and `worker` containers via `JOB_AGENT_API_TOKEN` in the
  compose `.env` — **never committed** (`.env` is gitignored; `.env.example`
  carries a placeholder only).
- **In-network base**: both containers reach the API at `http://api:8601` (the
  compose service name, not the host's `127.0.0.1`). This is `_IngestClient`'s
  default; `JOB_AGENT_API_URL=http://api:8601` is set on `agent`/`worker` (and
  `tracker`) to make it explicit.
- **Flag**: the `config.ingest_via_api` key is the **single source of truth**
  (default `off`; flip it in the DB with no redeploy — `off` keeps the direct
  path unchanged and is the rollback at every step). `INGEST_VIA_API` (env) is a
  **staging/test bootstrap only**: it is consulted solely when the config key is
  absent, and its use is logged loudly to stderr; an env override set alongside a
  configured key is ignored (and logged). It must not appear in verva's `.env`.

---

# Worker — task queue consumer (spec 043)

The `worker` container runs `python -m core.worker` and consumes the `tasks`
table. It is **not a network service** — it has no published port and nothing in
`tailscale serve` references it — so it is reachable only from the host and the
other containers on the compose network, never over the tailnet.

- Shares the `job_data` volume (reads/writes `jobs.db` directly) and is serialized
  with the cron pipeline (`core.main`) through a shared `flock` (`pipeline_lock`),
  so the worker and cron never run at once.
- `restart: unless-stopped`, json-file log rotation (same as `agent`).
- Writes `config["worker.heartbeat"]` every ~15 s; `scripts/health_check.py`
  reports its age and gates on a stale threshold (spec 043, FR-005).
