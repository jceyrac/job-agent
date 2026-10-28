"""Unit tests for the flag-on ingest client (spec 045, FR-005 / US5-2).

`_load_ingest_token` and `_IngestClient` must fail loudly — every failure mode
raises `_IngestError` and the run exits non-zero, with **no silent fallback** to
the direct write path. These are offline tests: the HTTP layer is a
`httpx.MockTransport`, so no network or API is touched.
"""
from __future__ import annotations

import os
import sys

import httpx
import pytest

from core import scrape as scrape_mod


# ── _load_ingest_token ────────────────────────────────────────────────────────

def test_token_from_env(monkeypatch):
    monkeypatch.setenv("JOB_AGENT_API_TOKEN", "env-secret")
    assert scrape_mod._load_ingest_token() == "env-secret"


def test_token_missing_file(monkeypatch, tmp_path):
    monkeypatch.delenv("JOB_AGENT_API_TOKEN", raising=False)
    monkeypatch.setenv("JOB_AGENT_API_TOKEN_FILE", str(tmp_path / "does-not-exist"))
    with pytest.raises(scrape_mod._IngestError, match="no ingest API token"):
        scrape_mod._load_ingest_token()


def test_token_empty_file(monkeypatch, tmp_path):
    monkeypatch.delenv("JOB_AGENT_API_TOKEN", raising=False)
    path = tmp_path / "token"
    path.write_text("   \n")
    monkeypatch.setenv("JOB_AGENT_API_TOKEN_FILE", str(path))
    with pytest.raises(scrape_mod._IngestError, match="is empty"):
        scrape_mod._load_ingest_token()


def test_token_permissive_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("JOB_AGENT_API_TOKEN", raising=False)
    path = tmp_path / "token"
    path.write_text("secret")
    os.chmod(path, 0o644)
    monkeypatch.setenv("JOB_AGENT_API_TOKEN_FILE", str(path))
    with pytest.raises(scrape_mod._IngestError, match="permissive mode"):
        scrape_mod._load_ingest_token()


def test_token_owner_only_file_ok(monkeypatch, tmp_path):
    monkeypatch.delenv("JOB_AGENT_API_TOKEN", raising=False)
    path = tmp_path / "token"
    path.write_text("secret")
    os.chmod(path, 0o600)
    monkeypatch.setenv("JOB_AGENT_API_TOKEN_FILE", str(path))
    assert scrape_mod._load_ingest_token() == "secret"


# ── _IngestClient failure modes ───────────────────────────────────────────────

def _client_with_status(monkeypatch, status: int) -> scrape_mod._IngestClient:
    real_client = httpx.Client  # captured before patching — avoids recursion

    def fake_client(*args, **kwargs):
        transport = httpx.MockTransport(
            lambda req: httpx.Response(status, request=req))
        return real_client(transport=transport, base_url=kwargs["base_url"],
                           headers=kwargs["headers"])

    monkeypatch.setattr(scrape_mod.httpx, "Client", fake_client)
    return scrape_mod._IngestClient(base_url="http://api:8601", token="tok")


def _client_unreachable(monkeypatch) -> scrape_mod._IngestClient:
    real_client = httpx.Client  # captured before patching — avoids recursion

    def fake_client(*args, **kwargs):
        def handler(req):
            raise httpx.ConnectError("connection refused", request=req)
        transport = httpx.MockTransport(handler)
        return real_client(transport=transport, base_url=kwargs["base_url"],
                           headers=kwargs["headers"])

    monkeypatch.setattr(scrape_mod.httpx, "Client", fake_client)
    return scrape_mod._IngestClient(base_url="http://api:8601", token="tok")


@pytest.mark.parametrize("status", [401, 403])
def test_client_auth_failure(monkeypatch, status):
    client = _client_with_status(monkeypatch, status)
    with pytest.raises(scrape_mod._IngestError, match="auth failed"):
        client.get_plan()


def test_client_server_error(monkeypatch):
    client = _client_with_status(monkeypatch, 500)
    with pytest.raises(scrape_mod._IngestError, match="server error"):
        client.submit_batch([])


def test_client_unreachable(monkeypatch):
    client = _client_unreachable(monkeypatch)
    with pytest.raises(scrape_mod._IngestError, match="unreachable"):
        client.get_plan()


# ── _ingest_via_api precedence: config key is the single source of truth ──────

def _flag_db(value):
    class _DB:
        def get_config(self, key):
            assert key == "ingest_via_api"
            return value
    return _DB()


@pytest.mark.parametrize("cfg,env,expected", [
    ("on", None, True),      # config on, no env
    ("on", "off", True),     # config wins over env
    ("off", None, False),
    ("off", "on", False),    # config wins over env (production rule)
    (None, "on", True),      # env bootstrap when config absent (staging)
    (None, "true", True),
    (None, "off", False),
    (None, None, False),     # default off
])
def test_ingest_via_api_precedence(monkeypatch, cfg, env, expected):
    if env is None:
        monkeypatch.delenv("INGEST_VIA_API", raising=False)
    else:
        monkeypatch.setenv("INGEST_VIA_API", env)
    assert scrape_mod._ingest_via_api(_flag_db(cfg)) is expected


# ── the run fails loudly, never falls back to the direct path ─────────────────

def test_main_exits_nonzero_on_ingest_error(monkeypatch):
    class _FakeDB:
        def get_config(self, key):
            return "on"

    monkeypatch.setattr(scrape_mod, "JobStorage", lambda path: _FakeDB())
    monkeypatch.setattr(scrape_mod, "_ingest_via_api", lambda db: True)

    def _boom(*args, **kwargs):
        raise scrape_mod._IngestError("token missing")
    monkeypatch.setattr(scrape_mod, "_IngestClient", _boom)

    direct_called = {"value": False}
    monkeypatch.setattr(
        scrape_mod, "_run_broad_scrape",
        lambda db, profile: direct_called.__setitem__("value", True))

    exit_codes = []
    monkeypatch.setattr(sys, "exit", lambda code: exit_codes.append(code))
    monkeypatch.setattr(sys, "argv", ["scrape"])

    scrape_mod.main()

    assert exit_codes == [1]
    assert direct_called["value"] is False
