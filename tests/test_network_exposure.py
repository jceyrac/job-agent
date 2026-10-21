"""tests/test_network_exposure.py — guard: tracker/API stay off the LAN.

Two invariants, enforced on both prod (`docker-compose.yml`) and staging
(`scripts/staging.sh`):

1. Every published port is bound to ``127.0.0.1`` on the **host**. The tailnet
   reaches the tracker/API only through ``tailscale serve`` forwarding to loopback;
   a non-loopback publish would expose the unauthenticated tracker on the LAN
   (spec 033 FR-013, spec 041 FR-016).

2. The in-container server binds ``0.0.0.0`` (Streamlit ``--server.address`` /
   uvicorn ``--host``). The container's loopback is not the host's loopback, so a
   ``127.0.0.1`` bind inside the container is unreachable through Docker's ``-p``
   mapping.

Staging does not use compose, so both files are guarded here — this test is the
only guard for the prod compose publish.
"""

import pathlib
import re

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_COMPOSE = _ROOT / "docker-compose.yml"
_STAGING = _ROOT / "scripts" / "staging.sh"

# A loopback-only compose publish: 127.0.0.1:<host-port>:<container-port>.
_LOOPBACK_PUBLISH = re.compile(r"127\.0\.0\.1:\d+:\d+")
# A loopback-only staging.sh publish, where the host port is a shell variable.
_STAGING_PUBLISH = re.compile(r"127\.0\.0\.1:\$\{[A-Z_]+\}:\d+")


def _compose():
    with open(_COMPOSE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _flag_value(text: str, flag: str) -> str:
    """Return the value of a ``--flag <value>`` pair in ``text`` (asserts present)."""
    match = re.search(rf"{re.escape(flag)}\s+(\S+)", text)
    assert match, f"{flag} not found"
    return match.group(1)


def _assert_publish_loopback(ports):
    assert ports, "no published ports"
    for spec in ports:
        assert _LOOPBACK_PUBLISH.fullmatch(spec), (
            f"publish '{spec}' is not loopback-only on the host "
            "(want 127.0.0.1:<host>:<container>)"
        )


# ── Prod (compose) ────────────────────────────────────────────────────────────

def test_compose_tracker_loopback_only_and_binds_all_interfaces():
    svc = _compose()["services"]["tracker"]
    _assert_publish_loopback(svc["ports"])
    assert _flag_value(svc["command"], "--server.address") == "0.0.0.0"


def test_compose_api_loopback_only_and_binds_all_interfaces():
    svc = _compose()["services"]["api"]
    _assert_publish_loopback(svc["ports"])
    assert _flag_value(svc["command"], "--host") == "0.0.0.0"


def test_compose_tracker_health_check_reaches_api_by_service_name():
    # The post-deploy health check runs `docker exec job-tracker …`, so it sees
    # the tracker container's environment: the API must be addressed by the
    # compose service name (http://api:8601), never the host loopback.
    svc = _compose()["services"]["tracker"]
    env = dict(e.split("=", 1) for e in svc["environment"])
    assert env["JOB_AGENT_API_URL"] == "http://api:8601"


# ── Staging (scripts/staging.sh) ──────────────────────────────────────────────

def test_staging_sh_loopback_only_and_binds_all_interfaces():
    src = _STAGING.read_text(encoding="utf-8")

    publishes = re.findall(r'-p\s+"([^"]+)"', src)
    assert publishes, "no `-p` publish flags found in staging.sh"
    for spec in publishes:
        assert _STAGING_PUBLISH.fullmatch(spec), (
            f"staging.sh publish '{spec}' is not loopback-only on the host"
        )

    assert _flag_value(src, "--server.address") == "0.0.0.0"
    assert _flag_value(src, "--host") == "0.0.0.0"


# ── Negative fixtures — the scanners must catch each violation ────────────────

def test_loopback_publish_regex_rejects_wildcard_and_bare_ports():
    assert _LOOPBACK_PUBLISH.fullmatch("0.0.0.0:8501:8501") is None
    assert _LOOPBACK_PUBLISH.fullmatch("8501:8501") is None  # implicit 0.0.0.0
    assert _LOOPBACK_PUBLISH.fullmatch("127.0.0.1:8501:8501") is not None


def test_staging_publish_regex_rejects_literal_ports():
    assert _STAGING_PUBLISH.fullmatch("127.0.0.1:${PORT}:8501") is not None
    assert _STAGING_PUBLISH.fullmatch("0.0.0.0:${PORT}:8501") is None
    assert _STAGING_PUBLISH.fullmatch("127.0.0.1:8502:8501") is None
