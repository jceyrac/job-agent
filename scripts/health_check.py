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
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from paths import DB_PATH

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
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    args = parser.parse_args(argv)

    failures: list[str] = []

    if check_health(args.url):
        print(f"tracker health: OK ({args.url})")
    else:
        print(f"tracker health: FAILED ({args.url}/_stcore/health)", file=sys.stderr)
        failures.append("tracker health")

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

    if failures:
        print("HEALTH CHECK FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("HEALTH CHECK OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
