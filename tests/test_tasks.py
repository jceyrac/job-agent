"""tests/test_tasks.py — task queue storage layer (spec 043, US1).

Fast in-memory / tmp-file tests; never touch data/jobs.db.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.storage import JobStorage, ProfileNotFoundError


class _Profile:
    def __init__(self, pid: str):
        self.id = pid
        self.name = "Test"
    def to_criteria_dict(self):
        return {}


def _old(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ── enqueue + dedup ───────────────────────────────────────────────────────────

def test_enqueue_and_dedup_returns_existing_task():
    db = JobStorage(":memory:")
    first = db.enqueue_task("extract", {}, requested_by="tracker")
    second = db.enqueue_task("extract", {}, requested_by="tracker")
    assert first["id"] == second["id"]
    assert first["status"] == "queued"
    rows, total = db.list_tasks()
    assert total == 1


def test_enqueue_unknown_type_raises():
    db = JobStorage(":memory:")
    with pytest.raises(ValueError):
        db.enqueue_task("nope", {})


def test_enqueue_score_requires_profile_id():
    db = JobStorage(":memory:")
    with pytest.raises(ValueError):
        db.enqueue_task("score", {})


def test_enqueue_score_foreign_profile_raises(tmp_path):
    path = str(tmp_path / "db.sqlite")
    db_alice = JobStorage(path, user_id="alice")
    db_alice.upsert_profile(_Profile("p-alice"))
    db_bob = JobStorage(path, user_id="bob")
    with pytest.raises(ProfileNotFoundError):
        db_bob.enqueue_task("score", {"profile_id": "p-alice"})


def test_enqueue_score_own_profile_ok(tmp_path):
    path = str(tmp_path / "db.sqlite")
    db = JobStorage(path, user_id="alice")
    db.upsert_profile(_Profile("p-alice"))
    task = db.enqueue_task("score", {"profile_id": "p-alice"})
    assert task["type"] == "score"
    assert task["params"] == {"profile_id": "p-alice"}


# ── claim ─────────────────────────────────────────────────────────────────────

def test_claim_is_oldest_first_and_atomic():
    db = JobStorage(":memory:")
    extract = db.enqueue_task("extract", {})
    scrape = db.enqueue_task("scrape", {})
    claimed = db.claim_next_task()
    assert claimed["id"] == extract["id"]       # oldest first
    assert claimed["status"] == "running"
    assert claimed["started_at"] is not None
    assert claimed["cancel_requested"] == 0
    # the scrape task is still queued
    assert db.get_task(scrape["id"])["status"] == "queued"


def test_claim_returns_none_when_empty():
    db = JobStorage(":memory:")
    assert db.claim_next_task() is None


# ── events ────────────────────────────────────────────────────────────────────

def test_append_and_get_events_after_seq():
    db = JobStorage(":memory:")
    task = db.enqueue_task("extract", {})
    db.append_task_events(task["id"], ["one", "two"])
    db.append_task_events(task["id"], ["three"])
    events = db.get_task_events(task["id"])
    assert [e["line"] for e in events] == ["one", "two", "three"]
    assert [e["seq"] for e in events] == [1, 2, 3]
    resumed = db.get_task_events(task["id"], after_seq=1)
    assert [e["line"] for e in resumed] == ["two", "three"]


def test_append_events_trims_to_window():
    db = JobStorage(":memory:")
    task = db.enqueue_task("extract", {})
    lines = [f"line-{i}" for i in range(5000)]
    db.append_task_events(task["id"], lines)
    events = db.get_task_events(task["id"])
    assert len(events) == 4000
    assert events[0]["line"] == "line-1000"     # oldest 1000 trimmed
    assert events[-1]["line"] == "line-4999"


# ── finish / cancel / recover ─────────────────────────────────────────────────

def test_finish_task_records_result():
    db = JobStorage(":memory:")
    task = db.enqueue_task("extract", {})
    db.claim_next_task()
    db.finish_task(task["id"], "failed", exit_code=2, error="boom")
    got = db.get_task(task["id"])
    assert got["status"] == "failed"
    assert got["exit_code"] == 2
    assert got["error"] == "boom"
    assert got["finished_at"] is not None


def test_mark_cancelled_queued():
    db = JobStorage(":memory:")
    task = db.enqueue_task("extract", {})
    db.mark_cancelled(task["id"])
    assert db.get_task(task["id"])["status"] == "cancelled"


def test_request_cancel_and_is_cancel_requested():
    db = JobStorage(":memory:")
    task = db.enqueue_task("extract", {})
    db.claim_next_task()  # -> running
    assert db.is_cancel_requested(task["id"]) is False
    db.request_cancel(task["id"])
    assert db.is_cancel_requested(task["id"]) is True


def test_recover_interrupted():
    db = JobStorage(":memory:")
    task = db.enqueue_task("extract", {})
    db.claim_next_task()  # -> running
    db.recover_interrupted()
    got = db.get_task(task["id"])
    assert got["status"] == "failed"
    assert got["error"] == "interrupted"


# ── retention ─────────────────────────────────────────────────────────────────

def test_prune_task_history(tmp_path):
    path = str(tmp_path / "db.sqlite")
    db = JobStorage(path)

    old_task = db.enqueue_task("extract", {})
    db.append_task_events(old_task["id"], ["old-line"])
    db.finish_task(old_task["id"], "succeeded", exit_code=0)

    middle_task = db.enqueue_task("scrape", {})
    db.append_task_events(middle_task["id"], ["mid-line"])
    db.finish_task(middle_task["id"], "succeeded", exit_code=0)

    recent_task = db.enqueue_task("scrape_monitored", {})
    db.append_task_events(recent_task["id"], ["recent-line"])
    db.finish_task(recent_task["id"], "succeeded", exit_code=0)

    # Backdate: old = 31 days (row + events pruned), middle = 8 days (events only).
    with db._conn() as conn:
        conn.execute("UPDATE tasks SET finished_at = ? WHERE id = ?", (_old(31), old_task["id"]))
        conn.execute("UPDATE task_events SET ts = ? WHERE task_id = ?", (_old(31), old_task["id"]))
        conn.execute("UPDATE tasks SET finished_at = ? WHERE id = ?", (_old(8), middle_task["id"]))
        conn.execute("UPDATE task_events SET ts = ? WHERE task_id = ?", (_old(8), middle_task["id"]))

    db.prune_task_history()

    assert db.get_task(old_task["id"]) is None
    assert db.get_task_events(old_task["id"]) == []
    assert db.get_task(middle_task["id"])["status"] == "succeeded"   # row kept (< 30d)
    assert db.get_task_events(middle_task["id"]) == []               # events pruned (>= 7d)
    assert [e["line"] for e in db.get_task_events(recent_task["id"])] == ["recent-line"]


# ── user-scoping isolation ────────────────────────────────────────────────────

def test_user_scoping_isolation(tmp_path):
    path = str(tmp_path / "db.sqlite")
    db_alice = JobStorage(path, user_id="alice")
    db_bob = JobStorage(path, user_id="bob")
    task = db_alice.enqueue_task("extract", {})
    assert db_bob.get_task(task["id"]) is None
    _, total = db_bob.list_tasks()
    assert total == 0
    assert db_alice.get_task(task["id"])["id"] == task["id"]


# ── active-task lookup + config signals ───────────────────────────────────────

def test_get_active_task_resume():
    db = JobStorage(":memory:")
    db.enqueue_task("extract", {})
    active = db.get_active_task("extract", {})
    assert active is not None and active["status"] == "queued"
    db.mark_cancelled(active["id"])
    assert db.get_active_task("extract", {}) is None   # finished -> not active


def test_heartbeat_and_lock_holder_roundtrip():
    db = JobStorage(":memory:")
    assert db.get_worker_heartbeat() is None
    db.set_worker_heartbeat("2026-10-17T12:00:00+00:00")
    assert db.get_worker_heartbeat() == "2026-10-17T12:00:00+00:00"

    assert db.get_pipeline_lock_holder() is None
    db.set_pipeline_lock_holder("cron")
    assert db.get_pipeline_lock_holder() == "cron"
    db.set_pipeline_lock_holder(None)
    assert db.get_pipeline_lock_holder() is None
