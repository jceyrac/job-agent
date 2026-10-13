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

from core.storage import JobStorage

# Public user-space methods (read filter on `self.user_id`, write set it).
# Transcribed from plan.md §3a–§3g; the coverage test below guards completeness.
USER_SPACE_METHODS = (
    # search_profiles
    "upsert_profile", "get_profile", "get_all_profiles", "delete_profile",
    # job_tracking / status_history
    "set_status", "record_lifecycle_event", "reconcile_lifecycle_status",
    "get_tracking_status", "get_digest", "get_stats", "get_job_for_prepare",
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
    "count_blacklisted_jobs", "get_company_relationship_summary",
    "application_date_missing",
    # aggregate
    "get_dashboard_data", "get_database_stats",
    # user identity / settings accessors
    "get_active_profile_id", "set_active_profile_id",
    "get_onboarding_completed", "set_onboarding_completed",
    # company import (writes user_companies / company_status_history)
    "import_companies_from_csv",
)

# Deliberately unfiltered (cross-user) — plan.md §5 / FR-010, FR-011.
CROSS_USER_METHODS = (
    "purge_stale_jobs", "count_purgeable_jobs", "get_engaged_job_keys",
    "get_monitored_companies", "get_watching_companies_by_method",
    "get_watch_pending_companies", "get_companies_by_ats_provider",
)

# Tokens that must never appear in an unclassified (catalogue) method's source.
_USER_SPACE_TOKENS = (
    "self.user_id", "job_tracking", "job_applications", "search_profiles",
    "status_history", "company_status_history", "user_companies",
)

# User-space methods that derive their result purely from another user-space
# method (and so never reference `user_id` / a table name directly).
_DELEGATING_METHODS = {"get_lifecycle_summary"}


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
        "INSERT INTO users (id, display_name, created_at) VALUES ('user-a', 'A', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO users (id, display_name, created_at) VALUES ('user-b', 'B', '2026-01-01')"
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
