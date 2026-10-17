"""Auth semantics for the v1 API (spec 041, FR-005 / FR-006 / FR-013 / FR-014).

401 for missing/unknown/revoked/expired; 403 for a missing scope; the
`last_used_at` throttle; no token material in any log; and the token CLI
(create prints once, list omits the hash, revoke). Uses `tests/conftest.py`'s
`api_env` (file DB behind a `TestClient`).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

import api.deps as deps
from core.storage import API_TOKEN_SCOPES, JobStorage

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _reset_auth_state():
    deps._last_touch.clear()
    deps._failed_auth.clear()
    yield
    deps._last_touch.clear()
    deps._failed_auth.clear()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── scope constants (FR-004) ─────────────────────────────────────────────────

def test_api_token_scopes_are_fixed():
    assert API_TOKEN_SCOPES == frozenset(
        {
            "read:catalogue", "read:me", "write:me", "write:catalogue",
            "tasks:run", "tasks:pipeline",
        }
    )


# ── 401 semantics (FR-005) ───────────────────────────────────────────────────

def test_401_missing_token(api_env):
    r = api_env.client.get("/v1/me")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized", "message": "Missing bearer token"}


def test_401_unknown_token(api_env):
    r = api_env.client.get("/v1/me", headers=_auth("not-a-real-token"))
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized", "message": "Invalid token"}


def test_401_revoked_token(api_env):
    raw = api_env.make_token(["read:me"])
    row = api_env.db.resolve_api_token(raw)
    api_env.db.revoke_api_token(row["id"])

    r = api_env.client.get("/v1/me", headers=_auth(raw))
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized", "message": "Token revoked"}


def test_401_expired_token(api_env):
    raw = api_env.make_token(["read:me"], expires_at="2000-01-01T00:00:00+00:00")
    r = api_env.client.get("/v1/me", headers=_auth(raw))
    assert r.status_code == 401
    assert r.json() == {"error": "unauthorized", "message": "Token expired"}


# ── 403 scope (FR-005) ───────────────────────────────────────────────────────

def test_403_missing_scope(api_env):
    tok = api_env.make_token(["read:catalogue"])
    r = api_env.client.get("/v1/me", headers=_auth(tok))
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


# ── last_used_at throttle (FR-005) ───────────────────────────────────────────

def test_last_used_at_throttled_to_once_per_minute(api_env):
    raw = api_env.make_token(["read:me"])
    row = api_env.db.resolve_api_token(raw)
    token_id = row["id"]
    assert row["last_used_at"] is None

    # First request sets last_used_at.
    assert api_env.client.get("/v1/me", headers=_auth(raw)).status_code == 200
    t1 = api_env.db.resolve_api_token(raw)["last_used_at"]
    assert t1 is not None

    # Simulate a very recent touch so the throttle suppresses the next write.
    deps._last_touch[token_id] = time.monotonic()
    assert api_env.client.get("/v1/me", headers=_auth(raw)).status_code == 200
    assert api_env.db.resolve_api_token(raw)["last_used_at"] == t1


# ── FR-006 / FR-014: no token in logs, failed-auth counted ───────────────────

def test_no_token_material_in_logs(api_env, api_log_stream):
    raw = api_env.make_token(["read:me"])
    api_env.client.get("/v1/me", headers=_auth(raw))           # success: no auth log
    api_env.client.get("/v1/me", headers=_auth("bogus-token"))  # failure: reason only

    text = api_log_stream.getvalue()
    assert raw not in text
    assert "bogus-token" not in text
    assert "Authorization" not in text
    assert "unknown" in text  # the failure was logged with a reason, not the token


def test_failed_auth_counted_per_client(api_env):
    for _ in range(3):
        api_env.client.get("/v1/me", headers=_auth("bogus"))
    # TestClient reports its own host; assert any client counted 3 failures.
    assert max(deps._failed_auth.values()) == 3
    # The counter holds no token material.
    assert "bogus" not in json.dumps(deps._failed_auth)


# ── token CLI (FR-013 / api_token_cli.md) ────────────────────────────────────

def _run_cli(tmp_path, *args):
    env = {**os.environ, "JOB_AGENT_DATA_DIR": str(tmp_path)}
    return subprocess.run(
        [sys.executable, "-m", "scripts.api_token", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


def test_cli_create_prints_raw_once(api_env, tmp_path):
    # Use a separate temp data dir so the CLI never touches the live DB.
    p = _run_cli(tmp_path, "create", "--name", "smoke", "--scopes", "read:catalogue")
    assert p.returncode == 0, p.stderr
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    assert len(lines) == 2
    raw = lines[0]
    assert raw and "created token" in lines[1]
    assert raw not in lines[1]  # the raw token appears exactly once


def test_cli_list_omits_hash(api_env, tmp_path):
    p = _run_cli(tmp_path, "create", "--name", "smoke", "--scopes", "read:me")
    assert p.returncode == 0, p.stderr
    raw = p.stdout.splitlines()[0]

    p = _run_cli(tmp_path, "list", "--json")
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)
    assert len(rows) == 1
    assert "token_hash" not in rows[0]
    assert raw not in p.stdout  # the raw token never appears in a listing


def test_cli_revoke_and_unknown(api_env, tmp_path):
    p = _run_cli(tmp_path, "create", "--name", "smoke", "--scopes", "read:me")
    assert p.returncode == 0, p.stderr
    row = json.loads(_run_cli(tmp_path, "list", "--json").stdout)[0]

    p = _run_cli(tmp_path, "revoke", row["id"])
    assert p.returncode == 0, p.stdout

    p = _run_cli(tmp_path, "revoke", "does-not-exist")
    assert p.returncode == 1
    assert "Unknown token id" in p.stderr


def test_cli_invalid_scope_and_days(api_env, tmp_path):
    p = _run_cli(tmp_path, "create", "--name", "bad", "--scopes", "read:everything")
    assert p.returncode == 2
    assert "unknown scope" in p.stderr

    p = _run_cli(tmp_path, "create", "--name", "bad", "--scopes", "read:me", "--expires-in", "0")
    assert p.returncode == 2


# ── spec 042 scope enforcement on write routes (FR-005 / US4) ────────────────

_WRITE_ME_ROUTES = [
    ("PUT", "/v1/me/jobs/job-1/status", {"status": "applied", "occurred_at": "2026-01-05"}),
    ("POST", "/v1/me/jobs/job-1/events",
     {"event": "application_submitted", "occurred_at": "2026-01-05", "new_status": "applied"}),
    ("PATCH", "/v1/me/jobs/job-1/events/1", {"notes": "x"}),
    ("DELETE", "/v1/me/jobs/job-1/events/1", None),
    ("PATCH", "/v1/me/companies/1", {"status": "blacklisted"}),
    ("POST", "/v1/me/contacts", {"company_id": 1, "first_name": "X"}),
    ("PATCH", "/v1/me/contacts/1", {"notes": "x"}),
    ("POST", "/v1/me/contacts/1/interactions", {"type": "call"}),
    ("POST", "/v1/me/companies/1/interactions", {"type": "call"}),
    ("PUT", "/v1/me/active-profile", {"profile_id": "prof-a"}),
    ("PUT", "/v1/me/profiles/prof-a", {"name": "X", "criteria": {}}),
]


@pytest.mark.parametrize("method,path,body", _WRITE_ME_ROUTES)
def test_read_only_token_403_on_write_me_route(api_env, method, path, body):
    tok = api_env.make_token(["read:me"])
    r = api_env.client.request(method, path, json=body, headers=_auth(tok))
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


def test_read_only_token_403_on_catalogue_write(api_env):
    tok = api_env.make_token(["read:catalogue"])
    r = api_env.client.patch(
        "/v1/companies/1", json={"website": "https://example.com"}, headers=_auth(tok)
    )
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


def test_write_me_token_403_on_catalogue_write(api_env):
    tok = api_env.make_token(["write:me"])
    r = api_env.client.patch(
        "/v1/companies/1", json={"website": "https://example.com"}, headers=_auth(tok)
    )
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


@pytest.mark.parametrize("method,path,body", _WRITE_ME_ROUTES)
def test_write_catalogue_token_403_on_write_me_route(api_env, method, path, body):
    tok = api_env.make_token(["write:catalogue"])
    r = api_env.client.request(method, path, json=body, headers=_auth(tok))
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}
