"""Contract tests for the read-only v1 API (spec 041, FR-008 / US3).

Every endpoint's payload must equal its `JobStorage` source field-for-field
(same keys, same values) — the serializers pass values through unchanged. Tests
seed `tests/conftest.py`'s dataset and assert `response.json() == source`.
Catalogue payloads must contain no user-space key; absent ids → `404`; a
wrong-scope token → `403`.
"""
from __future__ import annotations

import pytest

from api.main import create_app
from fastapi.testclient import TestClient

# Keys that never belong on a shared-catalogue payload (user-space relationship,
# tracking, or score fields). Shared company enrichment (`monitoring_status`,
# `researched_at`, `research_notes`, …) is NOT user-space and stays in the facts.
_USER_SPACE_KEYS = {
    "status", "notes", "monitored", "score", "reason", "scored_by",
    "tracked_status", "score_count", "status_changed_at",
}

CATALOGUE_JOB_KEYS = {
    "id", "title", "company", "company_id", "url", "source", "location",
    "base_location", "posted_date", "description", "first_seen", "last_seen",
    "extracted_at", "extracted_by", "monitored_company_id", "canonical_url",
    "norm_title", "norm_company", "summary", "work_mode", "geo_zone",
    "company_size", "contract_type", "company_country", "industry_sector",
    "language_required", "country_code",
}

COMPANY_FACT_KEYS = {
    "id", "name", "website", "careers_url", "company_country", "industry_sector",
    "company_size", "ats_provider", "ats_identifier", "scraper_id",
    "first_seen_at", "last_seen_at", "summary", "monitoring_status",
    "scraping_method", "research_notes", "research_confidence", "researched_at",
    "x_handle",
}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── US1: /health ─────────────────────────────────────────────────────────────

def test_health(api_env):
    r = api_env.client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"status", "version"}
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)


def test_health_version_from_env(api_env, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "deadbeef")
    r = api_env.client.get("/health")
    assert r.status_code == 200
    assert r.json()["version"] == "deadbeef"


def test_health_version_unknown_when_unset(api_env, monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    r = api_env.client.get("/health")
    assert r.status_code == 200
    assert r.json()["version"] == "unknown"


# ── US3: catalogue ───────────────────────────────────────────────────────────

def test_catalogue_jobs(api_env):
    tok = api_env.make_token(["read:catalogue"])
    src = api_env.db.get_catalogue_jobs()

    r = api_env.client.get("/v1/jobs", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == src
    assert body["total"] == len(src)
    assert body["limit"] == 100
    assert body["offset"] == 0
    # No user-space key, and exactly the catalogue key set.
    assert set(body["items"][0].keys()) == CATALOGUE_JOB_KEYS
    assert not (set(body["items"][0].keys()) & _USER_SPACE_KEYS)


def test_catalogue_jobs_filter_and_pagination(api_env):
    tok = api_env.make_token(["read:catalogue"])
    src = api_env.db.get_catalogue_jobs(company_id=1)

    r = api_env.client.get("/v1/jobs", params={"company_id": 1}, headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["items"] == src
    assert r.json()["total"] == len(src)

    # Pagination: limit=1 slices, total stays the full filtered count.
    all_src = api_env.db.get_catalogue_jobs()
    r = api_env.client.get("/v1/jobs", params={"limit": 1}, headers=_auth(tok))
    assert r.json()["items"] == all_src[:1]
    assert r.json()["total"] == len(all_src)
    assert r.json()["limit"] == 1


def test_catalogue_job(api_env):
    tok = api_env.make_token(["read:catalogue"])
    src = api_env.db.get_catalogue_job("job-1")

    r = api_env.client.get("/v1/jobs/job-1", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == src


def test_catalogue_job_404(api_env):
    tok = api_env.make_token(["read:catalogue"])
    r = api_env.client.get("/v1/jobs/nope", headers=_auth(tok))
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Job not found"}


def test_catalogue_company_facts(api_env):
    tok = api_env.make_token(["read:catalogue"])
    src = api_env.db.get_company_facts(1)

    r = api_env.client.get("/v1/companies/1", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == src
    assert set(r.json().keys()) == COMPANY_FACT_KEYS
    assert not (set(r.json().keys()) & _USER_SPACE_KEYS)


def test_catalogue_company_404(api_env):
    tok = api_env.make_token(["read:catalogue"])
    r = api_env.client.get("/v1/companies/999", headers=_auth(tok))
    assert r.status_code == 404


# ── US3: read:me ─────────────────────────────────────────────────────────────

def test_me(api_env):
    tok = api_env.make_token(["read:me"])
    user = api_env.db.get_user()
    pid = api_env.db.get_active_profile_id()
    profiles = api_env.db.get_all_profiles()

    r = api_env.client.get("/v1/me", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == {
        "user": user,
        "active_profile_id": pid,
        "profiles": profiles,
    }
    assert r.json()["user"]["active_profile_id"] == pid


def test_me_feed(api_env):
    tok = api_env.make_token(["read:me"])
    pid = api_env.db.get_active_profile_id()
    src = api_env.db.get_all_for_tracker(pid)

    r = api_env.client.get("/v1/me/feed", headers=_auth(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == src
    assert body["total"] == len(src)


def test_me_feed_status_filter(api_env):
    tok = api_env.make_token(["read:me"])
    pid = api_env.db.get_active_profile_id()
    src = [r for r in api_env.db.get_all_for_tracker(pid) if r.get("status") == "applied"]

    r = api_env.client.get("/v1/me/feed", params={"status": "applied"}, headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["items"] == src
    assert r.json()["total"] == len(src)


def test_me_job_detail(api_env):
    tok = api_env.make_token(["read:me"])
    job = api_env.db.get_job_for_prepare("job-1")
    scores = api_env.db.get_user_scores_for_job("job-1")
    lifecycle = api_env.db.get_lifecycle_summary("job-1")
    events = api_env.db.get_lifecycle_events("job-1")
    application = api_env.db.get_application("job-1")

    r = api_env.client.get("/v1/me/jobs/job-1", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == {
        "job": job,
        "scores": scores,
        "lifecycle": lifecycle,
        "events": events,
        "application": application,
    }


def test_me_job_detail_404(api_env):
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get("/v1/me/jobs/nope", headers=_auth(tok))
    assert r.status_code == 404


def test_me_stats(api_env):
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get("/v1/me/stats", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == api_env.db.get_stats()


def test_me_dashboard(api_env):
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get("/v1/me/dashboard", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == api_env.db.get_dashboard_data()


def test_me_applications(api_env):
    tok = api_env.make_token(["read:me"])
    src = api_env.db.get_all_applications()
    r = api_env.client.get("/v1/me/applications", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["items"] == src
    assert r.json()["total"] == len(src)


def test_me_companies(api_env):
    tok = api_env.make_token(["read:me"])
    src = api_env.db.get_companies()
    r = api_env.client.get("/v1/me/companies", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["items"] == src
    assert r.json()["total"] == len(src)


def test_me_company_detail(api_env):
    tok = api_env.make_token(["read:me"])
    company = api_env.db.get_company_by_id(1)
    relationship = api_env.db.get_company_relationship_summary(1)
    contacts = api_env.db.get_company_contacts(1)
    interactions = api_env.db.get_company_interactions(1)

    r = api_env.client.get("/v1/me/companies/1", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == {
        "company": company,
        "relationship": relationship,
        "contacts": contacts,
        "interactions": interactions,
    }


def test_me_company_detail_404(api_env):
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get("/v1/me/companies/999", headers=_auth(tok))
    assert r.status_code == 404


def test_me_contacts(api_env):
    tok = api_env.make_token(["read:me"])
    src = api_env.db.get_all_contacts()
    r = api_env.client.get("/v1/me/contacts", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["items"] == src
    assert r.json()["total"] == len(src)


def test_me_contact_detail(api_env):
    tok = api_env.make_token(["read:me"])
    contact = api_env.db.get_contact(1)
    interactions = api_env.db.get_contact_interactions(1)

    r = api_env.client.get("/v1/me/contacts/1", headers=_auth(tok))
    assert r.status_code == 200
    assert r.json() == {"contact": contact, "interactions": interactions}


def test_me_contact_detail_404(api_env):
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get("/v1/me/contacts/999", headers=_auth(tok))
    assert r.status_code == 404


# ── scope enforcement (cross-story) ──────────────────────────────────────────

def test_me_scope_rejected_for_catalogue_token(api_env):
    tok = api_env.make_token(["read:catalogue"])
    r = api_env.client.get("/v1/me", headers=_auth(tok))
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


def test_catalogue_scope_rejected_for_me_token(api_env):
    tok = api_env.make_token(["read:me"])
    r = api_env.client.get("/v1/jobs", headers=_auth(tok))
    assert r.status_code == 403
