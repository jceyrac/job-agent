"""Agent-as-API-client node tests (spec 044, US3).

Each test runs the real ``nodes`` functions against a FastAPI ``TestClient``
backed by an in-memory DB (``api_env``), with the agent's ``api_client`` pointed
at that app via an ``httpx.ASGITransport``. The LLM never runs: the ``score_job``
worker is simulated by stubbing ``nodes._wait_for_task`` to seed the score
directly (exactly what the server's worker would persist). This proves the agent
reads/writes only through the API — the DB rows land through the routes, not
through ``JobStorage``.
"""

import asyncio

import httpx
import pytest
from unittest.mock import patch

from core.cv_agent import api_client
from core.cv_agent import nodes
from core.cv_agent.cli import _resolve_profile_id
from core.cv_agent.nodes import file_and_record, mark_skipped, resolve_reference
from core.models import JobPosting, posting_from_dict

FULL_SCOPES = ["read:me", "write:me", "tasks:run", "ingest"]


class _SyncASGITransport(httpx.BaseTransport):
    """Drive an ASGI app synchronously for the plain (sync) ``httpx.Client``.

    httpx 0.28 has no sync ASGI transport (``ASGITransport`` is async-only), and
    starlette 1.8's ``TestClient`` is built on the separate ``httpx2`` package —
    so the agent's real ``AgentClient`` (a plain ``httpx.Client``) cannot reuse
    either. This runs the app on a fresh loop per request via ``asyncio.run``.
    """

    def __init__(self, app, *, raise_app_exceptions=True):
        self.app = app
        self.raise_app_exceptions = raise_app_exceptions

    def handle_request(self, request):
        transport = httpx.ASGITransport(
            app=self.app, raise_app_exceptions=self.raise_app_exceptions
        )

        async def _run():
            response = await transport.handle_async_request(request)
            # Rebuild as a sync response: a sync httpx.Client requires a
            # SyncByteStream, while ASGITransport yields an async one.
            return httpx.Response(
                status_code=response.status_code,
                headers=response.headers,
                content=await response.aread(),
                extensions=response.extensions,
                request=request,
            )

        return asyncio.run(_run())


@pytest.fixture
def agent(api_env):
    """Install the singleton API client against the test app, full scopes."""
    tok = api_env.make_token(FULL_SCOPES)
    client = api_client.AgentClient(
        base_url="http://test", token=tok, transport=_SyncASGITransport(api_env.app)
    )
    api_client._set_client(client)
    yield api_env
    api_client.reset_client()


def _seed_user_no_profile(path: str) -> None:
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        INSERT INTO users (id, display_name, active_profile_id, onboarding_completed_at, created_at)
        VALUES ('user-noprof', 'No Prof', NULL, '2026-01-01', '2026-01-01');
        """
    )
    conn.commit()
    conn.close()


# A real job id is a 20-char lowercase-hex string (sha256[:20]); ``resolve_reference``
# only classifies such a string as an id entry, so the id-path tests seed a job whose
# stored id is the genuine ``JobPosting.id`` for its fields (not an arbitrary hex).
def _seed_hex_job(path: str) -> str:
    job = JobPosting(
        source="board", title="Backend", company="Alpha",
        location="Zurich", url="https://example.com/backend",
    )
    jid = job.id
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        INSERT INTO jobs (id, title, company, company_id, source, location, url, first_seen, last_seen)
        VALUES ('{jid}', 'Backend', 'Alpha', 1, 'board', 'Zurich',
                'https://example.com/backend', '2026-01-01', '2026-01-02');
        INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at)
        VALUES ('{jid}', 'prof-a', 8, 'good fit', 'llm', '2026-01-02');
        """
    )
    conn.commit()
    conn.close()
    return jid


# ── id entry: read the job + score through the API ───────────────────────────

def test_resolve_reference_id_reads_job_and_score(agent):
    jid = _seed_hex_job(agent.path)
    out = resolve_reference({"reference": jid, "profile_id": "prof-a"})

    assert out["entry_kind"] == "id"
    assert out["job_id"] == jid
    assert out["job"]["title"] == "Backend"
    assert out["score"] == 8
    assert out["score_reason"] == "good fit"
    # deterministic contact profile is still computed from the job's fields
    assert out["proposed_profile"] in ("swiss", "french")


def test_resolve_reference_unknown_id_raises_not_found(agent):
    with pytest.raises(api_client.ApiError) as ei:
        resolve_reference({"reference": "9" * 20, "profile_id": "prof-a"})
    assert ei.value.kind == "not_found"


# ── paste entry: submit + server-side score, no LLM on the client ────────────

def test_resolve_reference_paste_submits_and_scores(agent):
    # Simulate the worker: seed the score into the server DB, then report success.
    def fake_wait(task_id):
        task = api_client.get_task(task_id)
        params = task["params"]
        detail = api_client.get_job(params["job_id"])
        job = posting_from_dict(detail["job"])
        agent.db.save_scored(
            job,
            {"score": 7, "reason": "strong match", "country_code": "CH", "comp_flag": False},
            params["profile_id"],
        )
        return {"status": "succeeded", "error": None}

    state = {
        "reference": "own the roadmap from day one",
        "profile_id": "prof-a",
        "job_title": "Product Lead",
        "job_company": "Acme",
    }
    with patch.object(nodes, "_wait_for_task", side_effect=fake_wait):
        out = resolve_reference(state)

    assert out["entry_kind"] == "paste"
    assert out["job_id"] == JobPosting(
        source="paste", title="Product Lead", company="Acme", location="", url=""
    ).id
    assert out["score"] == 7
    assert out["score_reason"] == "strong match"
    # The posting was submitted and persisted by the server (not the agent).
    assert agent.db.get_catalogue_job(out["job_id"]) is not None


# ── skip → archived via the API ──────────────────────────────────────────────

def test_mark_skipped_archives_via_api(agent):
    mark_skipped({"job_id": "job-2"})
    assert agent.db.get_job_for_prepare("job-2")["status"] == "archived"


# ── approve → application + ready via the API ────────────────────────────────

def test_file_and_record_writes_application_and_ready(agent):
    state = {
        "job_id": "job-2",
        "slug": "beta-frontend",
        "fit_analysis": {"fit_recap": "ok", "angle": "payments", "gaps": []},
        "cover_letter": "Dear hiring manager…",
        "recruiter_message": None,
        "nextcloud_web_url": None,
        "pdf_url": None,
        "output_paths": {"pdf": "/tmp/out/Beta - Frontend/x.pdf",
                         "local_dir": "/tmp/out/Beta - Frontend"},
    }
    file_and_record(state)

    app_row = agent.db.get_application("job-2")
    assert app_row is not None
    assert app_row["cover_letter"] == "Dear hiring manager…"
    assert agent.db.get_job_for_prepare("job-2")["status"] == "ready"


# ── profile resolution through GET /v1/me ────────────────────────────────────

def test_resolve_profile_returns_active(agent):
    assert _resolve_profile_id(None) == "prof-a"


def test_resolve_profile_unknown_override_falls_back(agent, capsys):
    assert _resolve_profile_id("bogus") == "prof-a"
    assert "unknown profile" in capsys.readouterr().err


def test_resolve_profile_missing_active_profile_raises(api_env):
    _seed_user_no_profile(api_env.path)
    tok = api_env.make_token(["read:me"], user_id="user-noprof")
    client = api_client.AgentClient(
        base_url="http://test", token=tok, transport=_SyncASGITransport(api_env.app)
    )
    api_client._set_client(client)
    try:
        with pytest.raises(api_client.ApiError) as ei:
            _resolve_profile_id(None)
        assert "no active profile" in str(ei.value)
    finally:
        api_client.reset_client()


# ── error mapping ────────────────────────────────────────────────────────────

def test_client_maps_missing_scope_to_auth(api_env):
    tok = api_env.make_token(["read:me"])  # no `ingest`
    client = api_client.AgentClient(
        base_url="http://test", token=tok, transport=_SyncASGITransport(api_env.app)
    )
    with pytest.raises(api_client.ApiError) as ei:
        client.submit_job({"title": "X", "company": "Y"})
    assert ei.value.kind == "auth"


def test_client_maps_unreachable(api_env):
    client = api_client.AgentClient(base_url="http://127.0.0.1:1", token="x")
    with pytest.raises(api_client.ApiError) as ei:
        client.get_me()
    assert ei.value.kind == "unreachable"
