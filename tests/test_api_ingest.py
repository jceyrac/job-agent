"""Contract tests for `POST /v1/jobs` (spec 044, US1 — ingest).

A submitted posting must round-trip to the same `JobPosting.id` the local path
computes (URL when present, else `title::company::source`), save only a raw row
(no extraction, no LLM, no score), and be idempotent — an existing id returns
`created: false` unchanged. Scope `ingest` gates the write.
"""
from __future__ import annotations

import sqlite3

from core.models import JobPosting


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _job_id(source: str, title: str, company: str, url: str | None) -> str:
    return JobPosting(
        source=source, title=title, company=company, location="", url=url or "",
    ).id


def test_submit_new_job(api_env):
    tok = api_env.make_token(["ingest"])
    body = {
        "title": "Senior PM", "company": "Acme", "url": None,
        "location": "Zürich, CH", "description": "own the roadmap",
    }
    r = api_env.client.post("/v1/jobs", json=body, headers=_auth(tok))
    assert r.status_code == 200
    jid = _job_id("manual", "Senior PM", "Acme", None)
    assert r.json() == {"job_id": jid, "created": True}

    # Raw row only: present, not extracted (no LLM ran), no score written.
    row = api_env.db.get_catalogue_job(jid)
    assert row is not None
    assert row["title"] == "Senior PM"
    assert row["company"] == "Acme"
    assert row["extracted_at"] is None

    conn = sqlite3.connect(api_env.path)
    n = conn.execute("SELECT COUNT(*) FROM job_scores WHERE job_id = ?", (jid,)).fetchone()[0]
    conn.close()
    assert n == 0


def test_submit_existing_job_unchanged(api_env):
    tok = api_env.make_token(["ingest"])
    body = {
        "title": "Senior PM", "company": "Acme", "url": None,
        "source": "manual", "description": "first",
    }
    r1 = api_env.client.post("/v1/jobs", json=body, headers=_auth(tok))
    assert r1.json()["created"] is True

    # Resubmit with a different description → unchanged, `created: false`.
    r2 = api_env.client.post(
        "/v1/jobs", json={**body, "description": "second"}, headers=_auth(tok)
    )
    assert r2.status_code == 200
    assert r2.json() == {"job_id": r1.json()["job_id"], "created": False}
    assert api_env.db.get_catalogue_job(r1.json()["job_id"])["description"] == "first"


def test_submit_url_job_id_from_url(api_env):
    tok = api_env.make_token(["ingest"])
    body = {"title": "PM", "company": "Acme", "url": "https://acme.com/jobs/1", "source": "manual"}
    r = api_env.client.post("/v1/jobs", json=body, headers=_auth(tok))
    assert r.status_code == 200
    expected = _job_id("manual", "PM", "Acme", "https://acme.com/jobs/1")
    assert r.json() == {"job_id": expected, "created": True}


def test_submit_paste_source_is_part_of_id(api_env):
    # `source` is part of `JobPosting.id` for URL-less postings, so the same
    # title/company under `paste` must differ from `manual` (spec 036 id rule).
    tok = api_env.make_token(["ingest"])
    body = {"title": "PM", "company": "Acme", "source": "paste", "description": "…"}
    r = api_env.client.post("/v1/jobs", json=body, headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["job_id"] == _job_id("paste", "PM", "Acme", None)
    assert r.json()["job_id"] != _job_id("manual", "PM", "Acme", None)


def test_submit_missing_title_company_no_url_422(api_env):
    tok = api_env.make_token(["ingest"])
    r = api_env.client.post(
        "/v1/jobs", json={"title": "", "company": "", "url": None}, headers=_auth(tok)
    )
    assert r.status_code == 422


def test_submit_requires_ingest_scope(api_env):
    tok = api_env.make_token(["read:me", "write:me"])  # no `ingest`
    r = api_env.client.post(
        "/v1/jobs", json={"title": "X", "company": "Y"}, headers=_auth(tok)
    )
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}
