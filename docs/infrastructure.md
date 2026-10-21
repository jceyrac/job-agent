# Infrastructure — network exposure (spec 033, FR-013)

The production tracker has **no authentication**, so it is reachable **only over
the Tailscale network**, never from the LAN or any other interface. The compose
publish binds to loopback; `tailscale serve` forwards the tailnet port to that
loopback port.

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
`https://<verva>.<tailnet>.ts.net/api/…` → `http://127.0.0.1:8601/…`, stripping
the `/api` prefix before forwarding. The app sets `root_path="/api"` so generated
URLs (OpenAPI, `/docs`) carry the prefix.

## Prerequisite (one-time, Tailscale admin console)

`--https` needs **MagicDNS** and **HTTPS certificates** enabled for the tailnet.
Without them `tailscale serve --https` refuses to start (no certificate). MagicDNS
also supplies the stable `https://<verva>.<tailnet>.ts.net` name. (The `sudo
tailscale set --operator=$(whoami)` step above still applies.)

## Enable (run once on verva)

```bash
tailscale serve --bg --https=443 --set-path /api http://127.0.0.1:8601
```

The tracker keeps its existing `tcp 8501` exposure unchanged.

## Verify

```bash
tailscale serve status
# expect an HTTPS forward for /api → http://127.0.0.1:8601 on 443
```

From a tailnet device, `https://<verva>.<tailnet>.ts.net/api/health` returns
`200 {"status":"ok","version":"<sha>"}` with a valid certificate (no `-k`).
From a non-tailnet LAN device, `http://<verva LAN IP>:8601` is refused (the
compose publish is loopback-only).

## Configuration

Clients and scripts read the API base URL from `JOB_AGENT_API_URL` (no host is
hard-coded — FR-016):

- from the tailnet: `https://<verva>.<tailnet>.ts.net/api`
- on the host / in-container: `http://127.0.0.1:8601`

`scripts/health_check.py --api-url` defaults to `$JOB_AGENT_API_URL`, falling back
to `http://127.0.0.1:8601`.

## Forwarded headers

Only the loopback proxy (tailscaled on the same host) may set forwarded headers.
The `api` container binds `127.0.0.1:8601`, so the app does **not** enable
`--forwarded-allow-ips=*`.

## Undo

```bash
tailscale serve --bg --https=443 off
```

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
