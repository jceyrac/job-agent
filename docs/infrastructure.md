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
