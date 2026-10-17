"""tests/test_worker.py — worker command map, lifecycle and the shared lock
(spec 043, US1 + US2).
"""
import sys
import threading
import time

from core.storage import JobStorage
from core.tasks import PipelineLockTimeout, TASK_TIMEOUTS, command_for, pipeline_lock
from core.worker import _WorkerState, _poll_once, _run_task


def _slow_argv(seconds: float) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


# ── command map (R2) ──────────────────────────────────────────────────────────

def test_command_for_scrape():
    assert command_for("scrape", {}) == [sys.executable, "-u", "-m", "core.scrape"]


def test_command_for_scrape_monitored():
    assert command_for("scrape_monitored", {}) == [
        sys.executable, "-u", "-m", "core.scrape", "--monitored-only",
    ]


def test_command_for_extract():
    assert command_for("extract", {}) == [sys.executable, "-u", "-m", "core.score", "--extract"]


def test_command_for_score():
    assert command_for("score", {"profile_id": "p1"}) == [
        sys.executable, "-u", "-m", "core.score", "--profile", "p1",
    ]


def test_command_for_unknown_raises():
    try:
        command_for("bogus", {})
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown task type")


# ── lifecycle ─────────────────────────────────────────────────────────────────

def test_recover_interrupted_flips_running():
    db = JobStorage(":memory:")
    task = db.enqueue_task("extract", {})
    db.claim_next_task()
    db.recover_interrupted()
    got = db.get_task(task["id"])
    assert got["status"] == "failed"
    assert got["error"] == "interrupted"


def test_timeout_marks_failed(tmp_path):
    db = JobStorage(str(tmp_path / "db.sqlite"))
    task = db.enqueue_task("extract", {})
    db.claim_next_task()
    _run_task(db, task, _WorkerState(), argv=_slow_argv(30), timeout=0.3)
    got = db.get_task(task["id"])
    assert got["status"] == "failed"
    assert got["error"] == "timeout"


def test_cancel_running_terminates(tmp_path):
    db = JobStorage(str(tmp_path / "db.sqlite"))
    task = db.enqueue_task("extract", {})
    db.claim_next_task()
    state = _WorkerState()
    thread = threading.Thread(
        target=_run_task, args=(db, task, state), kwargs={"argv": _slow_argv(30), "timeout": 30}
    )
    thread.start()
    time.sleep(0.2)
    db.request_cancel(task["id"])
    thread.join(timeout=10)
    got = db.get_task(task["id"])
    assert got["status"] == "cancelled"


def test_success_records_exit_zero(tmp_path):
    db = JobStorage(str(tmp_path / "db.sqlite"))
    task = db.enqueue_task("extract", {})
    db.claim_next_task()
    _run_task(db, task, _WorkerState(),
              argv=[sys.executable, "-c", "print('hello')"])
    got = db.get_task(task["id"])
    assert got["status"] == "succeeded"
    assert got["exit_code"] == 0
    assert [e["line"] for e in db.get_task_events(task["id"])] == ["hello"]


# ── shared lock (R4 / US2) ────────────────────────────────────────────────────

def test_pipeline_lock_records_and_clears_holder(tmp_path):
    db = JobStorage(str(tmp_path / "db.sqlite"))
    with pipeline_lock("cron", db):
        assert db.get_pipeline_lock_holder() == "cron"
    assert db.get_pipeline_lock_holder() is None


def test_second_acquirer_blocks_then_proceeds(tmp_path):
    db = JobStorage(str(tmp_path / "db.sqlite"))
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with pipeline_lock("cron", db):
            acquired.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(timeout=2)

    done = threading.Event()

    def second():
        with pipeline_lock("worker", db):
            done.set()

    s = threading.Thread(target=second)
    s.start()
    time.sleep(0.2)
    assert not done.is_set()          # blocked while cron holds the lock
    release.set()
    t.join(timeout=5)
    s.join(timeout=5)
    assert done.is_set()


def test_bounded_wait_times_out(tmp_path):
    db = JobStorage(str(tmp_path / "db.sqlite"))
    with pipeline_lock("cron", db):
        try:
            with pipeline_lock("worker", db, timeout=0.2):
                raise AssertionError("should not acquire")
        except PipelineLockTimeout:
            pass
        else:
            raise AssertionError("expected PipelineLockTimeout")


# ── lock-before-claim ordering (T010) ─────────────────────────────────────────

def test_task_stays_queued_behind_cron_lock_then_runs(tmp_path):
    db = JobStorage(str(tmp_path / "db.sqlite"))
    task = db.enqueue_task("extract", {})
    state = _WorkerState()
    started = threading.Event()
    release = threading.Event()

    def hold_cron():
        with pipeline_lock("cron", db):
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=hold_cron)
    t.start()
    assert started.wait(timeout=2)

    polled = {}

    def poll():
        polled["task"] = _poll_once(
            db, state, argv=[sys.executable, "-c", "pass"], timeout=5
        )

    p = threading.Thread(target=poll)
    p.start()
    time.sleep(0.2)

    # The worker is blocked on the lock, so the task is still queued.
    assert db.get_task(task["id"])["status"] == "queued"
    assert db.get_pipeline_lock_holder() == "cron"

    release.set()
    t.join(timeout=5)
    p.join(timeout=5)
    assert db.get_task(task["id"])["status"] == "succeeded"


def test_idle_worker_releases_lock(tmp_path):
    db = JobStorage(str(tmp_path / "db.sqlite"))
    assert _poll_once(db, _WorkerState()) is None   # nothing queued
    assert db.get_pipeline_lock_holder() is None    # lock released between polls
