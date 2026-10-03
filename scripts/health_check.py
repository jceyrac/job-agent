#!/usr/bin/env python3
"""Post-deploy health check (spec 031, FR-018).

Reports Streamlit health (GET ``/_stcore/health``) and the last ``runs`` row's
status / type / age, and exits non-zero on a health failure or a run older than
``--max-age-hours``. Stdlib only — no ``import streamlit``.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paths import DB_PATH  # noqa: E402
from storage import JobStorage  # noqa: E402

DEFAULT_MAX_AGE_HOURS = 26
# A "healthy" last run is a full scrape or a successful score run. "error" and
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

    db = JobStorage(args.db)
    run = db.get_last_run()
    if run is None:
        print("last run: NONE (no runs recorded)", file=sys.stderr)
        failures.append("last run missing")
    else:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        age_h = run_age_hours(run["ran_at"], now)
        status = run.get("status")
        run_type = run.get("run_type") or "unknown"
        print(f"last run: status={status} type={run_type} age={age_h:.1f}h")
        if status not in HEALTHY_STATUSES:
            print(f"last run: FAILED (status={status!r})", file=sys.stderr)
            failures.append(f"last run status ({status})")
        if age_h > args.max_age_hours:
            print(
                f"last run: STALE ({age_h:.1f}h > {args.max_age_hours}h)",
                file=sys.stderr,
            )
            failures.append(f"last run age ({age_h:.1f}h)")

    if failures:
        print("HEALTH CHECK FAILED: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("HEALTH CHECK OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
