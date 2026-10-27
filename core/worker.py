"""core/worker.py — single-consumer task worker (spec 043, FR-003).

Claims queued tasks one at a time under the shared pipeline lock and runs each
as its mapped module command, streaming output into ``task_events``. One worker
runs as the ``worker`` compose service; cron (``core.main``) shares the lock.
"""
from __future__ import annotations

import collections
import logging
import os
import select
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

from core.paths import DB_PATH
from core.storage import JobStorage
from core.tasks import TASK_TIMEOUTS, command_for, pipeline_lock

logger = logging.getLogger(__name__)

POLL_INTERVAL = 1.0        # seconds between polls when idle
HEARTBEAT_INTERVAL = 15.0  # heartbeat cadence (spec FR-003)
PRUNE_INTERVAL = 3600.0    # retention tick (spec R6)
_CANCEL_GRACE = 5.0        # SIGTERM → SIGKILL grace
_EVENT_BATCH = 50          # flush events in small batches (spec R6)
_ERROR_TAIL = 20           # output-tail lines recorded as `error` on failure


class _WorkerState:
    """Mutable control flags shared with the SIGTERM/SIGINT handlers."""

    def __init__(self):
        self.stop = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terminate(proc: subprocess.Popen) -> None:
    """SIGTERM, bounded grace, then SIGKILL (spec FR-003)."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=_CANCEL_GRACE)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _run_task(db: JobStorage, task: dict, state: _WorkerState,
              argv: list[str] | None = None, timeout: float | None = None,
              heartbeat_interval: float = HEARTBEAT_INTERVAL) -> None:
    """Run one claimed task to completion, streaming lines into ``task_events``.

    The timeout deadline starts here, at claim time after the lock is held, so a
    lock-wait behind cron never eats the budget (research R3). ``argv``/``timeout``
    are injectable for tests; they default to the type map. The worker heartbeat
    is refreshed every ``heartbeat_interval`` seconds for the whole run, so the
    worker stays "alive" even while the subprocess prints nothing (FR-005).
    """
    task_id = task["id"]
    if argv is None:
        argv = command_for(task["type"], task["params"])
    if timeout is None:
        timeout = TASK_TIMEOUTS.get(task["type"], 3600)

    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    fd = proc.stdout.fileno()
    deadline = time.monotonic() + timeout
    pending: list[str] = []
    tail = collections.deque(maxlen=_ERROR_TAIL)

    def flush() -> None:
        if pending:
            db.append_task_events(task_id, pending)
            pending.clear()

    def finish(status: str, exit_code: int | None = None, error: str | None = None) -> None:
        flush()
        db.finish_task(task_id, status, exit_code=exit_code, error=error)

    last_beat = time.monotonic()
    while proc.poll() is None:
        now = time.monotonic()
        if now - last_beat >= heartbeat_interval:
            db.set_worker_heartbeat(_now())
            last_beat = now
        try:
            ready, _, _ = select.select([fd], [], [], 0.25)
        except (ValueError, OSError):
            ready = []
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if chunk:
                for line in chunk.decode("utf-8", errors="replace").splitlines():
                    if not line:
                        continue
                    pending.append(line)
                    tail.append(line)
                if len(pending) >= _EVENT_BATCH:
                    flush()

        if state.stop:
            _terminate(proc)
            finish("failed", error="interrupted")
            return
        if db.is_cancel_requested(task_id):
            _terminate(proc)
            finish("cancelled")
            return
        if time.monotonic() >= deadline:
            _terminate(proc)
            finish("failed", error="timeout")
            return

    # Process exited — drain any buffered output, then record the result.
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                if line:
                    pending.append(line)
                    tail.append(line)
    except OSError:
        pass

    ret = proc.wait()
    if state.stop:
        finish("failed", error="interrupted")
    elif db.is_cancel_requested(task_id):
        finish("cancelled")
    elif ret == 0:
        finish("succeeded", exit_code=0)
    else:
        finish("failed", exit_code=ret, error="\n".join(tail))


def _poll_once(db: JobStorage, state: _WorkerState,
               argv: list[str] | None = None, timeout: float | None = None,
               heartbeat_interval: float = HEARTBEAT_INTERVAL):
    """One worker iteration: peek → lock → claim → run. The idle poll is a cheap
    read (no flock, no holder write), so an idle worker writes only its heartbeat.
    A task is still claimed under the lock, so it is never claimed while the cron
    holds the pipeline. While waiting for the lock (behind cron) or running a
    task, the heartbeat is refreshed so the worker always looks alive (FR-005)."""
    if not db.has_queued_task():
        return None
    with pipeline_lock("worker", db,
                       heartbeat=lambda: db.set_worker_heartbeat(_now()),
                       heartbeat_interval=heartbeat_interval):
        task = db.claim_next_task()
        if task is not None:
            _run_task(db, task, state, argv=argv, timeout=timeout,
                      heartbeat_interval=heartbeat_interval)
    return task


def _install_signal_handlers(state: _WorkerState) -> None:
    def _handler(signum, frame):
        state.stop = True
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def run_forever() -> None:
    """Single-consumer loop (research R3)."""
    db = JobStorage(DB_PATH)
    db.recover_interrupted()
    db.set_worker_heartbeat(_now())

    state = _WorkerState()
    _install_signal_handlers(state)

    last_beat = time.monotonic()
    last_prune = time.monotonic()

    while not state.stop:
        now = time.monotonic()
        if now - last_beat >= HEARTBEAT_INTERVAL:
            db.set_worker_heartbeat(_now())
            last_beat = now
        if now - last_prune >= PRUNE_INTERVAL:
            db.prune_task_history()
            last_prune = now

        _poll_once(db, state)

        if not state.stop:
            time.sleep(POLL_INTERVAL)

    logger.info("[worker] shutting down")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
