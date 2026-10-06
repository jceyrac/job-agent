"""
tests/test_migration_safety_net.py — unit tests for the spec-031 safety-net scripts.

Covers the high-value pure logic: the backup prune-to-last-N rule, the manifest
JSON shape, the end-to-end backup → integrity → counts path on a scratch DB,
fingerprint determinism, the compare diff, and the health-check age threshold.
"""
import json
import sqlite3
from datetime import datetime

from scripts.backup_db import (
    _stems_to_prune,
    backup_sqlite,
    build_manifest,
    integrity_check,
    prune_backups,
    table_counts,
)
from scripts.compare_fingerprints import diff_fingerprints
from scripts.fingerprint import (
    _match_jobs,
    _regression_results,
    build_fingerprint,
)
from scripts.health_check import (
    DEFAULT_MAX_AGE_HOURS,
    gate_failures,
    run_age_hours,
)
from storage import JobStorage

# ── prune rule (FR-006) ────────────────────────────────────────────────────────

def test_stems_to_prune_keeps_newest_n():
    stems = [
        "20261001_000000", "20261002_000000", "20261003_000000",
        "20261004_000000", "20261005_000000", "20261006_000000",
    ]
    assert _stems_to_prune(stems, keep=5) == ["20261001_000000"]


def test_stems_to_prune_no_op_at_or_below_keep():
    stems = ["20261001_000000", "20261002_000000", "20261003_000000"]
    assert _stems_to_prune(stems, keep=5) == []


def test_prune_backups_only_touches_backup_dir(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for i in range(1, 7):
        stem = f"2026100{i}_000000"
        (backup_dir / f"jobs_{stem}.db").write_text("x")
        (backup_dir / f"cv_agent_checkpoints_{stem}.sqlite").write_text("x")
        (backup_dir / f"manifest_{stem}.json").write_text("{}")

    # A manual backup living OUTSIDE the backup dir must never be touched.
    outside = tmp_path / "jobs_backup_manual.db"
    outside.write_text("precious")

    deleted = prune_backups(str(backup_dir), keep=5)

    assert sorted(deleted) == ["20261001_000000"]
    assert (backup_dir / "jobs_20261006_000000.db").exists()
    assert not (backup_dir / "jobs_20261001_000000.db").exists()
    assert not (backup_dir / "manifest_20261001_000000.json").exists()
    assert outside.read_text() == "precious"


# ── manifest shape (FR-003) ────────────────────────────────────────────────────

def test_build_manifest_shape_without_checkpoints():
    manifest = build_manifest(
        timestamp="2026-10-03T12:00:00Z",
        git_ref="abc1234",
        files={
            "jobs": {"path": "jobs_20261003_120000.db", "size_bytes": 123, "integrity": "ok"},
            "checkpoints": None,
        },
        counts={
            "jobs": 1, "job_scores": 2, "job_tracking": 3,
            "interactions": 4, "contacts": 5, "companies": 6,
        },
    )
    assert manifest["timestamp"] == "2026-10-03T12:00:00Z"
    assert manifest["git_ref"] == "abc1234"
    assert manifest["files"]["jobs"]["integrity"] == "ok"
    assert manifest["files"]["checkpoints"] is None
    assert set(manifest["counts"]) == {
        "jobs", "job_scores", "job_tracking", "interactions", "contacts", "companies",
    }


def test_build_manifest_with_checkpoints():
    manifest = build_manifest(
        timestamp="t",
        git_ref="r",
        files={
            "jobs": {"path": "jobs.db", "size_bytes": 1, "integrity": "ok"},
            "checkpoints": {
                "path": "cv_agent_checkpoints_20261003_120000.sqlite",
                "size_bytes": 9,
                "integrity": "ok",
            },
        },
        counts={},
    )
    assert manifest["files"]["checkpoints"]["integrity"] == "ok"


# ── backup → integrity → counts (FR-001/003, SC-001) ──────────────────────────

def test_backup_integrity_and_counts_end_to_end(tmp_path):
    src = tmp_path / "src.db"
    conn = sqlite3.connect(src)
    conn.executescript(
        """
        CREATE TABLE jobs (id INTEGER PRIMARY KEY);
        CREATE TABLE job_scores (id INTEGER PRIMARY KEY);
        CREATE TABLE job_tracking (id INTEGER PRIMARY KEY);
        CREATE TABLE interactions (id INTEGER PRIMARY KEY);
        CREATE TABLE contacts (id INTEGER PRIMARY KEY);
        CREATE TABLE companies (id INTEGER PRIMARY KEY);
        INSERT INTO jobs VALUES (1), (2), (3);
        INSERT INTO companies VALUES (1);
        """
    )
    conn.commit()
    conn.close()

    dst = tmp_path / "dst.db"
    size = backup_sqlite(str(src), str(dst))
    assert size > 0

    assert integrity_check(str(dst)) == "ok"

    counts = table_counts(str(dst))
    assert counts["jobs"] == 3
    assert counts["companies"] == 1
    assert counts["contacts"] == 0


# ── fingerprint determinism (SC-003) ─────────────────────────────────────────

def _scratch_db(tmp_path) -> str:
    src = tmp_path / "src.db"
    JobStorage(str(src))  # creates schema + runs migrations on the file
    return str(src)


def test_fingerprint_deterministic(tmp_path):
    src = _scratch_db(tmp_path)
    a = build_fingerprint(src, "2026-10-03", None, None, "abc1234")
    b = build_fingerprint(src, "2026-10-03", None, None, "abc1234")

    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["meta"]["as_of"] == "2026-10-03"
    assert a["meta"]["git_ref"] == "abc1234"
    assert set(a["counts"]) == {
        "jobs", "job_scores", "job_tracking", "interactions", "contacts", "companies",
    }


def test_fingerprint_as_of_pins_dashboard_windows(tmp_path):
    # A follow-up dated 2020-06-15 must count as "due today" when as_of is
    # 2020-06-16 (7/14-day windows pin to as_of), and NOT when as_of is 2019 —
    # proving the pin reaches get_dashboard_data() rather than real `today`.
    src = tmp_path / "src.db"
    JobStorage(str(src))  # schema
    conn = sqlite3.connect(str(src))
    conn.execute(
        "INSERT INTO companies (name, name_normalized, first_seen_at, last_seen_at, created_at) "
        "VALUES ('Acme', 'acme', '2020-01-01', '2020-01-01', '2020-01-01')"
    )
    conn.execute(
        "INSERT INTO interactions "
        "(company_id, contact_id, job_id, type, direction, outcome, subject, body_excerpt, "
        " occurred_at, follow_up_due_at, created_at) "
        "VALUES (1, NULL, NULL, 'email', 'outbound', NULL, NULL, NULL, "
        " '2020-01-01', '2020-06-15', '2020-01-01')"
    )
    conn.commit()
    conn.close()

    past = build_fingerprint(str(src), "2019-01-01", None, None, "test")
    near = build_fingerprint(str(src), "2020-06-16", None, None, "test")

    assert len(past["dashboard"]["follow_ups_due_today"]) == 0
    assert len(near["dashboard"]["follow_ups_due_today"]) == 1


# ── compare detects a diff (SC-004) ──────────────────────────────────────────

def test_compare_identical_is_empty():
    a = {"counts": {"jobs": 5}, "feed": {"by_status": {"new": 5}}}
    assert diff_fingerprints(a, a) == []


def test_compare_detects_diff():
    a = {"counts": {"jobs": 5}, "feed": {"by_status": {"new": 5}}}
    b = {"counts": {"jobs": 6}, "feed": {"by_status": {"new": 5}}}
    diffs = diff_fingerprints(a, b)
    assert diffs
    assert any("counts/jobs" in d for d in diffs)


# ── regression-case matching (FR-015) ────────────────────────────────────────

def test_match_jobs_title_and_location():
    feed = [
        {"title": "Senior Tech Product Owner", "company": "FELFEL", "score": 6,
         "location": "Zurich", "base_location": "Zurich"},
        {"title": "Data Engineer", "company": "Acme", "score": 9,
         "location": "Lausanne, CH", "base_location": "Lausanne"},
        {"title": "Backend Dev", "company": "X", "score": 3,
         "location": "Bern", "base_location": "Bern"},
    ]
    assert len(_match_jobs(feed, {"title": "Senior Tech Product Owner", "company": "FELFEL"})) == 1
    assert len(_match_jobs(feed, {"location_contains": "Lausanne"})) == 1
    # case-insensitive exact on title + company
    assert len(_match_jobs(feed, {"title": "senior tech product owner", "company": "felfel"})) == 1


def test_regression_results_absent_and_score():
    feed = [{"title": "Data Engineer", "company": "Acme", "score": 9,
             "location": "Lausanne", "base_location": "Lausanne"}]
    cases = [
        {"id": "felfel", "match": {"title": "Senior Tech Product Owner", "company": "FELFEL"}},
        {"id": "lausanne", "match": {"location_contains": "Lausanne"}},
    ]
    results = _regression_results(feed, cases)
    assert results["felfel"] == "absent"
    assert results["lausanne"]["presence"] is True
    assert results["lausanne"]["matched_jobs"] == 1
    assert results["lausanne"]["score"] == 9


# ── health-check age threshold (26 h, FR-018) ───────────────────────────────

def test_run_age_fresh_passes_threshold():
    now = datetime(2026, 10, 3, 12, 0, 0)
    age = run_age_hours("2026-10-03 06:00:00", now)  # 6 h old
    assert age == 6.0
    assert age <= DEFAULT_MAX_AGE_HOURS


def test_run_age_stale_flags_threshold():
    now = datetime(2026, 10, 3, 12, 0, 0)
    age = run_age_hours("2026-09-30 12:00:00", now)  # 72 h old
    assert age == 72.0
    assert age > DEFAULT_MAX_AGE_HOURS


def test_gate_failures_uses_full_run_not_any_type():
    now = datetime(2026, 10, 3, 12, 0, 0)
    runs = {
        "monitored_only": {"ran_at": "2026-10-03 06:00:00", "status": "error"},
        "full": {"ran_at": "2026-10-03 06:00:00", "status": "success"},
    }
    # Latest row is monitored_only (error), but the gate is on full → no failures.
    assert gate_failures(runs, now, DEFAULT_MAX_AGE_HOURS) == []


def test_gate_failures_stale_full_run():
    now = datetime(2026, 10, 3, 12, 0, 0)
    runs = {"full": {"ran_at": "2026-09-30 12:00:00", "status": "success"}}  # 72 h
    assert "last full run age" in gate_failures(runs, now, DEFAULT_MAX_AGE_HOURS)


def test_gate_failures_bad_full_status():
    now = datetime(2026, 10, 3, 12, 0, 0)
    runs = {"full": {"ran_at": "2026-10-03 06:00:00", "status": "error"}}
    assert "last full run status (error)" in gate_failures(
        runs, now, DEFAULT_MAX_AGE_HOURS
    )


def test_gate_failures_missing_full_run():
    now = datetime(2026, 10, 3, 12, 0, 0)
    runs = {"monitored_only": {"ran_at": "2026-10-03 06:00:00", "status": "success"}}
    assert gate_failures(runs, now, DEFAULT_MAX_AGE_HOURS) == ["last full run missing"]
