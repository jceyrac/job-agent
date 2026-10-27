#!/usr/bin/env python3
"""Post-deploy health check (spec 031, FR-018).

Reports Streamlit health (GET ``/_stcore/health``) and, from the ``runs`` table,
the latest run of each ``run_type``; gates status and the age threshold on the
latest **full** run — a fresh ``monitored_only`` run must not mask a failed or
stale full pipeline run. Exits non-zero on a health failure, a missing full run,
a non-success full-run status, or a full run older than ``--max-age-hours``.

The ``runs`` read is a read-only diagnostic (``mode=ro``), in the spirit of the
FR-014 exception: ``JobStorage`` exposes no per-``run_type`` history, and a
health check must not run migrations on the live DB.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from core.paths import DB_PATH
from core.tasks import WORKER_HEARTBEAT_STALE_SECONDS
from core.storage import PURGE_STALE_DAYS

DEFAULT_MAX_AGE_HOURS = 26
# A "healthy" full run is a full scrape or a successful score run. "error" and
# "partial" (scoring finished with some failures) are flagged.
HEALTHY_STATUSES = {"success", "scraped"}


def run_age_hours(ran_at: str, now: datetime) -> float:
    """Hours elapsed between a run's ``ran_at`` and ``now`` (naive UTC).

    ``ran_at`` is written by ``log_run`` as SQLite ``datetime('now')`` —
    ``"YYYY-MM-DD HH:MM:SS"``, UTC. Any tz suffix is dropped (assumed UTC).
    """
    ran = datetime.fromisoformat(ran_at)
    if ran.tzinfo is not None:
        ran = ran.replace(tzinfo=None)
    return (now - ran).total_seconds() / 3600.0


def check_health(url: str, timeout: int = 10) -> bool:
    """True when ``url/_stcore/health`` responds with ``ok``."""
    try:
        with urllib.request.urlopen(
            url.rstrip("/") + "/_stcore/health", timeout=timeout
        ) as resp:
            return resp.read().decode("utf-8").strip() == "ok"
    except (urllib.error.URLError, OSError):
        return False


def check_api_health(url: str, timeout: int = 10) -> bool:
    """True when ``url/health`` responds with JSON ``{"status": "ok", ...}``.

    Distinct from :func:`check_health` — the API exposes a plain JSON ``/health``
    endpoint, not the Streamlit ``/_stcore/health`` probe. The base URL is the
    configurable ``JOB_AGENT_API_URL`` (tailnet ``https://<verva>.<tailnet>.ts.net/api``
    from the tailnet, ``http://127.0.0.1:8601`` on the host).
    """
    try:
        with urllib.request.urlopen(
            url.rstrip("/") + "/health", timeout=timeout
        ) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body.get("status") == "ok"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def latest_runs_by_type(path: str) -> dict[str, dict]:
    """Latest run per ``run_type`` (``{run_type: {"ran_at", "status"}}``).

    Read-only diagnostic query; returns ``{}`` when the DB or ``runs`` table is
    absent/unreadable. ``run_type`` is normalised to ``"full"`` when NULL/empty
    (the schema default and what ``log_run`` writes).
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return {}
    try:
        rows = conn.execute(
            "SELECT run_type, ran_at, status FROM runs ORDER BY ran_at DESC, id DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()

    latest: dict[str, dict] = {}
    for run_type, ran_at, status in rows:
        rt = run_type or "full"
        latest.setdefault(rt, {"ran_at": ran_at, "status": status})
    return latest


def worker_heartbeat_age(path: str, now: datetime) -> float | None:
    """Age (seconds) of the worker heartbeat, or ``None`` when absent/unreadable.

    The worker writes ``config["worker.heartbeat"]`` every ~15 s (spec 043,
    FR-005); a missing or stale beat reads as "worker down". Read-only diagnostic,
    like :func:`latest_runs_by_type` — no migrations on the live DB.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            "SELECT value FROM config WHERE key = 'worker.heartbeat'"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not row:
        return None
    try:
        ts = datetime.fromisoformat(row[0])
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        return (now - ts).total_seconds()
    except (ValueError, TypeError):
        return None


def purge_last_success_age(path: str, now: datetime) -> float | None:
    """Age (seconds) of the last successful purge, or ``None`` when absent/unreadable.

    ``purge_stale_jobs`` writes ``config["purge.last_success"]`` on success (the
    hotfix for the nightly "database is locked"); a missing or stale value reads
    as "purge broken". Read-only diagnostic, like :func:`worker_heartbeat_age`.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            "SELECT value FROM config WHERE key = 'purge.last_success'"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not row:
        return None
    try:
        ts = datetime.fromisoformat(row[0])
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        return (now - ts).total_seconds()
    except (ValueError, TypeError):
        return None


def purge_staleness_warning(age_seconds: float | None,
                            stale_days: float = PURGE_STALE_DAYS) -> str | None:
    """Warning string when the last purge is missing or older than ``stale_days``."""
    if age_seconds is None:
        return "no successful purge recorded"
    if age_seconds > stale_days * 86400:
        return f"last successful purge >{stale_days:.0f}d ago"
    return None


def gate_failures(runs_by_type: dict[str, dict], now: datetime,
                  max_age_hours: float) -> list[str]:
    """Failure labels for the latest **full** run (status + age threshold)."""
    gate = runs_by_type.get("full")
    if gate is None:
        return ["last full run missing"]
    failures: list[str] = []
    if gate["status"] not in HEALTHY_STATUSES:
        failures.append(f"last full run status ({gate['status']})")
    if run_age_hours(gate["ran_at"], now) > max_age_hours:
        failures.append("last full run age")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post-deploy health check (spec 031).")
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("JOB_AGENT_API_URL", "http://127.0.0.1:8601"),
    )
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    args = parser.parse_args(argv)

    failures: list[str] = []
    warnings: list[str] = []

    if check_health(args.url):
        print(f"tracker health: OK ({args.url})")
    else:
        print(f"tracker health: FAILED ({args.url}/_stcore/health)", file=sys.stderr)
        failures.append("tracker health")

    if check_api_health(args.api_url):
        print(f"api health: OK ({args.api_url})")
    else:
        print(f"api health: FAILED ({args.api_url}/health)", file=sys.stderr)
        failures.append("api health")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    runs = latest_runs_by_type(args.db)
    if not runs:
        print("runs: NONE (no runs recorded)", file=sys.stderr)
        failures.append("last run missing")
    else:
        for rt in sorted(runs):
            r = runs[rt]
            age_h = run_age_hours(r["ran_at"], now)
            print(f"run[{rt}]: status={r['status']} age={age_h:.1f}h")
        failures.extend(gate_failures(runs, now, args.max_age_hours))

    hb_age = worker_heartbeat_age(args.db, now)
    if hb_age is None:
        print("worker heartbeat: NONE (worker not running or never beat)", file=sys.stderr)
        failures.append("worker heartbeat")
    elif hb_age > WORKER_HEARTBEAT_STALE_SECONDS:
        print(f"worker heartbeat: STALE (age={hb_age:.0f}s)", file=sys.stderr)
        failures.append("worker heartbeat")
    else:
        print(f"worker heartbeat: OK (age={hb_age:.0f}s)")

    purge_age = purge_last_success_age(args.db, now)
    purge_warn = purge_staleness_warning(purge_age)
    if purge_warn:
        print(f"purge last success: WARN ({purge_warn})", file=sys.stderr)
        warnings.append(purge_warn)
    else:
        print(f"purge last success: OK (age={purge_age / 86400:.1f}d)")

    if failures:
        print("HEALTH CHECK FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    if warnings:
        print("HEALTH CHECK OK (warnings: " + "; ".join(warnings) + ")")
        return 0
    print("HEALTH CHECK OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
