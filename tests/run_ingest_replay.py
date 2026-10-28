#!/usr/bin/env python3
"""Record/replay identity harness (spec 045, FR-006 / US4 / T027).

Proves the direct ingestion writer and the API writer (`POST /v1/ingest/batch`)
produce identical DB state from the same postings, on two DBs that start from the
same baseline. It is the re-runnable tool behind the real-scrape gate (T028); the
synthetic `tests/test_ingest_identity.py` (T025) is the regression-test subset.

Modes
-----
    capture   Run the real scrapers against a live DB's ingest plan (broad or
              monitored) and write the *collected postings only* to a JSON
              fixture. No DB state is captured — the fixture is the list of raw
              payloads the orchestrator would POST. Requires network.

    replay    Load a fixture and replay it through two independent DBs:
                * DB A — the direct write path (`dedupe_against_db` +
                  `upsert_company` + `save_unscored`),
                * DB B — the API path (`TestClient` → `/v1/ingest/batch`),
              then diff jobs, companies, `last_seen` touches and counts.
              Deterministic and offline (`_now` is pinned).

    all       capture, then replay.

`--seed-db` points replay at copies of a populated DB (engaged dedup + existing
postings); without it the replay runs on two fresh DBs (new-posting identity).

Scope note (matches `test_ingest_identity.py`): for an *existing* non-engaged
posting, both paths run the same upsert — `ingest_batch` mirrors `save_unscored`'s
ON CONFLICT refresh (`last_seen` + `base_location` overwritten; `company_id`/
`canonical_url`/norm_* COALESCE-backfilled). With `--seed-db` the replay exercises
the fully-populated existing case, not just new postings.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

from core.models import JobPosting
from core.scrape import dedupe_against_db
from core.storage import JobStorage, _normalize_key

FIXED_NOW = "2026-02-02T00:00:00+00:00"

_JOBS_COLS = (
    "id", "title", "company", "company_id", "url", "canonical_url",
    "norm_title", "norm_company", "source", "location", "base_location",
    "posted_date", "description", "first_seen", "last_seen",
    "monitored_company_id", "filtered_non_product",
)

_COMPANY_COLS = (
    "id", "name", "name_normalized", "website", "careers_url",
    "first_seen_at", "last_seen_at", "created_at",
)

_API_CHUNK = 100  # mirrors the orchestrator's ≤ ~100 postings per request


# ── Capture ──────────────────────────────────────────────────────────────────

class _RecordingClient:
    """Stands in for `core.scrape._IngestClient` — records instead of POSTing."""

    def __init__(self):
        self.postings: list[dict] = []
        self.events: list[dict] = []

    def submit_batch(self, postings: list[dict]) -> dict:
        self.postings.extend(postings)
        return {"new": 0, "existing": 0, "dropped_engaged": 0}

    def submit_event(self, event: dict) -> dict:
        self.events.append(event)
        return {"ok": True}

    def close(self) -> None:
        pass


def _plan_from_db(db: JobStorage) -> dict:
    """Assemble the ingest plan exactly as `GET /v1/ingest/plan` does (R8).

    Reuses the router's own helpers (`_enabled_sources`, `_monitoring_gates`,
    `_monitored_targets`, `_watching_slugs`) so the captured run is driven by the
    same plan the API would serve — no re-implementation, no drift.
    """
    from api.schemas import BoardSlugs, IngestPlan
    from api.routers.ingest import (
        _enabled_sources,
        _monitored_targets,
        _monitoring_gates,
        _watching_slugs,
    )

    scope = db.get_ingestion_scope()
    return IngestPlan(
        title_families=scope["title_families"],
        exclude=scope["exclude"],
        remote_or_hybrid=scope["remote_or_hybrid"],
        search_locations=scope["search_locations"],
        greenhouse_boards=scope["greenhouse_boards"],
        freshness_days=db.get_freshness_days(),
        enabled_sources=_enabled_sources(db),
        monitoring_gates=_monitoring_gates(db),
        monitored_targets=_monitored_targets(db),
        board_slugs=BoardSlugs(
            lever=_watching_slugs(db, "lever"),
            workable=_watching_slugs(db, "workable"),
            greenhouse_watching=_watching_slugs(db, "greenhouse"),
        ),
    ).model_dump()


def _capture(db_path: str, out_path: str, monitored_only: bool) -> int:
    from core.scrape import _run_broad_scrape_via_api, _run_monitored_only_via_api

    db = JobStorage(db_path)
    plan = _plan_from_db(db)
    recorder = _RecordingClient()

    if monitored_only:
        _run_monitored_only_via_api(recorder, plan)
    else:
        _run_broad_scrape_via_api(recorder, plan)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(recorder.postings, f, indent=2, default=str)

    print(f"\ncaptured {len(recorder.postings)} postings → {out_path}")
    return 0


# ── Replay ───────────────────────────────────────────────────────────────────

def _posting_from_payload(d: dict) -> JobPosting:
    posted_date = date.fromisoformat(d["posted_date"]) if d.get("posted_date") else None
    return JobPosting(
        source=d.get("source") or "",
        title=d.get("title") or "",
        company=d.get("company") or "",
        location=d.get("location") or "",
        url=d.get("url") or "",
        description=d.get("description"),
        base_location=d.get("base_location"),
        posted_date=posted_date,
        monitored_company_id=d.get("monitored_company_id"),
    )


def _direct_write(db: JobStorage, jobs: list[JobPosting]) -> tuple[int, set[str]]:
    """Replay the direct path's write sequence (broad/monitored, pre-title-gated).

    Returns `(dropped_count, touched_engaged_ids)` — the number of postings the
    engaged dedup dropped, and the *distinct* engaged job_ids whose `last_seen`
    it touched. The distinct set is re-derived with the same shared
    `_normalize_key` `dedupe_against_db` uses, so duplicate postings (same
    normalised title+company → same engaged row) collapse to one id, exactly as
    `touch_many`'s set does.
    """
    kept = dedupe_against_db(jobs, db)
    for job in kept:
        company_id = None
        if job.company and job.company.strip():
            try:
                company_id = db.upsert_company(job.company.strip())
            except ValueError:
                pass
        db.save_unscored(job, company_id=company_id,
                         monitored_company_id=job.monitored_company_id)

    engaged_map = {}
    for row in db.get_engaged_job_keys():
        key = _normalize_key(row["title"], row["company"])
        if key:
            engaged_map[key] = row["id"]
    touched: set[str] = set()
    for job in jobs:
        key = _normalize_key(job.title, job.company)
        if key in engaged_map:
            touched.add(engaged_map[key])
    return len(jobs) - len(kept), touched


def _api_write(db: JobStorage, payloads: list[dict]) -> dict:
    """Replay the API path: POST the fixture to `/v1/ingest/batch` via TestClient."""
    from api.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    app.state.db_path = db.db_path
    client = TestClient(app)
    token = db.create_api_token("replay", ["ingest"], user_id="platform")
    headers = {"Authorization": f"Bearer {token}"}

    total = {"new": 0, "existing": 0, "dropped_engaged": 0, "outcomes": []}
    for i in range(0, len(payloads), _API_CHUNK):
        r = client.post("/v1/ingest/batch", json=payloads[i:i + _API_CHUNK],
                        headers=headers)
        if r.status_code != 200:
            print(f"  ❌ POST /v1/ingest/batch → HTTP {r.status_code}: {r.text[:300]}")
            sys.exit(1)
        d = r.json()
        total["new"] += d["new"]
        total["existing"] += d["existing"]
        total["dropped_engaged"] += d["dropped_engaged"]
        total["outcomes"].extend(d["outcomes"])
    return total


def _snapshot(path: str) -> tuple[list[tuple], list[tuple]]:
    conn = sqlite3.connect(path)
    jobs = [tuple(r) for r in
            conn.execute(f"SELECT {', '.join(_JOBS_COLS)} FROM jobs ORDER BY id")]
    companies = [tuple(r) for r in
                 conn.execute(f"SELECT {', '.join(_COMPANY_COLS)} FROM companies ORDER BY id")]
    conn.close()
    return jobs, companies


def _diff_rows(rows_a: list[tuple], rows_b: list[tuple],
               columns: tuple[str, ...], key_index: int) -> tuple[bool, list[str]]:
    """Return (equal, [human-readable diffs]) keyed by `columns[key_index]`."""
    map_a = {row[key_index]: row for row in rows_a}
    map_b = {row[key_index]: row for row in rows_b}
    diffs: list[str] = []

    only_a = sorted(set(map_a) - set(map_b))
    only_b = sorted(set(map_b) - set(map_a))
    for key in only_a:
        diffs.append(f"  only in A: {columns[key_index]}={key!r}")
    for key in only_b:
        diffs.append(f"  only in B: {columns[key_index]}={key!r}")

    for key in sorted(set(map_a) & set(map_b)):
        a, b = map_a[key], map_b[key]
        for i, col in enumerate(columns):
            if a[i] != b[i]:
                diffs.append(f"  {key!r}.{col}: A={a[i]!r} B={b[i]!r}")

    return (not diffs), diffs


def _replay(fixture_path: str, seed_db_path: str | None) -> int:
    with open(fixture_path, encoding="utf-8") as f:
        payloads = json.load(f)
    jobs = [_posting_from_payload(p) for p in payloads]

    # Pin the clock so first_seen/last_seen/*_at are byte-identical across paths.
    import core.storage as storage
    storage._now = lambda: FIXED_NOW

    tmp = Path(tempfile.mkdtemp(prefix="ingest_replay_"))
    path_a = str(tmp / "a.db")
    path_b = str(tmp / "b.db")

    if seed_db_path:
        shutil.copyfile(seed_db_path, path_a)
        shutil.copyfile(seed_db_path, path_b)
        baseline = f"copies of {seed_db_path}"
    else:
        baseline = "fresh DBs"
    db_a = JobStorage(path_a)
    db_b = JobStorage(path_b)

    dropped_direct, direct_touched = _direct_write(db_a, jobs)
    result = _api_write(db_b, payloads)

    jobs_a, comps_a = _snapshot(path_a)
    jobs_b, comps_b = _snapshot(path_b)

    jobs_ok, jobs_diffs = _diff_rows(jobs_a, jobs_b, _JOBS_COLS, key_index=0)
    comps_ok, comps_diffs = _diff_rows(comps_a, comps_b, _COMPANY_COLS, key_index=2)

    api_dropped_ids = {o["job_id"] for o in result["outcomes"]
                       if o["result"] == "dropped_engaged"}

    print(f"\nReplay baseline: {baseline} ({len(payloads)} postings)")
    print(f"  direct path:   dropped_engaged={dropped_direct} "
          f"({len(direct_touched)} distinct rows touched)")
    print(f"  API path:      new={result['new']} existing={result['existing']} "
          f"dropped_engaged={result['dropped_engaged']}")

    ok = True
    if jobs_ok:
        print(f"  jobs:          identical ({len(jobs_a)} rows)")
    else:
        ok = False
        print(f"  jobs:          ❌ DIFFER ({len(jobs_a)} vs {len(jobs_b)} rows)")
        for line in jobs_diffs:
            print(line)

    if comps_ok:
        print(f"  companies:     identical ({len(comps_a)} rows)")
    else:
        ok = False
        print(f"  companies:     ❌ DIFFER ({len(comps_a)} vs {len(comps_b)} rows)")
        for line in comps_diffs:
            print(line)

    if dropped_direct != result["dropped_engaged"]:
        ok = False
        print(f"  dropped-engaged count: ❌ direct={dropped_direct} "
              f"API={result['dropped_engaged']}")
    if direct_touched != api_dropped_ids:
        ok = False
        print(f"  dropped-engaged set: ❌ direct touched {len(direct_touched)} "
              f"ids, API dropped {len(api_dropped_ids)} ids")
        for jid in sorted(direct_touched ^ api_dropped_ids):
            print(f"    {jid!r}: direct={jid in direct_touched} "
                  f"api={jid in api_dropped_ids}")
    else:
        print(f"  dropped-engaged set: identical ({len(api_dropped_ids)} "
              f"distinct ids)")

    if ok:
        print("\n✅ IDENTITY HOLDS — direct and API writers produced identical state")
        shutil.rmtree(tmp, ignore_errors=True)
        return 0
    print(f"\n❌ IDENTITY DIVERGED — DB copies kept at {tmp} for inspection")
    return 1


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    from core.paths import DB_PATH

    parser = argparse.ArgumentParser(description="Record/replay ingest identity harness")
    sub = parser.add_subparsers(dest="mode", required=True)

    for name in ("capture", "all"):
        p = sub.add_parser(name)
        p.add_argument("--db", default=DB_PATH, help="DB to read the ingest plan from")
        p.add_argument("--out", required=True, help="JSON fixture to write")
        p.add_argument("--monitored-only", action="store_true",
                       help="capture the monitored-only run instead of broad")

    r = sub.add_parser("replay")
    r.add_argument("--in", dest="fixture", required=True, help="JSON fixture to replay")
    r.add_argument("--seed-db", help="populated DB to copy for both replay DBs")

    args = parser.parse_args()

    if args.mode == "replay":
        return _replay(args.fixture, args.seed_db)

    rc = _capture(args.db, args.out, args.monitored_only)
    if rc == 0 and args.mode == "all":
        rc = _replay(args.out, None)
    return rc


if __name__ == "__main__":
    sys.exit(main())
