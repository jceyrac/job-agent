"""core/tasks.py — task queue constants, the type→command map, and the shared
pipeline lock (spec 043).

The worker runs each task as a subprocess of the same module command the
tracker runs today (spec Q1). ``pipeline_lock`` serialises the worker against
``core.main`` (cron) on a lock file in the data volume (spec Q2 / FR-004).
"""
from __future__ import annotations

import errno
import os
import sys
import time
from contextlib import contextmanager

from core.paths import data_path

# Allow-listed task types (spec R2). ``core.main`` is deliberately NOT a task
# type: its subprocess steps are cron's, not the queue's.
TASK_TYPES = ("scrape", "scrape_monitored", "extract", "score")

# Per-type maximum duration in seconds (spec FR-002 / R2).
TASK_TIMEOUTS = {
    "scrape": 60 * 60,
    "scrape_monitored": 45 * 60,
    "extract": 45 * 60,
    "score": 60 * 60,
}

# Bounded event storage + retention (spec R6): keep the last N events per
# active task; prune finished tasks' events after EVENT_RETENTION_DAYS and
# finished task rows after TASK_RETENTION_DAYS (worker idle tick).
EVENT_WINDOW = 4000
EVENT_RETENTION_DAYS = 7
TASK_RETENTION_DAYS = 30

# Lock file lives in the shared data volume so the worker and cron containers
# both see it (spec R4).
_LOCK_FILENAME = ".pipeline.lock"

# Bounded wait for cron behind a worker-held lock (seconds) — spec R4.
CRON_LOCK_WAIT_SECONDS = 300

# A worker heartbeat older than this (seconds) reads as "worker down" in the
# tracker and health check (spec FR-005 / US4). The worker writes one every
# ~15 s, so this is ~4 missed beats.
WORKER_HEARTBEAT_STALE_SECONDS = 60


def command_for(task_type: str, params: dict | None = None) -> list[str]:
    """Map a task type + params to the exact argv the worker runs (spec R2).

    ``-u`` keeps each output line unbuffered so it lands in ``task_events`` as
    it is produced. ``sys.executable`` is the same interpreter the tracker's
    launch buttons spawn today.
    """
    if task_type == "scrape":
        return [sys.executable, "-u", "-m", "core.scrape"]
    if task_type == "scrape_monitored":
        return [sys.executable, "-u", "-m", "core.scrape", "--monitored-only"]
    if task_type == "extract":
        return [sys.executable, "-u", "-m", "core.score", "--extract"]
    if task_type == "score":
        profile_id = (params or {}).get("profile_id")
        return [sys.executable, "-u", "-m", "core.score", "--profile", profile_id]
    raise ValueError(f"unknown task type: {task_type}")


class PipelineLockTimeout(Exception):
    """Raised when a bounded lock wait expires (cron behind a stuck worker)."""


@contextmanager
def pipeline_lock(label: str, db=None, timeout: float | None = None):
    """Exclusive lock serialising the worker and cron (spec R4).

    Takes an exclusive ``fcntl.flock`` on ``data_path(".pipeline.lock")``. On
    acquire it records the holder via ``db.set_pipeline_lock_holder(label)``
    (``"worker"`` or ``"cron"``) and clears it on release, so the tracker can
    render "waiting for the scheduled pipeline" deterministically. ``timeout``
    bounds the wait (cron); ``None`` blocks until acquired (worker).
    """
    import fcntl  # Unix-only; imported lazily so core.storage stays portable.

    path = data_path(_LOCK_FILENAME)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout if timeout else None
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
                if deadline is not None and time.monotonic() >= deadline:
                    raise PipelineLockTimeout(label) from None
                time.sleep(0.25)
        if db is not None:
            db.set_pipeline_lock_holder(label)
        try:
            yield
        finally:
            if db is not None:
                db.set_pipeline_lock_holder(None)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
