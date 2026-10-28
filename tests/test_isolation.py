"""
tests/test_isolation.py — two-user isolation suite (spec 039, US2 / FR-012).

Two ``JobStorage`` instances bound to different users over the SAME DB file:
drive every public user-space method as A, assert B sees none of A's data
(and vice versa). A module-level ``USER_SPACE_METHODS`` list plus a coverage
meta-test ensures no user-space method can slip through unclassified.
"""
import inspect
import re
import sqlite3
from types import SimpleNamespace

import pytest

from core.storage import JobStorage, ProfileNotFoundError

# Public user-space methods (read filter on `self.user_id`, write set it).
# Transcribed from plan.md §3a–§3g; the coverage test below guards completeness.
USER_SPACE_METHODS = (
    # search_profiles
    "upsert_profile", "get_profile", "get_all_profiles", "delete_profile",
    # job_scores (user space via search_profiles.user_id — spec 041)
    "get_score_result", "save_scored", "delete_scores_for_profile",
    "get_best_score_profile_id",
    # job_tracking / status_history
    "set_status", "record_lifecycle_event", "reconcile_lifecycle_status",
    "get_tracking_status", "get_digest", "get_stats", "get_job_for_prepare",
    "get_user_scores_for_job",
    "get_jobs_for_company", "get_all_jobs_best_score", "get_all_for_tracker",
    "get_cohort_jobs", "get_classified_jobs", "find_jobs_by_company",
    "get_jobs_for_scoring", "get_queued_jobs_unprepared", "get_queued_jobs",
    "get_calibration_signals", "get_calibration_deltas", "get_archived_notes",
    "get_few_shot_anchors", "get_action_item_candidates",
    # job_applications
    "get_application", "get_all_applications", "get_applications_count",
    "save_application", "save_prepared_application",
    # contacts
    "upsert_contact", "get_company_contacts", "get_contact", "get_all_contacts",
    "update_contact_fields", "bulk_mark_contacts_verified",
    "get_contacts_discovered_for_job", "get_contacts_by_role_family",
    # interactions
    "log_interaction", "get_lifecycle_events", "get_lifecycle_summary",
    "update_lifecycle_event", "delete_lifecycle_event",
    "get_contact_relationship_status", "get_company_interactions",
    "get_contact_interactions", "build_application_rows", "build_activity_rows",
    # company relationship (user_companies)
    "set_company_status", "set_company_monitored", "set_monitoring_status",
    "update_company_fields", "get_companies", "get_company_by_id",
    "get_all_monitorable_companies",
    "count_blacklisted_jobs", "get_company_relationship_summary",
    "application_date_missing",
    # aggregate
    "get_dashboard_data", "get_database_stats",
    # user identity / settings accessors
    "get_user",
    "get_active_profile_id", "set_active_profile_id",
    "get_onboarding_completed", "set_onboarding_completed",
    # company import (writes user_companies / company_status_history)
    "import_companies_from_csv",
    # tasks (spec 043) — user-scoped queue reads/writes
    "enqueue_task", "get_task", "list_tasks", "get_active_task",
    "mark_cancelled", "request_cancel",
)

# Deliberately unfiltered (cross-user) — plan.md §5 / FR-010, FR-011.
CROSS_USER_METHODS = (
    "purge_stale_jobs", "count_purgeable_jobs", "get_engaged_job_keys",
    "get_monitored_companies", "get_watching_companies_by_method",
    "get_watch_pending_companies", "get_companies_by_ats_provider",
    # Catalogue seed writer that reads user_companies for the union watch guard
    # (Constitution VIII) — cross-user by design.
    "upsert_company_from_seed",
    # API token auth/management (spec 041) — keyed by token_id/user_id, not the
    # bound user's data view; the CLI is the only creator/revoker.
    "create_api_token", "list_api_tokens", "revoke_api_token",
    "resolve_api_token", "touch_api_token",
    # Task worker lifecycle (spec 043) — the worker is the single consumer for
    # every user's queue; these act on a task_id (or global/config state) without
    # the bound user's filter.
    "claim_next_task", "append_task_events", "get_task_events", "finish_task",
    "is_cancel_requested", "recover_interrupted", "prune_task_history",
    "get_worker_heartbeat", "set_worker_heartbeat",
    "get_pipeline_lock_holder", "set_pipeline_lock_holder",
)

# Tokens that must never appear in an unclassified (catalogue) method's source.
_USER_SPACE_TOKENS = (
    "self.user_id", "job_tracking", "job_applications", "search_profiles",
    "status_history", "company_status_history", "user_companies", "job_scores",
)

# User-space methods that never reference `user_id` / a table name directly:
# either they derive purely from another user-space method, or they delegate the
# user-binding to `_require_profile` (the profile-ownership check — spec 041).
_DELEGATING_METHODS = {
    "get_lifecycle_summary", "get_score_result", "save_scored",
    "delete_scores_for_profile",
}


def _strip_docstring(src: str) -> str:
    """Drop the leading method docstring so table names in prose don't false-positive."""
    return re.sub(r'"""(?:.|\n)*?"""', "", src, count=1)


def _public_methods() -> set[str]:
    return {
        m for m in dir(JobStorage)
        if not m.startswith("_") and callable(getattr(JobStorage, m))
    }


@pytest.fixture
def two_users(tmp_path):
    """Shared DB file with two bound users and shared catalogue rows."""
    path = str(tmp_path / "shared.db")
    JobStorage(path)  # run the one-shot migration once

    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO users (id, display_name, active_profile_id, created_at) VALUES ('user-a', 'A', 'prof-a', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, display_name, active_profile_id, created_at) VALUES ('user-b', 'B', 'prof-b', '2026-01-01')"
    )
    # Catalogue rows (shared by both users).
    conn.execute(
        "INSERT INTO companies (name, name_normalized, status, first_seen_at, last_seen_at, created_at) "
        "VALUES ('Alpha', 'alpha', 'prospect', '2026-01-01', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO companies (name, name_normalized, status, first_seen_at, last_seen_at, created_at) "
        "VALUES ('Beta', 'beta', 'prospect', '2026-01-01', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO jobs (id, title, company, company_id, source, first_seen, last_seen) "
        "VALUES ('job-1', 'Backend', 'Alpha', 1, 'test', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO jobs (id, title, company, company_id, source, first_seen, last_seen) "
        "VALUES ('job-2', 'Frontend', 'Beta', 2, 'test', '2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    a = JobStorage(path, user_id="user-a")
    b = JobStorage(path, user_id="user-b")
    return a, b


def _profile(pid: str = "prof-a"):
    return SimpleNamespace(
        id=pid, name=f"Profile {pid}",
        to_criteria_dict=lambda: {"geo_zones": ["ch"]},
    )


# ── Coverage (FR-012) ─────────────────────────────────────────────────────────

def test_user_space_methods_cover_full_set():
    public = _public_methods()
    classified = set(USER_SPACE_METHODS) | set(CROSS_USER_METHODS)

    # Every classified name is a real public method.
    assert classified <= public, f"stale names: {classified - public}"
    # No overlap between the two lists.
    assert not (set(USER_SPACE_METHODS) & set(CROSS_USER_METHODS))

    # Every unclassified (catalogue) method must not touch a user-space table.
    for name in sorted(public - classified):
        src = _strip_docstring(inspect.getsource(getattr(JobStorage, name)))
        for token in _USER_SPACE_TOKENS:
            assert token not in src, (
                f"{name} references user-space '{token}' but is not in "
                f"USER_SPACE_METHODS / CROSS_USER_METHODS"
            )


def test_user_space_methods_all_actually_bind_user():
    # Every listed user-space method must reference the bound user (directly, or
    # via a delegated helper) — guards against listing a catalogue method by mistake.
    for name in USER_SPACE_METHODS:
        if name in _DELEGATING_METHODS:
            continue
        src = inspect.getsource(getattr(JobStorage, name))
        assert "self.user_id" in src, (
            f"{name} is listed user-space but never references self.user_id"
        )


# ── Isolation: job_tracking / status ──────────────────────────────────────────

def test_set_status_isolated(two_users):
    a, b = two_users
    b.upsert_profile(_profile("prof-b"))
    a.set_status("job-1", "applied", "note from A")

    assert a.get_tracking_status("job-1") == "applied"
    assert b.get_tracking_status("job-1") is None
    # B still sees job-1 (catalogue) but without A's tracking status.
    b_feed = b.get_all_for_tracker("prof-b")
    b_job = next(j for j in b_feed if j["id"] == "job-1")
    assert b_job["tracked_status"] is None
    assert b.get_digest("prof-b", status="applied") == []


def test_status_writer_is_bidirectional(two_users):
    a, b = two_users
    a.set_status("job-1", "rejected")
    b.set_status("job-2", "applied")

    # Each user sees only their own tracking.
    assert a.get_tracking_status("job-2") is None
    assert b.get_tracking_status("job-1") is None


# ── Isolation: job_applications ───────────────────────────────────────────────

def test_applications_isolated(two_users):
    a, b = two_users
    a.save_application("job-1", "A analysis", "A letter")

    assert a.get_application("job-1") is not None
    assert b.get_application("job-1") is None
    assert b.get_all_applications() == []
    assert a.get_applications_count() == 1
    assert b.get_applications_count() == 0


def test_prepared_application_isolated(two_users):
    a, b = two_users
    a.upsert_profile(_profile("prof-a"))
    a.save_prepared_application(
        "job-1", "prof-a", "cover", {"cv": "x"}, {"co": "y"}, {"s": "z"}, "en", "A",
    )
    assert a.get_application("job-1") is not None
    assert b.get_application("job-1") is None


# ── Isolation: contacts ───────────────────────────────────────────────────────

def test_contacts_isolated(two_users):
    a, b = two_users
    contact_id = a.upsert_contact(
        company_id=1, linkedin_url="https://linkedin.com/in/a",
        first_name="Alice", email="alice@example.com",
    )

    assert a.get_contact(contact_id) is not None
    assert b.get_contact(contact_id) is None
    assert b.get_all_contacts() == []
    assert b.get_company_contacts(1) == []
    assert a.get_company_contacts(1) != []


# ── Isolation: interactions ───────────────────────────────────────────────────

def test_interactions_isolated(two_users):
    a, b = two_users
    a.log_interaction(company_id=1, type="outreach_sent", job_id="job-1", direction="outbound")

    assert b.get_company_interactions(1) == []
    assert b.get_contact_interactions(1) == []
    assert a.get_company_interactions(1) != []


def test_lifecycle_events_isolated(two_users):
    a, b = two_users
    a.set_status("job-1", "applied")  # logs application lifecycle via auto-log

    assert b.get_lifecycle_events("job-1") == []
    assert a.get_lifecycle_events("job-1") != []


# ── Isolation: search_profiles ────────────────────────────────────────────────

def test_profiles_isolated(two_users):
    a, b = two_users
    a.upsert_profile(_profile("prof-a"))

    assert a.get_profile("prof-a") is not None
    assert b.get_profile("prof-a") is None
    assert b.get_all_profiles() == []
    assert a.get_all_profiles() != []


def test_delete_profile_scoped(two_users):
    a, b = two_users
    a.upsert_profile(_profile("prof-a"))
    b.upsert_profile(_profile("prof-b"))

    a.delete_profile("prof-a")
    assert a.get_profile("prof-a") is None
    assert b.get_profile("prof-b") is not None  # B's profile untouched


# ── Isolation: user-scoped scores (spec 041) ─────────────────────────────────

def test_user_scores_for_job_isolated(two_users):
    a, b = two_users
    a.upsert_profile(_profile("prof-a"))
    b.upsert_profile(_profile("prof-b"))

    # Write a score under each user's own profile directly (job_scores is keyed
    # by profile_id, not user_id, so we seed through the two profiles).
    with sqlite3.connect(str(a.db_path)) as c:
        c.execute(
            "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
            "VALUES ('job-1', 'prof-a', 9, 'a', 'model', '2026-01-02')"
        )
        c.execute(
            "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
            "VALUES ('job-1', 'prof-b', 2, 'b', 'model', '2026-01-02')"
        )
        c.commit()

    # Each user sees only their own profile's score for the same shared job.
    a_scores = a.get_user_scores_for_job("job-1")
    b_scores = b.get_user_scores_for_job("job-1")
    assert [s["profile_id"] for s in a_scores] == ["prof-a"]
    assert [s["profile_id"] for s in b_scores] == ["prof-b"]
    assert a_scores[0]["score"] == 9
    assert b_scores[0]["score"] == 2


def test_profile_scoped_methods_reject_foreign_profile(two_users):
    """A profile_id belonging to another user raises ``ProfileNotFoundError`` —
    never returns or writes that user's data (spec 041)."""
    a, b = two_users
    a.upsert_profile(_profile("prof-a"))
    b.upsert_profile(_profile("prof-b"))

    calls = [
        ("get_score_result", lambda: a.get_score_result("job-1", "prof-b")),
        ("split_new_cached", lambda: a.split_new_cached([], "prof-b")),
        ("get_jobs_for_scoring", lambda: a.get_jobs_for_scoring("prof-b")),
        ("save_scored", lambda: a.save_scored(None, {"score": 5}, "prof-b")),
        ("get_digest", lambda: a.get_digest("prof-b")),
        ("get_stats", lambda: a.get_stats("prof-b")),
        ("get_all_for_tracker", lambda: a.get_all_for_tracker("prof-b")),
        ("get_calibration_signals", lambda: a.get_calibration_signals("prof-b")),
        ("delete_scores_for_profile", lambda: a.delete_scores_for_profile("prof-b")),
        ("get_classified_jobs", lambda: a.get_classified_jobs("prof-b")),
        ("get_few_shot_anchors", lambda: a.get_few_shot_anchors("prof-b")),
        ("get_action_item_candidates", lambda: a.get_action_item_candidates("prof-b")),
        ("save_prepared_application",
         lambda: a.save_prepared_application("job-1", "prof-b", "cover", None, None, None, "en", "A")),
        ("delete_profile", lambda: a.delete_profile("prof-b")),
    ]

    for name, call in calls:
        with pytest.raises(ProfileNotFoundError, match="prof-b"):
            call()

    # The rejected writes left B's profile (and its data) untouched.
    assert b.get_profile("prof-b") is not None


def test_missing_profile_returns_empty_not_error(tmp_path):
    """A ``profile_id`` with no ``search_profiles`` row is not a cross-user leak:
    profile-scoped reads return empty results, so the app still works on a fresh
    install before any profile is seeded (Constitution IX). Only a profile owned
    by another user raises ``ProfileNotFoundError``."""
    db = JobStorage(str(tmp_path / "fresh.db"))  # default user only, no profile

    assert db.get_all_for_tracker("unified_jc") == []
    assert db.get_digest("unified_jc") == []
    assert db.get_jobs_for_scoring("unified_jc") == []
    assert db.get_score_result("job-1", "unified_jc") is None
    assert db.get_best_score_profile_id("job-1") is None
    stats = db.get_stats("unified_jc")
    assert stats["total"] == 0 and stats["scored"] == 0


def test_aggregates_user_scoped(two_users):
    """``get_stats(None)``, ``get_best_score_profile_id`` and ``get_database_stats``
    count only the bound user's profiles (``job_scores`` is user space)."""
    a, b = two_users
    a.upsert_profile(_profile("prof-a"))
    b.upsert_profile(_profile("prof-b"))

    # A scores job-1 (9) and job-2 (7); B scores only job-1 (2).
    with sqlite3.connect(str(a.db_path)) as c:
        c.execute(
            "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
            "VALUES ('job-1', 'prof-a', 9, 'a', 'model', '2026-01-02')"
        )
        c.execute(
            "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
            "VALUES ('job-2', 'prof-a', 7, 'a', 'model', '2026-01-02')"
        )
        c.execute(
            "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
            "VALUES ('job-1', 'prof-b', 2, 'b', 'model', '2026-01-02')"
        )
        c.commit()

    a_stats = a.get_stats(None)
    b_stats = b.get_stats(None)
    assert (a_stats["scored"], a_stats["hot"], a_stats["solid"]) == (2, 1, 1)
    assert (b_stats["scored"], b_stats["hot"], b_stats["solid"]) == (1, 0, 0)

    assert a.get_best_score_profile_id("job-1") == "prof-a"
    assert b.get_best_score_profile_id("job-1") == "prof-b"

    assert a.get_database_stats()["total_scores"] == 2
    assert b.get_database_stats()["total_scores"] == 1
    assert a.get_database_stats()["distinct_scored"] == 2
    assert b.get_database_stats()["distinct_scored"] == 1


# ── Isolation: company relationship (user_companies) ──────────────────────────

def test_company_status_isolated(two_users):
    a, b = two_users
    a.set_company_status(1, "blacklisted", "no thanks")

    # B still sees the default relationship for the same company.
    assert b.get_company_by_id(1)["status"] == "prospect"
    assert b.count_blacklisted_jobs() == 0
    assert a.count_blacklisted_jobs() >= 1


def test_company_monitored_isolated(two_users):
    a, b = two_users
    a.set_company_monitored(1, True)

    # The monitoring READERS are cross-user (union) by design (FR-010), but the
    # per-user relationship column must not leak into B's company view.
    assert b.get_company_by_id(1)["monitored"] in (0, False, None)
    assert a.get_company_by_id(1)["monitored"] == 1


def test_monitoring_targets_follow_user_companies_not_ghost_column(tmp_path):
    """Scrape target selection reads user_companies.monitored, not the legacy
    companies.monitored ghost: watching/unwatching moves a company in/out of the
    set even when the ghost column is stuck at 1."""
    path = str(tmp_path / "monitor.db")
    JobStorage(path)  # run the one-shot migration

    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO users (id, display_name, created_at) VALUES ('user-a', 'A', '2026-01-01')"
    )
    # watch_ready capability, but the legacy companies.monitored ghost is 1 while
    # NO user has requested a watch yet.
    conn.execute(
        "INSERT INTO companies (name, name_normalized, monitoring_status, monitored, "
        "first_seen_at, last_seen_at, created_at) "
        "VALUES ('WatchCo', 'watchco', 'watch_ready', 1, "
        "'2026-01-01', '2026-01-01', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    a = JobStorage(path, user_id="user-a")

    # Ghost column is 1, but no user_companies request → not in the scrape set.
    assert a.get_monitored_companies() == []

    a.set_company_monitored(1, True)
    assert [c["id"] for c in a.get_monitored_companies()] == [1]

    a.set_company_monitored(1, False)
    # Ghost column is still 1 — the set must be empty (reads user_companies).
    assert a.get_monitored_companies() == []


# ── Isolation: aggregate views ────────────────────────────────────────────────

def test_dashboard_and_stats_isolated(two_users):
    a, b = two_users
    a.set_status("job-1", "applied")
    a.upsert_contact(company_id=1, first_name="Alice")
    a.log_interaction(company_id=1, type="outreach_sent")

    # A has activity, B's aggregate user-space counts must not include any of it.
    assert b.get_database_stats()["total_contacts"] == 0
    assert b.get_database_stats()["total_interactions"] == 0
    assert a.get_database_stats()["total_contacts"] >= 1
    # Catalogue counts are shared (jobs/companies are not user-scoped).
    assert b.get_database_stats()["total_jobs"] == a.get_database_stats()["total_jobs"]


# ── Isolation: through the API (spec 041, FR-010 / T029) ─────────────────────

def test_api_two_user_isolation(two_users):
    """Two users + two tokens behind a `TestClient`: B must never see A's
    user-space data, and `/v1/me/jobs/{id}` scores are B's profiles only."""
    from api.main import create_app
    from fastapi.testclient import TestClient

    a, b = two_users
    path = a.db_path

    # Per-user profiles + one score each on job-1 (A=8, B=5).
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO search_profiles (id, user_id, name, criteria) "
        "VALUES ('prof-a', 'user-a', 'A', '{}')"
    )
    conn.execute(
        "INSERT INTO search_profiles (id, user_id, name, criteria) "
        "VALUES ('prof-b', 'user-b', 'B', '{}')"
    )
    conn.execute(
        "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
        "VALUES ('job-1', 'prof-a', 8, 'a', 'llm', '2026-01-02')"
    )
    conn.execute(
        "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
        "VALUES ('job-1', 'prof-b', 5, 'b', 'llm', '2026-01-02')"
    )
    conn.commit()
    conn.close()

    # A's user-space data (tracking + contact); B has none.
    a.set_status("job-1", "applied")
    a.upsert_contact(company_id=1, first_name="Alice")

    token_a = a.create_api_token("ta", ["read:me", "read:catalogue"])
    token_b = b.create_api_token("tb", ["read:me", "read:catalogue"])

    app = create_app()
    app.state.db_path = path
    client = TestClient(app)
    ha = {"Authorization": f"Bearer {token_a}"}
    hb = {"Authorization": f"Bearer {token_b}"}

    # A's contact is invisible to B (404, never 403) but visible to A.
    contact_id = a.get_all_contacts()[0]["id"]
    assert client.get(f"/v1/me/contacts/{contact_id}", headers=hb).status_code == 404
    assert client.get(f"/v1/me/contacts/{contact_id}", headers=ha).status_code == 200

    # B's feed carries no A tracking; A's does.
    feed_a = {i["id"]: i for i in client.get("/v1/me/feed", headers=ha).json()["items"]}
    feed_b = {i["id"]: i for i in client.get("/v1/me/feed", headers=hb).json()["items"]}
    assert feed_a["job-1"]["tracked_status"] == "applied"
    assert feed_b["job-1"]["tracked_status"] is None

    # B's applications/contacts are empty; A's contact and tracking do not leak.
    assert client.get("/v1/me/applications", headers=hb).json()["items"] == []
    assert client.get("/v1/me/contacts", headers=hb).json()["items"] == []
    assert len(client.get("/v1/me/contacts", headers=ha).json()["items"]) == 1

    # /v1/me/jobs/{id} scores are the bound user's profiles only.
    scores_b = client.get("/v1/me/jobs/job-1", headers=hb).json()["scores"]
    assert [s["profile_id"] for s in scores_b] == ["prof-b"]
    assert scores_b[0]["score"] == 5
    scores_a = client.get("/v1/me/jobs/job-1", headers=ha).json()["scores"]
    assert [s["profile_id"] for s in scores_a] == ["prof-a"]
    assert scores_a[0]["score"] == 8


def test_api_feed_foreign_profile_404_and_stats_scoped(two_users):
    """`/v1/me/feed?profile_id=<other user's>` → 404; `/v1/me/stats` is scoped."""
    from api.main import create_app
    from fastapi.testclient import TestClient

    a, b = two_users
    path = a.db_path

    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO search_profiles (id, user_id, name, criteria) "
        "VALUES ('prof-a', 'user-a', 'A', '{}')"
    )
    conn.execute(
        "INSERT INTO search_profiles (id, user_id, name, criteria) "
        "VALUES ('prof-b', 'user-b', 'B', '{}')"
    )
    # A scores job-1 (9) and job-2 (7); B scores only job-1 (2).
    conn.execute(
        "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
        "VALUES ('job-1', 'prof-a', 9, 'a', 'llm', '2026-01-02')"
    )
    conn.execute(
        "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
        "VALUES ('job-2', 'prof-a', 7, 'a', 'llm', '2026-01-02')"
    )
    conn.execute(
        "INSERT INTO job_scores (job_id, profile_id, score, reason, scored_by, scored_at) "
        "VALUES ('job-1', 'prof-b', 2, 'b', 'llm', '2026-01-02')"
    )
    conn.commit()
    conn.close()

    token_a = a.create_api_token("ta", ["read:me"])
    token_b = b.create_api_token("tb", ["read:me"])

    app = create_app()
    app.state.db_path = path
    client = TestClient(app)
    ha = {"Authorization": f"Bearer {token_a}"}
    hb = {"Authorization": f"Bearer {token_b}"}

    # B passing A's profile_id → 404 (never A's data); A's own profile → 200.
    assert (
        client.get("/v1/me/feed", params={"profile_id": "prof-a"}, headers=hb).status_code
        == 404
    )
    assert (
        client.get("/v1/me/feed", params={"profile_id": "prof-a"}, headers=ha).status_code
        == 200
    )

    # /v1/me/stats is user-scoped: B sees only its own profile's score.
    stats_a = client.get("/v1/me/stats", headers=ha).json()
    stats_b = client.get("/v1/me/stats", headers=hb).json()
    assert (stats_a["scored"], stats_a["hot"]) == (2, 1)
    assert (stats_b["scored"], stats_b["hot"]) == (1, 0)


# ── Isolation: spec 042 write routes ──────────────────────────────────────────

def _write_client(two_users):
    """Two write:me tokens + a `TestClient` over the shared two-user DB."""
    from api.main import create_app
    from fastapi.testclient import TestClient

    a, b = two_users
    path = a.db_path
    token_a = a.create_api_token("ta", ["write:me", "read:me"])
    token_b = b.create_api_token("tb", ["write:me", "read:me"])

    app = create_app()
    app.state.db_path = path
    client = TestClient(app)
    ha = {"Authorization": f"Bearer {token_a}"}
    hb = {"Authorization": f"Bearer {token_b}"}
    return a, b, client, ha, hb


def test_api_write_status_isolated(two_users):
    a, b, client, ha, _ = _write_client(two_users)

    r = client.put(
        "/v1/me/jobs/job-1/status",
        json={"status": "applied", "occurred_at": "2026-01-05"},
        headers=ha,
    )
    assert r.status_code == 200

    # A's write is visible to A, invisible to B (B's tracking for the shared job
    # stays None — no cross-user leak through the write route).
    assert a.get_tracking_status("job-1") == "applied"
    assert b.get_tracking_status("job-1") is None
    assert b.get_lifecycle_events("job-1") == []


def test_api_write_event_foreign_user_404(two_users):
    a, b, client, _, hb = _write_client(two_users)

    a.record_lifecycle_event(
        "job-1", event="application_submitted", occurred_at="2026-01-05", new_status="applied"
    )
    a.record_lifecycle_event(
        "job-1", event="interview_invited", occurred_at="2026-02-01", new_status="interviewing"
    )
    event_id = next(
        e["id"] for e in a.get_lifecycle_events("job-1") if e["type"] == "interview_invited"
    )

    # B addressing A's event → 404 (never 403 — no existence oracle), and A's
    # event/status are untouched by the rejected write.
    r = client.patch(
        f"/v1/me/jobs/job-1/events/{event_id}", json={"notes": "hijack"}, headers=hb
    )
    assert r.status_code == 404

    assert a.get_tracking_status("job-1") == "interviewing"
    assert any(e["id"] == event_id for e in a.get_lifecycle_events("job-1"))
    assert b.get_lifecycle_events("job-1") == []
    assert b.get_tracking_status("job-1") is None


def test_api_write_event_cross_job_404(two_users):
    a, b, client, ha, _ = _write_client(two_users)

    a.record_lifecycle_event(
        "job-1", event="application_submitted", occurred_at="2026-01-05", new_status="applied"
    )
    event_id = a.get_lifecycle_events("job-1")[0]["id"]

    # The event exists on job-1 but is addressed under job-2 → 404, and
    # reconciliation never runs on the wrong job (job-2 stays untracked, job-1
    # untouched).
    r = client.delete(f"/v1/me/jobs/job-2/events/{event_id}", headers=ha)
    assert r.status_code == 404

    assert a.get_lifecycle_events("job-1")[0]["id"] == event_id
    assert a.get_tracking_status("job-1") == "applied"
    assert a.get_tracking_status("job-2") is None


def test_api_write_company_relationship_isolated(two_users):
    a, b, client, ha, _ = _write_client(two_users)

    r = client.patch(
        "/v1/me/companies/1", json={"status": "blacklisted", "monitored": True}, headers=ha
    )
    assert r.status_code == 200

    # A's relationship write is visible only to A; B keeps the default
    # relationship and therefore no blacklisted jobs in its own feed.
    assert a.get_company_by_id(1)["status"] == "blacklisted"
    assert a.get_company_by_id(1)["monitored"] == 1
    assert b.get_company_by_id(1)["status"] == "prospect"
    assert b.count_blacklisted_jobs() == 0
    assert a.count_blacklisted_jobs() >= 1


def test_api_write_contact_isolated(two_users):
    a, b, client, ha, hb = _write_client(two_users)

    r = client.post(
        "/v1/me/contacts",
        json={"company_id": 1, "linkedin_url": "https://linkedin.com/in/alice", "first_name": "Alice"},
        headers=ha,
    )
    assert r.status_code == 200
    cid = r.json()["id"]

    # A's contact is visible to A, invisible to B (404, never 403), and B
    # writing A's contact id → 404 without touching it.
    assert a.get_contact(cid) is not None
    assert b.get_contact(cid) is None
    assert b.get_all_contacts() == []
    assert b.get_company_contacts(1) == []

    assert (
        client.patch(f"/v1/me/contacts/{cid}", json={"notes": "hijack"}, headers=hb).status_code
        == 404
    )
    assert a.get_contact(cid)["notes"] is None


def test_api_write_profile_isolated(two_users):
    a, b, client, ha, hb = _write_client(two_users)
    a.upsert_profile(_profile("prof-a"))
    b.upsert_profile(_profile("prof-b"))

    # A edits its own profile and switches to it via the API.
    r = client.put(
        "/v1/me/profiles/prof-a",
        json={"name": "A edited", "criteria": {"allowed_geo_zones": ["ch"]}},
        headers=ha,
    )
    assert r.status_code == 200
    assert client.put(
        "/v1/me/active-profile", json={"profile_id": "prof-a"}, headers=ha
    ).status_code == 204

    assert a.get_profile("prof-a")["name"] == "A edited"
    assert b.get_profile("prof-a") is None

    # B cannot edit A's profile or switch to it (foreign → 404, never 403), and
    # A's row is untouched by the rejected writes.
    assert (
        client.put(
            "/v1/me/profiles/prof-a",
            json={"name": "hijack", "criteria": {"allowed_geo_zones": []}},
            headers=hb,
        ).status_code
        == 404
    )
    assert (
        client.put("/v1/me/active-profile", json={"profile_id": "prof-a"}, headers=hb).status_code
        == 404
    )
    assert a.get_profile("prof-a")["name"] == "A edited"
    assert b.get_profile("prof-b")["name"] == "Profile prof-b"  # B's own profile intact


# ── Isolation: platform principal (spec 045, FR-007/FR-008) ───────────────────

def _platform_headers(api_env) -> dict:
    return {"Authorization": f"Bearer {api_env.make_token(['ingest'], user_id='platform')}"}


def test_platform_token_ingest_only(api_env):
    """A platform-principal token (scope `ingest` only) drives the ingest
    endpoints but can never read or write user space — 403, not 200/404."""
    h = _platform_headers(api_env)

    # Ingest endpoints are open to the platform principal.
    assert api_env.client.get("/v1/ingest/plan", headers=h).status_code == 200
    assert api_env.client.post("/v1/ingest/batch", json=[], headers=h).status_code == 200
    assert api_env.client.post(
        "/v1/ingest/events",
        json={"type": "run", "jobs_scraped": 0, "duration_seconds": 0.0, "run_type": "full"},
        headers=h,
    ).status_code == 200

    # User-space reads (read:me) are forbidden.
    for path in ("/v1/me", "/v1/me/feed", "/v1/me/contacts", "/v1/me/applications",
                 "/v1/me/stats", "/v1/me/dashboard", "/v1/me/companies",
                 "/v1/me/jobs/job-1"):
        r = api_env.client.get(path, headers=h)
        assert r.status_code == 403, f"{path} → {r.status_code}"
        assert r.json() == {"error": "forbidden", "message": "Insufficient scope"}

    # User-space writes (write:me) are forbidden.
    assert api_env.client.put(
        "/v1/me/jobs/job-1/status",
        json={"status": "applied", "occurred_at": "2026-01-05"}, headers=h,
    ).status_code == 403
    assert api_env.client.patch(
        "/v1/me/companies/1", json={"status": "blacklisted"}, headers=h,
    ).status_code == 403


def test_platform_principal_is_service_without_profile(api_env):
    """The platform principal is flagged `is_service=1` with no active profile and
    no user-space rows — a non-human identity, never a person's account."""
    conn = sqlite3.connect(api_env.path)
    row = conn.execute(
        "SELECT display_name, is_service, active_profile_id FROM users WHERE id = 'platform'"
    ).fetchone()
    n_profiles = conn.execute(
        "SELECT COUNT(*) FROM search_profiles WHERE user_id = 'platform'").fetchone()[0]
    conn.close()

    assert row == ("Platform ingestion", 1, None)
    assert n_profiles == 0
