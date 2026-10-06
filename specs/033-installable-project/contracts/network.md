# Contract: Tailscale-only tracker exposure (US4, FR-012/013/014)

## Production tracker (port 8501)

- Compose publishes **loopback only**: `"127.0.0.1:8501:8501"` (FR-012).
- The tailnet forwards TCP 8501 to loopback (run once on verva, persisted in
  tailscaled state):

  ```bash
  tailscale serve --bg --tcp=8501 tcp://127.0.0.1:8501
  ```

- **Deploy order** (FR-013, avoids tailnet downtime): deploy the compose change →
  immediately run the `tailscale serve` command → verify with `tailscale serve
  status`.

### Acceptance

- Tailnet device: `http://100.74.139.28:8501` and `/job_detail?id=<id>` work as
  today.
- Non-tailnet LAN device: `http://<verva LAN IP>:8501` is refused (port closed).
- In-container health check `localhost:8501` keeps working unchanged.
- After a reboot of verva, the tailnet URL works again without manual action.

### Undo

```bash
tailscale serve --bg --tcp=8501 off
```

## Staging tracker (port 8502)

- Container binds loopback only (`127.0.0.1:8502:8501`; Streamlit
  `--server.address 127.0.0.1`) — **never `0.0.0.0`** (FR-014).
- Tailnet forward: `tailscale serve --bg --tcp=8502 tcp://127.0.0.1:8502`.
- Binding directly to the Tailscale IP is permitted for staging (manual `up`, no
  boot race), but the loopback + `tailscale serve` pattern is preferred for
  consistency with production.
