"""Contract tests for `POST /v1/jobs` (spec 044, US1 — ingest).

A submitted posting must round-trip to the same `JobPosting.id` the local path
computes (URL when present, else `title::company::source`), save only a raw row
(no extraction, no LLM, no score), and be idempotent — an existing id returns
`created: false` unchanged. Scope `ingest` gates the write.
"""
from __future__ import annotations

import sqlite3

from core.models import JobPosting
from core.scrapers import discover_scrapers


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


# ── GET /v1/ingest/plan (spec 045, US2) ─────────────────────────────────────

def _seed_monitored_company(path, name, *, method, slug, provider):
    """Insert a watched, ready company (+ user_companies.monitored=1) so it shows
    up in `get_monitored_companies()` and `get_watching_companies_by_method()`."""
    conn = sqlite3.connect(path)
    cur = conn.execute(
        """INSERT INTO companies (name, name_normalized, status, first_seen_at,
                                  last_seen_at, created_at, ats_provider,
                                  ats_identifier, scraping_method, monitoring_status)
           VALUES (?, ?, 'prospect', '2026-01-01', '2026-01-01', '2026-01-01',
                   ?, ?, ?, 'watch_ready')""",
        (name, name.lower(), provider, slug, method),
    )
    company_id = cur.lastrowid
    conn.execute(
        """INSERT INTO user_companies (user_id, company_id, status, monitored, created_at)
           VALUES ('user-a', ?, 'prospect', 1, '2026-01-01')""",
        (company_id,),
    )
    conn.commit()
    conn.close()


def test_plan_requires_token(api_env):
    assert api_env.client.get("/v1/ingest/plan").status_code == 401


def test_plan_requires_ingest_scope(api_env):
    tok = api_env.make_token(["read:me"])  # no `ingest`
    r = api_env.client.get("/v1/ingest/plan", headers=_auth(tok))
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


def test_plan_field_for_field(api_env):
    db = api_env.db
    db.set_ingestion_scope(
        title_families=["product manager"],
        exclude=["intern"],
        remote_or_hybrid=True,
        search_locations=["Switzerland", "France"],
        greenhouse_boards=["coinbase"],
    )
    db.set_config("freshness_days", "45")
    db.set_config("scraper.indeed.enabled", "false")
    db.set_config("monitoring.ats.greenhouse.enabled", "false")

    _seed_monitored_company(api_env.path, "Coinbase", method="greenhouse",
                            slug="coinbase", provider="greenhouse")
    _seed_monitored_company(api_env.path, "Impossible Cloud", method="lever",
                            slug="impossiblecloud", provider="lever")
    _seed_monitored_company(api_env.path, "WalletConnect", method="workable",
                            slug="walletconnect", provider="workable")

    r = api_env.client.get("/v1/ingest/plan", headers=_auth(api_env.make_token(["ingest"])))
    assert r.status_code == 200
    plan = r.json()

    scope = db.get_ingestion_scope()
    assert plan["title_families"] == scope["title_families"]
    assert plan["exclude"] == scope["exclude"]
    assert plan["remote_or_hybrid"] == scope["remote_or_hybrid"]
    assert plan["search_locations"] == scope["search_locations"]
    assert plan["greenhouse_boards"] == scope["greenhouse_boards"]
    assert plan["freshness_days"] == db.get_freshness_days() == 45

    # enabled_sources equals the direct per-scraper config read, override honoured.
    expected_enabled = {}
    for cls in discover_scrapers():
        val = db.get_config(cls.enabled_config_key())
        expected_enabled[cls.slug()] = cls.ENABLED if val is None else val.lower() == "true"
    assert plan["enabled_sources"] == expected_enabled
    assert plan["enabled_sources"]["indeed"] is False

    # monitoring gates equal the direct per-provider config read, override honoured.
    expected_gates = {}
    for provider in sorted({c["ats_provider"] for c in db.get_monitored_companies()
                            if c.get("ats_provider")}):
        val = db.get_config(f"monitoring.ats.{provider}.enabled")
        expected_gates[provider] = val is None or val.lower() != "false"
    assert plan["monitoring_gates"] == expected_gates
    assert plan["monitoring_gates"]["greenhouse"] is False

    # monitored targets map get_monitored_companies() field-for-field.
    expected_targets = [
        {"company_id": r["id"], "name": r["name"],
         "ats_provider": r.get("ats_provider"), "ats_identifier": r.get("ats_identifier")}
        for r in db.get_monitored_companies()
    ]
    assert plan["monitored_targets"] == expected_targets

    def _slugs(method):
        return [r["ats_identifier"] for r in db.get_watching_companies_by_method(method)]

    assert plan["board_slugs"] == {
        "lever": _slugs("lever"),
        "workable": _slugs("workable"),
        "greenhouse_watching": _slugs("greenhouse"),
    }


# ── POST /v1/ingest/batch (spec 045, US3) ────────────────────────────────────

def test_batch_requires_token(api_env):
    assert api_env.client.post("/v1/ingest/batch", json=[]).status_code == 401


def test_batch_requires_ingest_scope(api_env):
    tok = api_env.make_token(["read:me"])  # no `ingest`
    r = api_env.client.post("/v1/ingest/batch", json=[], headers=_auth(tok))
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


def test_batch_new_and_idempotent(api_env):
    tok = api_env.make_token(["ingest"])
    body = [{"source": "board", "title": "Staff Engineer", "company": "Acme",
             "url": "https://acme.com/jobs/42", "location": "Remote"}]
    r1 = api_env.client.post("/v1/ingest/batch", json=body, headers=_auth(tok))
    assert r1.status_code == 200
    jid = _job_id("board", "Staff Engineer", "Acme", "https://acme.com/jobs/42")
    assert r1.json()["new"] == 1
    assert r1.json()["existing"] == 0
    assert r1.json()["dropped_engaged"] == 0
    assert r1.json()["outcomes"] == [{"job_id": jid, "result": "new"}]

    # Same batch again → nothing new (deterministic id).
    r2 = api_env.client.post("/v1/ingest/batch", json=body, headers=_auth(tok))
    assert r2.json()["new"] == 0
    assert r2.json()["existing"] == 1
    assert r2.json()["outcomes"] == [{"job_id": jid, "result": "existing"}]

    conn = sqlite3.connect(api_env.path)
    n = conn.execute("SELECT COUNT(*) FROM jobs WHERE id = ?", (jid,)).fetchone()[0]
    conn.close()
    assert n == 1


def test_batch_per_posting_outcomes(api_env):
    # A posting with no URL and empty title/company is reported, not fatal.
    tok = api_env.make_token(["ingest"])
    body = [
        {"source": "board", "title": "PM", "company": "Acme", "url": "https://a.com/1"},
        {"title": "", "company": "", "url": None},  # → error
    ]
    r = api_env.client.post("/v1/ingest/batch", json=body, headers=_auth(tok))
    assert r.status_code == 200
    data = r.json()
    assert data["new"] == 1
    assert data["existing"] == 0
    assert data["dropped_engaged"] == 0
    good_id = _job_id("board", "PM", "Acme", "https://a.com/1")
    assert data["outcomes"] == [
        {"job_id": good_id, "result": "new"},
        {"job_id": None, "result": "error"},
    ]


def test_batch_engaged_dedup_touches_last_seen(api_env):
    # Seed has job-1 ('Backend', 'Alpha', status='applied'). A scraped 'Backend'
    # @ 'Alpha' normalizes to the same engaged key → dropped, job-1 touched.
    tok = api_env.make_token(["ingest"])
    conn = sqlite3.connect(api_env.path)
    before = conn.execute("SELECT last_seen FROM jobs WHERE id = 'job-1'").fetchone()[0]
    conn.close()

    body = [{"source": "board", "title": "Backend", "company": "Alpha", "url": None}]
    r = api_env.client.post("/v1/ingest/batch", json=body, headers=_auth(tok))
    assert r.status_code == 200
    data = r.json()
    assert data["new"] == 0
    assert data["existing"] == 0
    assert data["dropped_engaged"] == 1
    assert data["outcomes"] == [{"job_id": "job-1", "result": "dropped_engaged"}]

    conn = sqlite3.connect(api_env.path)
    after = conn.execute("SELECT last_seen FROM jobs WHERE id = 'job-1'").fetchone()[0]
    n_new = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert after != before
    assert n_new == 2  # only the two seeded jobs — no new row inserted


def test_batch_carries_base_location_and_posted_date(api_env):
    tok = api_env.make_token(["ingest"])
    body = [{"source": "board", "title": "Eng", "company": "Acme",
             "url": "https://acme.com/e1", "base_location": "United States",
             "posted_date": "2026-10-01"}]
    r = api_env.client.post("/v1/ingest/batch", json=body, headers=_auth(tok))
    assert r.status_code == 200
    jid = _job_id("board", "Eng", "Acme", "https://acme.com/e1")
    row = api_env.db.get_catalogue_job(jid)
    assert row["base_location"] == "United States"
    assert row["posted_date"] == "2026-10-01"


def test_batch_accepts_body_over_64kb_under_5mb(api_env):
    # The route-specific 5 MB cap must apply (the global cap is 64 KB).
    tok = api_env.make_token(["ingest"])
    big = "x" * (128 * 1024)  # 128 KB description
    body = [{"source": "board", "title": "Big", "company": "Acme",
             "url": "https://acme.com/big", "description": big}]
    r = api_env.client.post("/v1/ingest/batch", json=body, headers=_auth(tok))
    assert r.status_code == 200


def test_batch_413_over_5mb(api_env):
    tok = api_env.make_token(["ingest"])
    huge = "y" * (6 * 1024 * 1024)  # 6 MB description → over the 5 MB cap
    body = [{"source": "board", "title": "Huge", "company": "Acme",
             "url": "https://acme.com/huge", "description": huge}]
    r = api_env.client.post("/v1/ingest/batch", json=body, headers=_auth(tok))
    assert r.status_code == 413


# ── POST /v1/ingest/events (spec 045, US3) ───────────────────────────────────

def test_events_requires_ingest_scope(api_env):
    tok = api_env.make_token(["read:me"])
    r = api_env.client.post(
        "/v1/ingest/events",
        json={"type": "run", "jobs_scraped": 1, "duration_seconds": 1.0, "run_type": "scrape"},
        headers=_auth(tok),
    )
    assert r.status_code == 403


def test_events_source_disabled(api_env):
    tok = api_env.make_token(["ingest"])
    r = api_env.client.post(
        "/v1/ingest/events",
        json={"type": "source_disabled", "source": "linkedin", "reason": "ip block"},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert api_env.db.get_config("scraper.linkedin.enabled") == "false"
    assert api_env.db.get_config("scraper.linkedin.disabled_reason") == "ip block"


def test_events_run_record(api_env):
    tok = api_env.make_token(["ingest"])
    r = api_env.client.post(
        "/v1/ingest/events",
        json={"type": "run", "jobs_scraped": 42, "duration_seconds": 12.5,
              "run_type": "scrape"},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    run = api_env.db.get_last_run("platform")
    assert run["jobs_scraped"] == 42
    assert run["status"] == "scraped"
    assert run["run_type"] == "scrape"
