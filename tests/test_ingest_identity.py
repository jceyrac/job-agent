"""Identity test (spec 045, FR-006 / SC-002): the API path (`ingest_batch`)
produces identical results to the direct write path on copies of the same DB.

Scope: the broad-scrape write sequence only — engaged dedup + `upsert_company` +
`save_unscored` — replayed against a *fresh* DB (day-one semantics). This proves
`ingest_batch` is a faithful replacement for the direct writer for every case a
first scrape produces:

  * new postings (with `base_location`, `posted_date`, `description`, no company,
    no URL → fallback id),
  * company dedup (two postings, one company row),
  * engaged dedup (a posting matching an already-applied job is dropped and its
    `last_seen` touched).

A second test covers the *existing* non-engaged posting re-scraped with changed
fields: both paths refresh the same field set on conflict (`last_seen` +
`base_location` overwritten; `norm_title`/`norm_company`/`canonical_url`/
`company_id`/`monitored_company_id` COALESCE-backfilled).

`_now` is pinned so `first_seen`/`last_seen`/`*_at` timestamps are byte-identical
across both replays — the comparison is on real row values, not clocks.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from core.models import JobPosting
from core.scrape import dedupe_against_db
from core.storage import JobStorage

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


def _mk(source: str, title: str, company: str, url: str | None = None, *,
        location: str | None = None, description: str | None = None,
        base_location: str | None = None, posted_date: date | None = None) -> JobPosting:
    return JobPosting(
        source=source, title=title, company=company,
        location=location or "", url=url or "",
        description=description, base_location=base_location,
        posted_date=posted_date,
    )


def _serialize(job: JobPosting) -> dict:
    """The raw-field payload an orchestrator would POST — no computed fields."""
    return {
        "source": job.source, "title": job.title, "company": job.company,
        "location": job.location, "url": job.url,
        "description": job.description, "base_location": job.base_location,
        "posted_date": job.posted_date.isoformat() if job.posted_date else None,
        "monitored_company_id": job.monitored_company_id,
    }


def _direct_write(db: JobStorage, jobs: list[JobPosting]) -> None:
    """Replay the direct path's write sequence (`core/scrape._run_broad_scrape`)."""
    for job in dedupe_against_db(jobs, db):
        company_id = None
        if job.company and job.company.strip():
            try:
                company_id = db.upsert_company(job.company.strip())
            except ValueError:
                pass
        db.save_unscored(job, company_id=company_id,
                         monitored_company_id=job.monitored_company_id)


def _seed_engaged(db: JobStorage, job_id: str, title: str, company: str) -> None:
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, title, company, source, location, first_seen, last_seen) "
            "VALUES (?, ?, ?, 'board', 'Zurich', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:00:00+00:00')",
            (job_id, title, company))
        conn.execute(
            "INSERT INTO job_tracking (user_id, job_id, status, changed_at) "
            "VALUES (?, ?, 'applied', '2026-01-01T00:00:00+00:00')",
            (db.user_id, job_id))


def _seed_existing(db: JobStorage, job: JobPosting, *, base_location: str,
                   company_id: int | None = None, canonical_url: str | None = None,
                   norm_title: str | None = None, norm_company: str | None = None) -> None:
    """Seed a non-engaged job row that a re-scrape of `job` will hit on conflict."""
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, title, company, company_id, url, canonical_url, "
            "norm_title, norm_company, source, location, base_location, posted_date, "
            "description, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job.id, job.title, job.company, company_id, job.url, canonical_url,
             norm_title, norm_company, job.source, job.location, base_location,
             None, None, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))


def _snapshot(path: str) -> tuple[list[tuple], list[tuple]]:
    conn = sqlite3.connect(path)
    jobs = [tuple(r) for r in
            conn.execute(f"SELECT {', '.join(_JOBS_COLS)} FROM jobs ORDER BY id")]
    companies = [tuple(r) for r in
                 conn.execute(f"SELECT {', '.join(_COMPANY_COLS)} FROM companies ORDER BY id")]
    conn.close()
    return jobs, companies


def _fixture() -> list[JobPosting]:
    return [
        # New job + new company, with base_location, posted_date, description.
        _mk("board", "Staff Engineer", "Acme", "https://acme.com/jobs/1",
            location="Remote", description="Own the roadmap",
            base_location="United States", posted_date=date(2026, 10, 1)),
        # New job, same company → company dedup (one "Acme" row).
        _mk("board", "Senior PM", "Acme", "https://acme.com/jobs/2",
            location="Remote"),
        # Engaged dedup: matches the seeded "Backend Engineer" @ "Alpha Corp".
        _mk("board", "Backend Engineer", "Alpha Corp", "https://alpha.com/jobs/9"),
        # New job, no company.
        _mk("board", "Designer", "", "https://studio.com/1", location="Remote"),
        # New job, no URL → id from title::company::source.
        _mk("board", "PM", "Beta", None, location="Bern"),
    ]


def test_ingest_identity_direct_vs_api(tmp_path, monkeypatch):
    monkeypatch.setattr("core.storage._now", lambda: FIXED_NOW)

    # Two independent fresh DBs (a copy+reopen would re-trigger the greenhouse
    # board seed migration, giving the copies different company baselines).
    path_a = str(tmp_path / "a.db")
    path_b = str(tmp_path / "b.db")
    db_a = JobStorage(path_a)
    db_b = JobStorage(path_b)

    # Same engaged baseline in both (the engaged-key union ignores user_id, so
    # the differing default-user id across the two DBs is irrelevant here).
    _seed_engaged(db_a, "engaged-1", "Backend Engineer", "Alpha Corp")
    _seed_engaged(db_b, "engaged-1", "Backend Engineer", "Alpha Corp")

    jobs = _fixture()
    _direct_write(db_a, jobs)
    result = db_b.ingest_batch([_serialize(j) for j in jobs])

    # Same rows, same fields, byte-for-byte (timestamps pinned).
    assert _snapshot(path_a) == _snapshot(path_b)

    # Counts agree with the replay.
    assert result["new"] == 4            # Staff Engineer, Senior PM, Designer, PM
    assert result["existing"] == 0
    assert result["dropped_engaged"] == 1

    # The engaged row was touched in both copies.
    conn = sqlite3.connect(path_a)
    a_seen = conn.execute("SELECT last_seen FROM jobs WHERE id = 'engaged-1'").fetchone()[0]
    conn.close()
    conn = sqlite3.connect(path_b)
    b_seen = conn.execute("SELECT last_seen FROM jobs WHERE id = 'engaged-1'").fetchone()[0]
    conn.close()
    assert a_seen == b_seen == FIXED_NOW


def test_ingest_identity_existing_posting_refresh(tmp_path, monkeypatch):
    """An existing non-engaged posting re-scraped with changed fields must refresh
    the same field set on both paths (base_location overwritten, COALESCE
    backfills applied, last_seen touched) — the former last_seen-only divergence."""
    monkeypatch.setattr("core.storage._now", lambda: FIXED_NOW)

    path_a = str(tmp_path / "a.db")
    path_b = str(tmp_path / "b.db")
    db_a = JobStorage(path_a)
    db_b = JobStorage(path_b)

    # Same canonical URL (hence same id) as the seeded row, but with a changed
    # base_location and a company that must backfill a NULL company_id.
    job = _mk("board", "Staff Engineer", "Acme", "https://acme.com/jobs/1",
              location="Remote", description="Own the roadmap",
              base_location="Germany", posted_date=date(2026, 10, 1))

    for db in (db_a, db_b):
        _seed_existing(
            db, job,
            base_location="United States",       # diverges → must be overwritten
            canonical_url=job.canonical_url,     # id + canonical_url match
            norm_title=None,                     # COALESCE backfill path
            norm_company=None,
            company_id=None,                     # COALESCE backfill path
        )

    _direct_write(db_a, [job])
    result = db_b.ingest_batch([_serialize(job)])

    assert _snapshot(path_a) == _snapshot(path_b)
    assert result["new"] == 0
    assert result["existing"] == 1

    # base_location overwritten, last_seen touched, and the COALESCE fields
    # backfilled identically in both copies.
    for path in (path_a, path_b):
        conn = sqlite3.connect(path)
        base_location, last_seen, company_id, norm_title, norm_company = conn.execute(
            "SELECT base_location, last_seen, company_id, norm_title, norm_company "
            "FROM jobs WHERE id = ?", (job.id,)).fetchone()
        conn.close()
        assert base_location == "Germany"
        assert last_seen == FIXED_NOW
        assert company_id is not None
        assert norm_title is not None
        assert norm_company is not None
