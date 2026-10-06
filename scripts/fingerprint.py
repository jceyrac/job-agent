#!/usr/bin/env python3
"""Deterministic parity fingerprint of app-visible state (spec 031, FR-012..015).

Computes a key-sorted JSON description of what the tracker exposes from a DB at
an ``--as-of`` date, by calling the ``JobStorage`` public read methods the UI
uses (FR-013). It works on a **temporary copy** of the DB — never mutating the
source, so migrations never alter a backup (FR-012) — and reads table row counts
via a raw read-only connection (FR-014). ``datetime.date.today`` is pinned to
``--as-of`` (R4) so two runs on the same snapshot with the same code and date are
byte-identical (SC-003).
"""
from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from unittest import mock

from storage import JobStorage

CORE_TABLES = (
    "jobs",
    "job_scores",
    "job_tracking",
    "interactions",
    "contacts",
    "companies",
)


def _git_ref() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _dashboard_at(db: JobStorage, as_of: str) -> dict:
    """Return ``get_dashboard_data()`` with ``date.today()`` pinned to ``as_of`` (R4).

    ``datetime.date`` is an immutable C type, so ``date.today`` cannot be
    reassigned directly. Instead we shadow the module attribute ``datetime.date``
    for the duration of the call: ``get_dashboard_data`` resolves ``date`` via
    ``from datetime import date`` at call time and picks up the stand-in.
    """
    pinned = _datetime.date.fromisoformat(as_of)

    class _PinnedDate:
        @classmethod
        def today(cls):
            return pinned

    with mock.patch("datetime.date", _PinnedDate):
        return db.get_dashboard_data()


def _copy_db(src: str, dst: str) -> None:
    """Consistent, self-contained copy of ``src`` into ``dst`` (online backup)."""
    src_conn = sqlite3.connect(src)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _table_counts(path: str) -> dict[str, int]:
    """Row counts for the core tables (raw diagnostic read — SELECT only)."""
    conn = sqlite3.connect(path)
    try:
        return {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in CORE_TABLES
        }
    finally:
        conn.close()


def _load_cases(path: str) -> list[dict]:
    with open(path) as fh:
        return json.load(fh).get("cases", [])


def _feed_sections(feed: list[dict]) -> dict:
    by_status: dict[str, int] = {}
    bands = {">=8": 0, "5-7": 0, "<5": 0, "unscored": 0}
    for job in feed:
        status = job.get("status") or "new"
        by_status[status] = by_status.get(status, 0) + 1
        score = job.get("score")
        if score is None:
            bands["unscored"] += 1
        elif score >= 8:
            bands[">=8"] += 1
        elif score >= 5:
            bands["5-7"] += 1
        else:
            bands["<5"] += 1
    return {"by_status": by_status, "by_score_band": bands}


def _score_distribution(feed: list[dict]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for job in feed:
        score = job.get("score")
        if score is not None:
            key = str(int(score))
            dist[key] = dist.get(key, 0) + 1
    return dist


def _match_jobs(feed: list[dict], match: dict) -> list[dict]:
    """Jobs matching a regression-case ``match``.

    ``match`` is one of ``{title}``, ``{title, company}`` (case-insensitive
    exact) or ``{location_contains}`` (case-insensitive substring on
    ``location``/``base_location``).
    """
    title = (match.get("title") or "").strip().lower()
    company = (match.get("company") or "").strip().lower()
    loc = (match.get("location_contains") or "").strip().lower()

    out: list[dict] = []
    for job in feed:
        if title:
            if (job.get("title") or "").strip().lower() != title:
                continue
            if company and (job.get("company") or "").strip().lower() != company:
                continue
            out.append(job)
        elif loc:
            jloc = (job.get("location") or "").lower()
            jbase = (job.get("base_location") or "").lower()
            if loc in jloc or loc in jbase:
                out.append(job)
    return out


def _regression_results(feed: list[dict], cases: list[dict]) -> dict:
    results: dict = {}
    for case in cases:
        cid = case["id"]
        matched = _match_jobs(feed, case.get("match", {}))
        if not matched:
            results[cid] = "absent"
            continue
        scores = [j.get("score") for j in matched if j.get("score") is not None]
        results[cid] = {
            "presence": True,
            "matched_jobs": len(matched),
            "score": max(scores) if scores else None,
        }
    return results


def build_fingerprint(db_path: str, as_of: str, profile_id: str | None,
                      cases_path: str | None, git_ref: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = os.path.join(tmp, "jobs.db")
        _copy_db(db_path, copy_path)
        db = JobStorage(copy_path)
        profile = profile_id or db.get_config("active_profile_id") or "unified_jc"
        feed = db.get_all_for_tracker(profile)
        counts = _table_counts(copy_path)
        dashboard = _dashboard_at(db, as_of)
        stats = db.get_stats(profile)
        last_run = db.get_last_run(profile)
        applications = len(db.get_all_applications())
        companies = len(db.get_companies())
        contacts = len(db.get_all_contacts())
        cases = _load_cases(cases_path) if cases_path else []

    return {
        "meta": {
            "schema": 1,
            "as_of": as_of,
            "git_ref": git_ref,
            "db_path": os.path.abspath(db_path),
            "profile": profile,
        },
        "counts": counts,
        "feed": _feed_sections(feed),
        "scores": {"distribution": _score_distribution(feed)},
        "stats": stats,
        "dashboard": dashboard,
        "last_run": last_run,
        "applications": {"count": applications},
        "companies": {"count": companies},
        "contacts": {"count": contacts},
        "regression_cases": _regression_results(feed, cases),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic parity fingerprint of app-visible state.",
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--as-of", required=True, help="YYYY-MM-DD to pin date.today() to")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--regression-cases", default=None,
                        help="path to regression_cases.json (default: sibling of this script)")
    parser.add_argument("--ref", default=None, help="git ref (default: short HEAD)")
    parser.add_argument("--out", default=None, help="write JSON here (default: stdout)")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.db):
        print(f"ERROR: DB not found: {args.db}", file=sys.stderr)
        return 1

    cases_path = args.regression_cases
    if cases_path is None:
        candidate = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "regression_cases.json"
        )
        cases_path = candidate if os.path.isfile(candidate) else None

    git_ref = args.ref or _git_ref()
    try:
        fp = build_fingerprint(args.db, args.as_of, args.profile, cases_path, git_ref)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the caller
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(fp, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(payload + "\n")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
