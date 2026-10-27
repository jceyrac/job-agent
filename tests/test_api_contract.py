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
    assert set(body.keys()) == {"status", "version", "purge_last_success", "warnings"}
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert body["purge_last_success"] is None          # seeded DB has no purge history
    assert body["warnings"] == ["no successful purge recorded"]


def test_health_warns_when_purge_stale(api_env):
    from datetime import datetime, timedelta, timezone
    stale = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    api_env.db.set_config("purge.last_success", stale)
    r = api_env.client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["purge_last_success"] == stale
    assert "last successful purge >3d ago" in body["warnings"]


def test_health_no_warning_when_purge_recent(api_env):
    from datetime import datetime, timezone
    api_env.db.set_config("purge.last_success", datetime.now(timezone.utc).isoformat())
    r = api_env.client.get("/health")
    body = r.json()
    assert body["purge_last_success"] is not None
    assert body["warnings"] == []


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


# ── spec 042 write contracts (US1 — jobs & lifecycle) ────────────────────────
#
# For each route, drive the API on one copy of the seeded DB and the matching
# `JobStorage` method(s) on an identical copy, then assert the user-space rows
# (`job_tracking` / `status_history` / `interactions`) are byte-identical.
# `changed_at`/`created_at` are wall-clock (`_now()`) and differ between the two
# writes, so they are dropped from the comparison.

import shutil
import sqlite3
from datetime import date

from core.profiles import SearchProfile
from core.storage import JobStorage


def _copy_db(src_path, tmp_path, name):
    dest = str(tmp_path / name)
    shutil.copy(src_path, dest)
    return dest


def _write_snapshot(path):
    """User-space rows a write touches, ordered deterministically, timestamps dropped."""
    conn = sqlite3.connect(path)
    snap = {
        "job_tracking": [
            tuple(r)
            for r in conn.execute(
                "SELECT user_id, job_id, status, notes FROM job_tracking ORDER BY user_id, job_id"
            )
        ],
        "status_history": [
            tuple(r)
            for r in conn.execute(
                "SELECT user_id, job_id, status FROM status_history ORDER BY id"
            )
        ],
        "interactions": [
            tuple(r)
            for r in conn.execute(
                "SELECT user_id, company_id, job_id, type, direction, outcome, "
                "notes, source, source_ref FROM interactions ORDER BY id"
            )
        ],
    }
    conn.close()
    return snap


def test_write_status_lifecycle_contract(api_env, tmp_path):
    """A lifecycle status goes through `record_lifecycle_event` (not `set_status`),
    so the event is dated by the client and `source='manual'` — byte-identical to
    the tracker's `_confirm_lifecycle` mapping (FR-002)."""
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    r = api_env.client.put(
        "/v1/me/jobs/job-2/status",
        json={"status": "applied", "occurred_at": "2026-01-05"},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "applied"

    direct.record_lifecycle_event(
        "job-2", event="application_submitted", occurred_at="2026-01-05", new_status="applied"
    )

    assert _write_snapshot(api_env.path) == _write_snapshot(direct_path)

    # No today-dated back-fill: the only lifecycle event is the client's date.
    events = api_env.db.get_lifecycle_events("job-2")
    assert [e["occurred_at"] for e in events] == ["2026-01-05"]
    assert all(e["occurred_at"] != date.today().isoformat() for e in events)


def test_write_status_nonlifecycle_contract(api_env, tmp_path):
    """Non-lifecycle statuses still go through `set_status` (no date required)."""
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    r = api_env.client.put(
        "/v1/me/jobs/job-1/status",
        json={"status": "queued", "notes": "via api"},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "queued"

    direct.set_status("job-1", "queued", "via api")

    assert _write_snapshot(api_env.path) == _write_snapshot(direct_path)


def test_write_status_lifecycle_requires_occurred_at_422(api_env):
    tok = api_env.make_token(["write:me"])
    r = api_env.client.put(
        "/v1/me/jobs/job-2/status", json={"status": "applied"}, headers=_auth(tok)
    )
    assert r.status_code == 422
    assert r.json() == {"error": "validation_error", "message": "Invalid request"}


def test_write_event_create_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    body = {"event": "interview_invited", "occurred_at": "2026-02-01", "new_status": "interviewing"}
    r = api_env.client.post("/v1/me/jobs/job-1/events", json=body, headers=_auth(tok))
    assert r.status_code == 200
    # `occurred_at` passes through verbatim — the API never defaults it to "now".
    assert any(
        e["type"] == "interview_invited" and e["occurred_at"] == "2026-02-01"
        for e in r.json()
    )

    direct.record_lifecycle_event(
        "job-1", event="interview_invited", occurred_at="2026-02-01", new_status="interviewing"
    )

    assert _write_snapshot(api_env.path) == _write_snapshot(direct_path)


def test_write_event_patch_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    event_id = api_env.db.get_lifecycle_events("job-1")[0]["id"]

    r = api_env.client.patch(
        f"/v1/me/jobs/job-1/events/{event_id}",
        json={"notes": "patched"},
        headers=_auth(tok),
    )
    assert r.status_code == 200

    direct.update_lifecycle_event(event_id, notes="patched")
    direct.reconcile_lifecycle_status("job-1")

    assert _write_snapshot(api_env.path) == _write_snapshot(direct_path)


def test_write_event_delete_contract(api_env, tmp_path):
    # Delete the event that drives `interviewing` → reconcile reverts to `applied`.
    tok = api_env.make_token(["write:me"])
    api_env.db.record_lifecycle_event(
        "job-1", event="interview_invited", occurred_at="2026-02-01", new_status="interviewing"
    )
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    event_id = next(
        e["id"] for e in api_env.db.get_lifecycle_events("job-1") if e["type"] == "interview_invited"
    )

    r = api_env.client.delete(
        f"/v1/me/jobs/job-1/events/{event_id}", headers=_auth(tok)
    )
    assert r.status_code == 204

    direct.delete_lifecycle_event(event_id)
    direct.reconcile_lifecycle_status("job-1")

    assert _write_snapshot(api_env.path) == _write_snapshot(direct_path)


def test_write_event_create_missing_occurred_at_422(api_env):
    tok = api_env.make_token(["write:me"])
    r = api_env.client.post(
        "/v1/me/jobs/job-1/events",
        json={"event": "interview_invited", "new_status": "interviewing"},
        headers=_auth(tok),
    )
    assert r.status_code == 422
    assert r.json() == {"error": "validation_error", "message": "Invalid request"}


def test_write_status_unknown_job_404(api_env):
    tok = api_env.make_token(["write:me"])
    r = api_env.client.put(
        "/v1/me/jobs/nope/status",
        json={"status": "applied", "occurred_at": "2026-01-05"},
        headers=_auth(tok),
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Job not found"}


def test_write_event_unknown_event_404(api_env):
    tok = api_env.make_token(["write:me"])
    r = api_env.client.patch(
        "/v1/me/jobs/job-1/events/9999", json={"notes": "x"}, headers=_auth(tok)
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Event not found"}


# ── spec 044 write contract — application (PUT /v1/me/jobs/{id}/application) ──

def _application_snapshot(path):
    """`job_applications` + the `ready` status `save_application` sets."""
    conn = sqlite3.connect(path)
    snap = {
        "job_applications": [
            tuple(r)
            for r in conn.execute(
                "SELECT user_id, job_id, analysis, cover_letter "
                "FROM job_applications ORDER BY user_id, job_id"
            )
        ],
        "job_tracking": [
            tuple(r)
            for r in conn.execute(
                "SELECT user_id, job_id, status FROM job_tracking ORDER BY user_id, job_id"
            )
        ],
    }
    conn.close()
    return snap


def test_write_application_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    body = {"analysis": "strong fit", "cover_letter": "Dear Acme,\n\n…"}
    r = api_env.client.put("/v1/me/jobs/job-2/application", json=body, headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["analysis"] == "strong fit"
    assert r.json()["cover_letter"] == "Dear Acme,\n\n…"
    assert r.json()["job_id"] == "job-2"

    direct.save_application("job-2", "strong fit", "Dear Acme,\n\n…")

    assert _application_snapshot(api_env.path) == _application_snapshot(direct_path)


def test_write_application_unknown_job_404(api_env):
    tok = api_env.make_token(["write:me"])
    r = api_env.client.put(
        "/v1/me/jobs/nope/application",
        json={"analysis": "a", "cover_letter": "c"}, headers=_auth(tok),
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Job not found"}


def test_write_application_requires_write_me(api_env):
    tok = api_env.make_token(["read:me"])
    r = api_env.client.put(
        "/v1/me/jobs/job-2/application",
        json={"analysis": "a", "cover_letter": "c"}, headers=_auth(tok),
    )
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}


# ── spec 042 write contracts (US2 — companies, contacts, interactions) ───────

def _us2_snapshot(path):
    """Company-relationship / contact / interaction rows, timestamps dropped."""
    conn = sqlite3.connect(path)
    snap = {
        "user_companies": [
            tuple(r)
            for r in conn.execute(
                "SELECT user_id, company_id, status, notes, monitored FROM user_companies "
                "ORDER BY user_id, company_id"
            )
        ],
        "company_status_history": [
            tuple(r)
            for r in conn.execute(
                "SELECT user_id, company_id, status, note FROM company_status_history ORDER BY id"
            )
        ],
        "contacts": [
            tuple(r)
            for r in conn.execute(
                "SELECT user_id, company_id, first_name, last_name, full_name, role_title, "
                "role_family, seniority, email, email_status, email_confidence, linkedin_url, "
                "x_handle, telegram_handle, github_handle, phone, is_current, is_unverified, "
                "last_verified_at, notes FROM contacts ORDER BY id"
            )
        ],
        "interactions": [
            tuple(r)
            for r in conn.execute(
                "SELECT user_id, company_id, contact_id, job_id, type, direction, outcome, "
                "subject, body_excerpt, follow_up_due_at, notes, source, source_ref "
                "FROM interactions ORDER BY id"
            )
        ],
    }
    conn.close()
    return snap


def test_write_company_relationship_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    r = api_env.client.patch(
        "/v1/me/companies/1",
        json={"status": "blacklisted", "notes": "nope", "monitored": True},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    assert r.json()["company"]["status"] == "blacklisted"
    assert r.json()["company"]["monitored"] == 1
    assert r.json()["company"]["notes"] == "nope"

    direct.set_company_status(1, "blacklisted")
    direct.update_company_fields(1, {"notes": "nope"})
    direct.set_company_monitored(1, True)

    assert _us2_snapshot(api_env.path) == _us2_snapshot(direct_path)


def test_write_contact_create_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    body = {
        "company_id": 2,
        "first_name": "Jane",
        "last_name": "Roe",
        "email": "jane@example.com",
        "linkedin_url": "https://linkedin.com/in/jane",
    }
    r = api_env.client.post("/v1/me/contacts", json=body, headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["first_name"] == "Jane"

    direct.upsert_contact(
        company_id=2, first_name="Jane", last_name="Roe",
        email="jane@example.com", linkedin_url="https://linkedin.com/in/jane",
    )

    assert _us2_snapshot(api_env.path) == _us2_snapshot(direct_path)


def test_write_contact_upsert_merge(api_env):
    tok = api_env.make_token(["write:me"])
    body = {"company_id": 1, "linkedin_url": "https://linkedin.com/in/john", "first_name": "John"}
    r1 = api_env.client.post("/v1/me/contacts", json=body, headers=_auth(tok))
    assert r1.status_code == 200
    cid = r1.json()["id"]

    # Same linkedin_url → merge into the same id (no 409, no new row).
    r2 = api_env.client.post(
        "/v1/me/contacts",
        json={"company_id": 1, "linkedin_url": "https://linkedin.com/in/john", "last_name": "Doe"},
        headers=_auth(tok),
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == cid
    assert len(api_env.db.get_all_contacts()) == 2  # seeded + this one, no duplicate


def test_write_contact_update_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    r = api_env.client.patch(
        "/v1/me/contacts/1", json={"notes": "updated", "is_unverified": True}, headers=_auth(tok)
    )
    assert r.status_code == 200

    direct.update_contact_fields(1, {"notes": "updated", "is_unverified": True})

    assert _us2_snapshot(api_env.path) == _us2_snapshot(direct_path)


def test_write_contact_interaction_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    body = {"type": "outreach_sent", "direction": "outbound", "occurred_at": "2026-03-01"}
    r = api_env.client.post("/v1/me/contacts/1/interactions", json=body, headers=_auth(tok))
    assert r.status_code == 201

    direct.log_interaction(
        company_id=1, contact_id=1, type="outreach_sent", direction="outbound",
        occurred_at="2026-03-01",
    )

    assert _us2_snapshot(api_env.path) == _us2_snapshot(direct_path)


def test_write_company_interaction_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    body = {"type": "call", "direction": "outbound", "occurred_at": "2026-03-02"}
    r = api_env.client.post("/v1/me/companies/1/interactions", json=body, headers=_auth(tok))
    assert r.status_code == 201

    direct.log_interaction(company_id=1, contact_id=None, type="call", direction="outbound", occurred_at="2026-03-02")

    assert _us2_snapshot(api_env.path) == _us2_snapshot(direct_path)


def test_write_contact_unknown_company_404(api_env):
    tok = api_env.make_token(["write:me"])
    r = api_env.client.post(
        "/v1/me/contacts", json={"company_id": 999, "first_name": "X"}, headers=_auth(tok)
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Company not found"}


# ── spec 042 write contracts (US3 — profile) ──────────────────────────────────

def _profile_snapshot(path):
    """Profile rows + active-profile pointer + score rows (should not change)."""
    conn = sqlite3.connect(path)
    snap = {
        "search_profiles": [
            tuple(r)
            for r in conn.execute(
                "SELECT id, user_id, name, criteria FROM search_profiles ORDER BY id"
            )
        ],
        "active_profile": [
            tuple(r)
            for r in conn.execute("SELECT id, active_profile_id FROM users ORDER BY id")
        ],
        "job_scores": [
            tuple(r)
            for r in conn.execute(
                "SELECT job_id, profile_id, score, reason, scored_by, scored_at "
                "FROM job_scores ORDER BY job_id, profile_id"
            )
        ],
    }
    conn.close()
    return snap


def test_write_active_profile_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    r = api_env.client.put(
        "/v1/me/active-profile", json={"profile_id": "prof-b"}, headers=_auth(tok)
    )
    assert r.status_code == 204

    direct.set_active_profile_id("prof-b")

    assert _profile_snapshot(api_env.path) == _profile_snapshot(direct_path)


def test_write_profile_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:me"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    body = {"name": "Renamed", "criteria": {"allowed_geo_zones": ["fr"], "job_titles": ["product manager"]}}
    r = api_env.client.put("/v1/me/profiles/prof-a", json=body, headers=_auth(tok))
    assert r.status_code == 200
    assert r.json()["name"] == "Renamed"

    direct.upsert_profile(SearchProfile.from_criteria("prof-a", "Renamed", body["criteria"]))

    # Identical rows — and no score rows were written (scoring is spec 043).
    assert _profile_snapshot(api_env.path) == _profile_snapshot(direct_path)


def test_write_profile_unknown_404(api_env):
    tok = api_env.make_token(["write:me"])

    # Missing profile → 404 for both routes (no create-with-new-id, no 403).
    assert (
        api_env.client.put(
            "/v1/me/active-profile", json={"profile_id": "nope"}, headers=_auth(tok)
        ).status_code
        == 404
    )
    assert (
        api_env.client.put(
            "/v1/me/profiles/nope", json={"name": "X", "criteria": {}}, headers=_auth(tok)
        ).status_code
        == 404
    )


# ── spec 042 write contracts (US4 — catalogue company facts) ─────────────────

def _company_facts_snapshot(path):
    conn = sqlite3.connect(path)
    snap = [
        tuple(r)
        for r in conn.execute(
            "SELECT id, name, website, company_country, industry_sector, company_size, "
            "x_handle, careers_url, ats_provider, ats_identifier, scraping_method, "
            "research_notes FROM companies ORDER BY id"
        )
    ]
    conn.close()
    return snap


def test_write_company_facts_contract(api_env, tmp_path):
    tok = api_env.make_token(["write:catalogue"])
    direct_path = _copy_db(api_env.path, tmp_path, "direct.db")
    direct = JobStorage(direct_path, user_id="user-a")

    r = api_env.client.patch(
        "/v1/companies/1",
        json={"website": "https://alpha.example.com", "company_size": "scaleup"},
        headers=_auth(tok),
    )
    assert r.status_code == 200
    assert r.json()["website"] == "https://alpha.example.com"

    direct.update_company_fields(
        1, {"website": "https://alpha.example.com", "company_size": "scaleup"}
    )

    assert _company_facts_snapshot(api_env.path) == _company_facts_snapshot(direct_path)


def test_write_company_facts_unknown_company_404(api_env):
    tok = api_env.make_token(["write:catalogue"])
    r = api_env.client.patch(
        "/v1/companies/999", json={"website": "https://x.example.com"}, headers=_auth(tok)
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "Company not found"}


def test_write_company_facts_reject_relationship_field_422(api_env):
    # `notes`/`status`/`monitored` are user-space, not catalogue facts → 422.
    tok = api_env.make_token(["write:catalogue"])
    r = api_env.client.patch("/v1/companies/1", json={"notes": "x"}, headers=_auth(tok))
    assert r.status_code == 422
    assert r.json() == {"error": "validation_error", "message": "Invalid request"}


# ── spec 042 write safety (US4 — body cap, validation, write log) ────────────

def test_write_body_over_64kb_413(api_env):
    tok = api_env.make_token(["write:me"])
    big = {"status": "applied", "notes": "x" * 70000}
    r = api_env.client.put("/v1/me/jobs/job-1/status", json=big, headers=_auth(tok))
    assert r.status_code == 413
    assert r.json() == {"error": "payload_too_large", "message": "Request body too large"}


def test_write_unknown_field_422(api_env):
    tok = api_env.make_token(["write:me"])
    r = api_env.client.put(
        "/v1/me/jobs/job-1/status", json={"status": "applied", "bogus": 1}, headers=_auth(tok)
    )
    assert r.status_code == 422
    assert r.json() == {"error": "validation_error", "message": "Invalid request"}


def test_write_log_emits_one_line_no_body_or_token(api_env, api_log_stream):
    raw = api_env.make_token(["write:me"])
    row = api_env.db.resolve_api_token(raw)

    r = api_env.client.put(
        "/v1/me/jobs/job-1/status",
        json={"status": "queued", "notes": "secret-body"},
        headers=_auth(raw),
    )
    assert r.status_code == 200

    text = api_log_stream.getvalue()
    lines = [ln for ln in text.splitlines() if "write_ok" in ln]
    assert len(lines) == 1
    assert f"token_id={row['id']}" in lines[0]
    assert "user_id=user-a" in lines[0]
    assert "status=200" in lines[0]
    assert "secret-body" not in text
    assert raw not in text
    assert "Authorization" not in text


def test_api_logging_wiring_emits_write_and_auth_lines(api_env, api_log_stream):
    """FR-007 + FR-014 wiring: after app startup, a write and a failed auth each
    land on the configured `api.*` handler (stdout in prod), and no line carries
    the Authorization header, the token, or the request body."""
    raw = api_env.make_token(["write:me"])

    assert (
        api_env.client.put(
            "/v1/me/jobs/job-1/status",
            json={"status": "queued", "notes": "secret-body"},
            headers=_auth(raw),
        ).status_code
        == 200
    )
    assert api_env.client.get("/v1/me", headers=_auth("bogus-token")).status_code == 401

    text = api_log_stream.getvalue()
    assert "write_ok" in text
    assert "auth_failed" in text
    assert "Authorization" not in text
    assert raw not in text
    assert "bogus-token" not in text
    assert "secret-body" not in text
