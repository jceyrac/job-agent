"""tests/test_main_lock.py — `core.main` wraps every pipeline step in the
cron-side pipeline lock (spec 043, US2 / FR-004).

The lock itself is unit-tested in `test_worker.py`; these tests pin the *cron
path*: each of the four steps runs its subprocess while `pipeline_lock("cron")`
is held, and the holder is cleared afterwards — including when a step fails.
Subprocesses are faked, so no scrape/score actually runs.
"""
import sqlite3
import subprocess
import sys
import types

import core.main as main_mod
from core.storage import JobStorage


def _seed_monitored_company(db_path: str, user_id: str) -> None:
    """One `watch_ready` company monitored by the user, so the monitored-scrape
    step (and therefore all four steps) run under default config."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO companies"
        " (name, name_normalized, first_seen_at, last_seen_at, created_at, monitoring_status)"
        " VALUES ('Acme', 'acme', '2026-01-01', '2026-01-01', '2026-01-01', 'watch_ready')"
    )
    cid = conn.execute(
        "SELECT id FROM companies WHERE name_normalized = 'acme'"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO user_companies (user_id, company_id, status, monitored, created_at)"
        " VALUES (?, ?, 'prospect', 1, '2026-01-01')",
        (user_id, cid),
    )
    conn.commit()
    conn.close()


def _make_fake_run(db: JobStorage, calls: list, fail_on: str | None = None):
    """A `subprocess.run` stand-in that records the lock holder it observes and
    optionally raises (simulating a failed step)."""
    def fake_run(argv, **kwargs):
        calls.append({"argv": tuple(argv), "holder": db.get_pipeline_lock_holder()})
        if fail_on and fail_on in argv:
            raise subprocess.CalledProcessError(1, argv)
        return types.SimpleNamespace(returncode=0)
    return fake_run


def _run_main(tmp_path, monkeypatch, fail_on: str | None = None):
    db_path = str(tmp_path / "db.sqlite")
    db = JobStorage(db_path)  # seeds default user + profile, runs migrations
    _seed_monitored_company(db_path, db.user_id)
    monkeypatch.setattr(main_mod, "JobStorage", lambda db_path=None: db)
    calls = []
    monkeypatch.setattr(main_mod.subprocess, "run", _make_fake_run(db, calls, fail_on))
    monkeypatch.setattr(sys, "argv", ["core.main"])
    main_mod.main()
    return db, calls


def test_main_wraps_each_pipeline_step_in_cron_lock(tmp_path, monkeypatch):
    db, calls = _run_main(tmp_path, monkeypatch)

    # Exactly the four pipeline steps, in order.
    argv = [c["argv"][1:] for c in calls]
    assert len(argv) == 4
    assert argv[0] == ("-m", "core.scrape", "--monitored-only", "--no-score")
    assert argv[1] == ("-m", "core.scrape")
    assert argv[2] == ("-m", "core.score", "--extract")
    assert argv[3][:3] == ("-m", "core.score", "--profile")

    # Every step's subprocess ran while cron held the lock.
    assert [c["holder"] for c in calls] == ["cron", "cron", "cron", "cron"]

    # The holder is cleared once the pipeline finishes.
    assert db.get_pipeline_lock_holder() is None


def test_main_clears_lock_when_a_step_fails(tmp_path, monkeypatch):
    db, calls = _run_main(tmp_path, monkeypatch, fail_on="--extract")

    # Extraction failed, but the pipeline continued (scoring still ran) and the
    # failed step's lock was released cleanly: scoring re-acquired it, and the
    # holder is None at the end (no stale "cron" left behind).
    assert len(calls) == 4
    assert [c["holder"] for c in calls] == ["cron", "cron", "cron", "cron"]
    assert db.get_pipeline_lock_holder() is None
