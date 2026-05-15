"""
tests/test_storage.py — storage layer unit tests

All tests use an in-memory SQLite DB (:memory:) so they never touch
data/jobs.db and are safe to run at any time.

Usage:
    python tests/test_storage.py
"""

import sys
import os
import dataclasses
import sqlite3
import traceback
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import JobPosting
from storage import JobStorage, _normalize_company_name, COMPANY_STATUSES
from tracker_views.shared import (
    score_badge, company_status_badge, relationship_badge,
    sector_label, unverified_badge, apply_filters,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _job(url: str = "https://example.com/jobs/1", **kwargs) -> JobPosting:
    """Return a fully-populated JobPosting fixture."""
    defaults = dict(
        source="TestSource",
        title="Senior Product Manager",
        company="Acme Corp",
        location="Remote",
        url=url,
        posted_date=date(2026, 4, 1),
        description="Lead our crypto product team.",
        tags=["crypto", "web3"],
        salary="$120k–$160k",
        work_mode="remote",
        base_location="United States",
        company_size="startup",
        contract_type="permanent",
        geo_zone="us_only",
    )
    defaults.update(kwargs)
    return JobPosting(**defaults)


def _score(score: int = 8) -> dict:
    return {
        "score":            score,
        "reason":           "Strong crypto PM fit",
        "summary":          "Crypto-focused PM role at a startup.",
        "work_mode":        "remote",
        "geo_zone":         "us_only",
        "company_size":     "startup",
        "contract_type":    "permanent",
        "scored_by":        "mock",
        "company_country":  "United States",
        "industry_sector":  "web3_crypto",
        "language_required": "english",
    }


PROFILE_ID  = "test_profile"
PROFILE_ID2 = "test_profile_2"


class _FakeProfile:
    id = PROFILE_ID
    name = "Test Profile"
    def to_criteria_dict(self): return {}


class _FakeProfile2:
    id = PROFILE_ID2
    name = "Test Profile 2"
    def to_criteria_dict(self): return {}


# ── Test runner ───────────────────────────────────────────────────────────────

_results: list[tuple[str, bool, str]] = []


def _run(fn):
    try:
        fn()
        _results.append((fn.__name__, True, ""))
        print(f"  ✅ {fn.__name__}")
    except Exception as e:
        _results.append((fn.__name__, False, str(e)))
        print(f"  ❌ {fn.__name__}: {e}")
        traceback.print_exc()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_db_initialises():
    db = JobStorage(":memory:")
    with db._conn() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "jobs"             in tables, "missing table: jobs"
    assert "job_scores"       in tables, "missing table: job_scores"
    assert "job_tracking"     in tables, "missing table: job_tracking"
    assert "job_applications"  in tables, "missing table: job_applications"
    assert "search_profiles"  in tables, "missing table: search_profiles"


def test_schema_matches_model():
    """Every JobPosting field (excluding computed/excluded) maps to a jobs column."""
    db = JobStorage(":memory:")
    with db._conn() as conn:
        db_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}

    model_fields = {f.name for f in dataclasses.fields(JobPosting)}
    scorer_owned = {"work_mode", "company_size", "contract_type", "geo_zone", "summary"}
    excluded = {"tags", "salary"}
    # Phase 4: company-level fields moved to companies table
    company_owned = {"company_country", "industry_sector"}

    required_in_jobs = model_fields - scorer_owned - excluded - company_owned
    missing = required_in_jobs - db_cols
    assert not missing, f"Fields in JobPosting but missing from jobs table: {missing}"


def test_job_scores_has_no_status_column():
    """status/notes must live in job_tracking, not job_scores."""
    db = JobStorage(":memory:")
    with db._conn() as conn:
        score_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_scores)").fetchall()}
        track_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_tracking)").fetchall()}
    assert "status" not in score_cols, "status column should not be in job_scores"
    assert "notes"  not in score_cols, "notes column should not be in job_scores"
    assert "status" in track_cols, "status column missing from job_tracking"
    assert "notes"  in track_cols, "notes column missing from job_tracking"


def test_job_applications_has_required_columns():
    """job_applications must have the core prepare.py columns."""
    db = JobStorage(":memory:")
    with db._conn() as conn:
        app_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_applications)").fetchall()}
    assert "job_id" in app_cols, "job_id missing from job_applications"
    assert "profile_id" in app_cols, "profile_id missing from job_applications"
    assert "cover_letter" in app_cols, "cover_letter missing from job_applications"
    assert "prepared_by" in app_cols, "prepared_by missing from job_applications"


def test_upsert_and_retrieve():
    """save_scored → get_digest round-trips all key fields correctly."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())

    job = _job()
    db.save_scored(job, _score(8), PROFILE_ID)

    rows = db.get_digest(PROFILE_ID, min_score=5)
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    r = rows[0]

    assert r["title"]         == job.title,        f"title mismatch: {r['title']!r}"
    assert r["company"]       == job.company,       f"company mismatch"
    assert r["url"]           == job.url,           f"url mismatch"
    assert r["location"]      == job.location,      f"location mismatch"
    assert r["base_location"] == job.base_location, f"base_location mismatch: {r['base_location']!r}"
    assert r["source"]        == job.source,        f"source mismatch"
    assert r["score"]         == 8,                 f"score mismatch: {r['score']}"
    assert r["work_mode"]     == "remote",          f"work_mode mismatch"


def test_cache_split():
    """A scored job goes to cached; an unseen job goes to new."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())

    scored_job = _job("https://example.com/jobs/scored")
    new_job    = _job("https://example.com/jobs/new")

    db.save_scored(scored_job, _score(7), PROFILE_ID)

    new_jobs, cached_jobs = db.split_new_cached([scored_job, new_job], PROFILE_ID)
    assert len(new_jobs)    == 1, f"Expected 1 new, got {len(new_jobs)}"
    assert len(cached_jobs) == 1, f"Expected 1 cached, got {len(cached_jobs)}"
    assert new_jobs[0].url    == new_job.url
    assert cached_jobs[0]["url"] == scored_job.url


def test_no_rescore_on_second_run():
    """Simulates two consecutive runs — the second produces 0 new jobs."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())

    jobs = [_job(f"https://example.com/jobs/{i}") for i in range(5)]

    new1, cached1 = db.split_new_cached(jobs, PROFILE_ID)
    assert len(new1) == 5 and len(cached1) == 0, "First run: all should be new"

    for job in new1:
        db.save_scored(job, _score(), PROFILE_ID)

    new2, cached2 = db.split_new_cached(jobs, PROFILE_ID)
    assert len(new2)    == 0, f"Second run: expected 0 new, got {len(new2)}"
    assert len(cached2) == 5, f"Second run: expected 5 cached, got {len(cached2)}"


def test_id_stability():
    """Two JobPosting objects with the same URL always produce the same id."""
    url = "https://example.com/jobs/stable"
    j1 = JobPosting(source="A", title="PM",    company="X", location="Remote", url=url)
    j2 = JobPosting(source="B", title="Other", company="Y", location="NYC",    url=url, posted_date=date.today())
    assert j1.id == j2.id, f"IDs differ for same URL: {j1.id!r} vs {j2.id!r}"

    j3 = JobPosting(source="S", title="T", company="C", location="L", url="")
    j4 = JobPosting(source="S", title="T", company="C", location="L", url="")
    assert j3.id == j4.id, "IDs differ for same title+company+source"


def test_status_update():
    """set_status persists status and notes in job_tracking (profile-independent)."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())

    job = _job()
    db.save_scored(job, _score(), PROFILE_ID)

    db.set_status(job.id, "queued", notes="looks good")

    with db._conn() as conn:
        row = conn.execute(
            "SELECT status, notes FROM job_tracking WHERE job_id = ?",
            (job.id,),
        ).fetchone()
    assert row is not None,            "no job_tracking row found"
    assert row["status"] == "queued",  f"status not updated: {row['status']!r}"
    assert row["notes"]  == "looks good", f"notes not updated: {row['notes']!r}"


def test_status_is_profile_independent():
    """Setting status for one profile is visible when querying via another profile."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    db.upsert_profile(_FakeProfile2())

    job = _job()
    db.save_scored(job, _score(7), PROFILE_ID)
    db.save_scored(job, _score(5), PROFILE_ID2)

    db.set_status(job.id, "applied")

    # Status must be "applied" regardless of which profile we query through
    tracker1 = db.get_all_for_tracker(PROFILE_ID)
    tracker2 = db.get_all_for_tracker(PROFILE_ID2)

    j1 = next(j for j in tracker1 if j["id"] == job.id)
    j2 = next(j for j in tracker2 if j["id"] == job.id)

    assert j1["status"] == "applied", f"wrong status via profile 1: {j1['status']!r}"
    assert j2["status"] == "applied", f"wrong status via profile 2: {j2['status']!r}"


def test_invalid_status_rejected():
    """set_status raises ValueError for an unrecognised status string."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    job = _job()
    db.save_scored(job, _score(), PROFILE_ID)

    try:
        db.set_status(job.id, "banana")
        raise AssertionError("Expected ValueError was not raised")
    except ValueError:
        pass  # expected


def test_status_history_append_only():
    """Each set_status call inserts a row into status_history."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    job = _job()
    db.save_scored(job, _score(), PROFILE_ID)

    db.set_status(job.id, "queued")
    db.set_status(job.id, "ready")
    db.set_status(job.id, "applied")

    with db._conn() as conn:
        rows = conn.execute(
            "SELECT status FROM status_history WHERE job_id = ? ORDER BY changed_at",
            (job.id,),
        ).fetchall()
    history = [r["status"] for r in rows]
    assert history == ["queued", "ready", "applied"], \
        f"Expected queued→ready→applied, got {history!r}"


def test_status_history_present_in_tracker():
    """Tracker queries return status_changed_at."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    job = _job()
    db.save_scored(job, _score(), PROFILE_ID)
    db.set_status(job.id, "queued")

    rows = db.get_all_for_tracker(PROFILE_ID)
    j = next(r for r in rows if r["id"] == job.id)
    assert j["status_changed_at"] is not None, "status_changed_at should not be None"


def test_application_persistence():
    """save_prepared_application and get_application round-trip correctly."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    db.upsert_profile(_FakeProfile2())

    job = _job()
    db.save_scored(job, _score(9), PROFILE_ID)
    db.save_scored(job, _score(6), PROFILE_ID2)

    db.save_prepared_application(
        job_id=job.id,
        profile_id=PROFILE_ID,
        cover_letter="Dear Hiring Manager...",
        cv_bullets_selected={"bullets": [{"text": "Led API product", "rationale": "Matches JD"}]},
        company_research={"what_company_does": "A fintech startup"},
        screening_answers={"why_company": "I admire your work"},
        language="en",
        prepared_by="llama-3.3-70b-versatile",
    )

    app = db.get_application(job.id)
    assert app is not None, "get_application returned None"
    assert app["cover_letter"] == "Dear Hiring Manager...", f"cover_letter mismatch"
    assert app["profile_id"] == PROFILE_ID, f"profile_id mismatch: {app['profile_id']!r}"
    assert app["language"] == "en", f"language mismatch: {app['language']!r}"

    # Status should have been promoted to 'ready'
    with db._conn() as conn:
        row = conn.execute("SELECT status FROM job_tracking WHERE job_id = ?", (job.id,)).fetchone()
    assert row is not None, "no job_tracking row after save_prepared_application"
    assert row["status"] == "ready", f"status not promoted to ready: {row['status']!r}"


def test_get_digest():
    """get_digest returns only jobs above min_score, ordered by score desc."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())

    for i, score in enumerate([4, 7, 9]):
        job = _job(url=f"https://example.com/jobs/{i}", title=f"Job score {score}")
        db.save_scored(job, _score(score), PROFILE_ID)

    digest = db.get_digest(PROFILE_ID, min_score=5)
    assert len(digest) == 2, f"Expected 2 results (score≥5), got {len(digest)}"
    assert digest[0]["score"] == 9, f"First result should be score=9, got {digest[0]['score']}"
    assert digest[1]["score"] == 7, f"Second result should be score=7, got {digest[1]['score']}"


def test_rejected_excluded_from_scoring():
    """Jobs with status='rejected' or 'archived' are not returned by get_jobs_for_scoring."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())

    job_a = _job("https://example.com/a", posted_date=date.today())
    job_b = _job("https://example.com/b", posted_date=date.today())

    db.save_unscored(job_a)
    db.save_unscored(job_b)
    db.set_status(job_a.id, "rejected")

    to_score = db.get_jobs_for_scoring(PROFILE_ID)
    ids = {j["id"] for j in to_score}
    assert job_a.id not in ids, "rejected job should be excluded from scoring"
    assert job_b.id in ids,     "non-rejected job should be included"


def test_best_score_view_status():
    """get_all_jobs_best_score returns the single profile-independent status."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    db.upsert_profile(_FakeProfile2())

    job = _job()
    db.save_scored(job, _score(9), PROFILE_ID)   # high score, new
    db.save_scored(job, _score(5), PROFILE_ID2)  # low score, ready
    db.set_status(job.id, "ready")

    results = db.get_all_jobs_best_score()
    r = next(j for j in results if j["id"] == job.id)
    assert r["score"]  == 9,       f"expected best score=9, got {r['score']}"
    assert r["status"] == "ready", f"expected status=ready, got {r['status']!r}"


def _apply_digest_filters(jobs: list[dict], profile) -> list[dict]:
    """Mirror of score.py's post-scoring filter logic for unit testing."""
    result = []
    for j in jobs:
        geo_zone          = j.get("geo_zone", "unknown")
        work_mode         = j.get("work_mode", "unknown")
        company_country   = j.get("company_country", "unknown")
        industry_sector   = j.get("industry_sector", "other")
        language_required = j.get("language_required", "unknown")

        allowed_geo   = getattr(profile, "allowed_geo_zones", [])
        allowed_mode  = getattr(profile, "allowed_work_modes", [])
        allowed_ctry  = getattr(profile, "allowed_countries", None)
        excl_sectors  = getattr(profile, "excluded_sectors", [])
        excl_langs    = getattr(profile, "excluded_languages", [])

        if allowed_geo and geo_zone and geo_zone not in allowed_geo:
            continue
        if allowed_mode and work_mode and work_mode not in allowed_mode:
            continue
        if allowed_ctry is not None and company_country != "unknown" and company_country not in allowed_ctry:
            continue
        if industry_sector in excl_sectors:
            continue
        if language_required in excl_langs:
            continue
        result.append(j)
    return result


class _ProfileAllFilters:
    allowed_geo_zones  = ["europe"]
    allowed_work_modes = ["hybrid", "remote"]
    allowed_countries  = ["Switzerland"]
    excluded_sectors   = ["pharma", "retail"]
    excluded_languages = ["german"]


class _ProfileNoFilters:
    allowed_geo_zones  = []
    allowed_work_modes = []
    allowed_countries  = None
    excluded_sectors   = []
    excluded_languages = []


def _job_dict(**overrides) -> dict:
    base = {
        "geo_zone":          "europe",
        "work_mode":         "hybrid",
        "company_country":   "Switzerland",
        "industry_sector":   "fintech",
        "language_required": "english",
        "score":             7,
    }
    base.update(overrides)
    return base


def test_filter_country_known_not_in_allowlist_excluded():
    jobs = [_job_dict(company_country="Germany")]
    assert _apply_digest_filters(jobs, _ProfileAllFilters()) == []


def test_filter_country_known_in_allowlist_kept():
    jobs = [_job_dict(company_country="Switzerland")]
    assert len(_apply_digest_filters(jobs, _ProfileAllFilters())) == 1


def test_filter_country_unknown_always_passes():
    jobs = [_job_dict(company_country="unknown")]
    assert len(_apply_digest_filters(jobs, _ProfileAllFilters())) == 1


def test_filter_country_none_means_no_restriction():
    jobs = [_job_dict(company_country="United States")]
    assert len(_apply_digest_filters(jobs, _ProfileNoFilters())) == 1


def test_filter_sector_excluded():
    jobs = [_job_dict(industry_sector="pharma")]
    assert _apply_digest_filters(jobs, _ProfileAllFilters()) == []


def test_filter_sector_not_excluded_kept():
    jobs = [_job_dict(industry_sector="fintech")]
    assert len(_apply_digest_filters(jobs, _ProfileAllFilters())) == 1


def test_filter_sector_empty_list_keeps_all():
    jobs = [_job_dict(industry_sector="pharma")]
    assert len(_apply_digest_filters(jobs, _ProfileNoFilters())) == 1


def test_filter_language_excluded():
    jobs = [_job_dict(language_required="german")]
    assert _apply_digest_filters(jobs, _ProfileAllFilters()) == []


def test_filter_language_not_excluded_kept():
    jobs = [_job_dict(language_required="english")]
    assert len(_apply_digest_filters(jobs, _ProfileAllFilters())) == 1


def test_filter_language_empty_list_keeps_all():
    jobs = [_job_dict(language_required="german")]
    assert len(_apply_digest_filters(jobs, _ProfileNoFilters())) == 1


def test_filter_all_inactive_keeps_all():
    jobs = [
        _job_dict(company_country="United States", industry_sector="pharma", language_required="german"),
    ]
    assert len(_apply_digest_filters(jobs, _ProfileNoFilters())) == 1


def test_score_new_fields_round_trip():
    """company_country, industry_sector, language_required persist and read back correctly."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    cid = db.upsert_company("Acme Corp")
    job = _job()
    score = _score()
    score["company_country"]   = "Switzerland"
    score["industry_sector"]   = "fintech"
    score["language_required"] = "french"
    db.save_scored(job, score, PROFILE_ID, company_id=cid)

    rows = db.get_digest(PROFILE_ID, min_score=5)
    assert len(rows) == 1
    r = rows[0]
    assert r["company_country"]   == "Switzerland", f"Got {r['company_country']!r}"
    assert r["industry_sector"]   == "fintech",     f"Got {r['industry_sector']!r}"
    assert r["language_required"] == "french",      f"Got {r['language_required']!r}"


def test_pre_migration_null_defaults():
    """Rows with NULLs in the three new columns (pre-migration) read back with correct defaults."""
    from storage import _now
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    job = _job()

    # Insert job and a minimal score row without the three new fields
    with db._conn() as conn:
        conn.execute(
            """INSERT INTO jobs (id, title, company, url, source, location,
               base_location, posted_date, description, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job.id, job.title, job.company, job.url, job.source, job.location,
             job.base_location, str(job.posted_date), job.description, _now(), _now()),
        )
        conn.execute(
            """INSERT INTO job_scores (job_id, profile_id, score, scored_at)
               VALUES (?, ?, ?, ?)""",
            (job.id, PROFILE_ID, 7, _now()),
        )

    rows = db.get_digest(PROFILE_ID, min_score=5)
    assert len(rows) == 1
    r = rows[0]
    assert r["company_country"]   == "unknown", f"Got {r['company_country']!r}"
    assert r["industry_sector"]   == "other",   f"Got {r['industry_sector']!r}"
    assert r["language_required"] == "unknown", f"Got {r['language_required']!r}"


def test_migration_idempotent():
    """Running the migration twice must not raise or double-backfill data."""
    from models import JobPosting
    from datetime import date

    job = JobPosting(
        source="Test", title="PM", company="Acme", location="Remote",
        url="https://example.com/idem", posted_date=date(2026, 4, 1),
    )

    # First init — creates tables + runs migration
    db1 = JobStorage(":memory:")
    db1.upsert_profile(_FakeProfile())
    cid = db1.upsert_company("Acme")
    db1.save_scored(job, _score(7), PROFILE_ID, company_id=cid)

    # Second init on the same DB — migration should be a no-op
    db2 = JobStorage(":memory:")
    with db2._conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    # Job-level fields should still be on jobs
    for col in ("summary", "work_mode", "geo_zone", "contract_type",
                "language_required", "extracted_at"):
        assert col in cols, f"Column {col!r} missing after re-init"
    # Company-level fields should NOT be on jobs (Phase 5 dropped them)
    for col in ("company_country", "industry_sector", "company_size"):
        if col in cols:
            # SQLite < 3.35 — column left as dead weight, which is OK
            pass

    # Company enrichment columns should be on companies table
    with db2._conn() as conn:
        comp_cols = {r[1] for r in conn.execute("PRAGMA table_info(companies)").fetchall()}
    for col in ("company_country", "industry_sector", "company_size",
                "enriched_at", "enriched_by"):
        assert col in comp_cols, f"Column {col!r} missing from companies after re-init"

    # Reading back must still work (db2 is a fresh in-memory DB — create company)
    db2.upsert_profile(_FakeProfile())
    cid2 = db2.upsert_company("Acme")
    db2.save_scored(job, _score(7), PROFILE_ID, company_id=cid2)
    rows = db2.get_digest(PROFILE_ID, min_score=5)
    assert len(rows) == 1


def test_backfill_correctness():
    """save_scored writes company fields to companies table; re-init backfills correctly."""
    from datetime import date

    jobs_data = [
        ("https://example.com/j1", "Company A", "Switzerland", "fintech", "english", 8),
        ("https://example.com/j2", "Company B", "Germany", "ai_ml", "german", 7),
        ("https://example.com/j3", "Company C", "United States", "web3_crypto", "english", 9),
    ]

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        db = JobStorage(tmp_path)
        db.upsert_profile(_FakeProfile())
        for url, company, country, sector, lang, score in jobs_data:
            cid = db.upsert_company(company)
            job = JobPosting(
                source="Test", title="PM", company=company, location="Remote",
                url=url, posted_date=date(2026, 4, 1),
            )
            sd = _score(score)
            sd.update({"company_country": country, "industry_sector": sector,
                       "language_required": lang})
            db.save_scored(job, sd, PROFILE_ID, company_id=cid)
        del db

        # Re-initialize — Phase 4 backfill + Phase 5 DROP COLUMN run
        db2 = JobStorage(tmp_path)

        # Verify enrichment landed on companies table
        with db2._conn() as conn:
            rows = conn.execute(
                "SELECT company_country, industry_sector, enriched_at "
                "FROM companies WHERE company_country IS NOT NULL "
                "ORDER BY company_country"
            ).fetchall()
        assert len(rows) >= 2, f"Expected ≥2 enriched companies, got {len(rows)}"
        countries = {r["company_country"] for r in rows}
        assert "Switzerland" in countries
        assert "Germany" in countries
        for r in rows:
            assert r["enriched_at"] is not None, \
                f"enriched_at should not be NULL for {r['company_country']}"

        # Verify get_digest reads company fields via JOIN
        db2.upsert_profile(_FakeProfile())
        digest = db2.get_digest(PROFILE_ID, min_score=5)
        assert len(digest) == 3
        for j in digest:
            assert j["company_country"] in ("Switzerland", "Germany", "United States")
            assert j["industry_sector"] in ("fintech", "ai_ml", "web3_crypto")
            assert j["language_required"] in ("english", "german")

    finally:
        os.unlink(tmp_path)


def test_dedupe_against_db():
    """Cross-batch dedup drops scraped jobs matching an already-engaged row."""
    from scrape import dedupe_against_db
    from datetime import date as dt_date

    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())

    # Pre-populate: a job the user already applied to
    existing = JobPosting(
        source="LinkedIn", title="Senior PM", company="Stripe",
        location="Remote", url="https://example.com/stripe-spm",
        posted_date=dt_date(2026, 5, 1), description="A PM role at Stripe.",
    )
    db.save_unscored(existing)
    db.set_status(existing.id, "applied")

    # Capture original last_seen before dedup
    with db._conn() as conn:
        orig = conn.execute(
            "SELECT last_seen FROM jobs WHERE id = ?", (existing.id,)
        ).fetchone()
    assert orig is not None
    original_last_seen = orig["last_seen"]

    # New scrape batch includes a near-duplicate: slightly different title, trailing space
    new_batch = [
        JobPosting(
            source="Indeed", title="Senior Product Manager", company="Stripe ",
            location="Remote", url="https://example.com/stripe-spm-2",
            posted_date=dt_date(2026, 5, 10), description="PM role at Stripe.",
        ),
        JobPosting(
            source="Indeed", title="Staff Engineer", company="Stripe",
            location="Remote", url="https://example.com/stripe-se",
            posted_date=dt_date(2026, 5, 10), description="Eng role.",
        ),
    ]

    result = dedupe_against_db(new_batch, db)

    # "Senior Product Manager" @ "Stripe" should match the existing "Senior PM" @ "Stripe"
    assert len(result) == 1, f"Expected 1 survivor, got {len(result)}"
    assert result[0].title == "Staff Engineer"

    # The original row's last_seen should be updated (different from before dedup)
    with db._conn() as conn:
        updated = conn.execute(
            "SELECT last_seen FROM jobs WHERE id = ?", (existing.id,)
        ).fetchone()
    assert updated is not None
    assert updated["last_seen"] > original_last_seen, \
        f"last_seen should have been updated: {original_last_seen} → {updated['last_seen']}"


def test_dedupe_against_db_normalization():
    """Parentheticals like ' (m/w/d)' and ' (Remote)' are stripped before comparing."""
    from scrape import _normalize_key

    k1 = _normalize_key("Product Manager (m/w/d)", "Acme")
    k2 = _normalize_key("Product Manager", "Acme")
    assert k1 == k2

    k3 = _normalize_key("Head of Product (Remote)", "Foo Inc.")
    k4 = _normalize_key("Head of Product", "Foo Inc.")
    assert k3 == k4


# ── Company normalization ─────────────────────────────────────────────────────

def test_normalize_company_name():
    assert _normalize_company_name("ConsenSys Software Inc.") == "consensys"
    assert _normalize_company_name("Consensys") == "consensys"
    assert _normalize_company_name("FELFEL AG") == "felfel"
    assert _normalize_company_name("Aave Companies LLC") == "aave"
    assert _normalize_company_name("UBS Group") == "ubs"
    # Edge cases
    assert _normalize_company_name("") == ""
    assert _normalize_company_name(None) == ""
    assert _normalize_company_name("   Acme Corp.   ") == "acme"
    assert _normalize_company_name("The Best Company Ltd.") == "best company"
    assert _normalize_company_name("Something Labs, Inc.") == "something"
    assert _normalize_company_name("Foo Technologies") == "foo"
    assert _normalize_company_name("Bar Solutions Systems") == "bar"
    # Single word
    assert _normalize_company_name("Google") == "google"
    # Unicode — Société Générale → strips géniale but name_normalized is consistent
    assert _normalize_company_name("Societe Generale SA") == "societe generale"


def test_upsert_company_dedupes():
    db = JobStorage(":memory:")
    id1 = db.upsert_company("ConsenSys")
    id2 = db.upsert_company("ConsenSys Software Inc.")
    assert id1 == id2, f"Expected same id for same normalization, got {id1} vs {id2}"
    id3 = db.upsert_company("consensys")
    assert id3 == id1, f"Expected same id for lowercase variant, got {id3} vs {id1}"


def test_blacklist_filter_excludes_jobs():
    from datetime import date as dt_date
    db = JobStorage(":memory:")
    # Create a profile first
    db.upsert_profile(_FakeProfile())
    # Create a company and blacklist it
    cid = db.upsert_company("Bad Corp")
    db.set_company_status(cid, "blacklisted", note="test blacklist")
    # Save a job linked to the blacklisted company (use recent date to pass 30d filter)
    today = dt_date.today()
    job = _job(company="Bad Corp", posted_date=today)
    db.save_unscored(job, company_id=cid)
    # Save a job linked to an un-blacklisted company
    cid2 = db.upsert_company("Good Corp")
    job2 = _job(company="Good Corp", url="https://example.com/jobs/2", posted_date=today)
    db.save_unscored(job2, company_id=cid2)
    # get_jobs_for_scoring should exclude the blacklisted job
    jobs = db.get_jobs_for_scoring(PROFILE_ID)
    titles = {j["company"] for j in jobs}
    assert "Bad Corp" not in titles, "Blacklisted company job should be excluded"
    assert "Good Corp" in titles, "Non-blacklisted company job should be included"
    blacklisted_count = db.count_blacklisted_jobs()
    assert blacklisted_count >= 1


def test_company_status_history_logs_transitions():
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    # Initial insert should have no history row (default status)
    with db._conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM company_status_history WHERE company_id = ?",
            (cid,)).fetchone()[0]
    assert count == 0, f"Expected 0 history rows after initial insert, got {count}"
    # Transition to another status
    db.set_company_status(cid, "active_outreach", note="first outreach")
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM company_status_history WHERE company_id = ? ORDER BY changed_at",
            (cid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "active_outreach"
    assert rows[0]["note"] == "first outreach"
    # Another transition
    db.set_company_status(cid, "engaged", note="response received")
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT status FROM company_status_history WHERE company_id = ? ORDER BY changed_at",
            (cid,)).fetchall()
    assert len(rows) == 2
    assert rows[0]["status"] == "active_outreach"
    assert rows[1]["status"] == "engaged"


def test_company_enrichment_write_once():
    """Calling update_company_enrichment twice does not overwrite existing values."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    # First write — should succeed (all fields NULL initially)
    fields1 = {
        "company_country": "Switzerland",
        "industry_sector": "fintech",
        "company_size": "startup",
        "extracted_by": "model-v1",
    }
    ok = db.update_company_enrichment(cid, fields1, enriched_by="model-v1")
    assert ok, "First enrichment should write"

    # Verify first write landed
    with db._conn() as conn:
        row = conn.execute(
            "SELECT company_country, industry_sector, company_size, enriched_by "
            "FROM companies WHERE id = ?", (cid,)
        ).fetchone()
    assert row["company_country"] == "Switzerland"
    assert row["industry_sector"] == "fintech"
    assert row["company_size"] == "startup"
    assert row["enriched_by"] == "model-v1"

    # Second write — should NOT overwrite (all fields already set)
    fields2 = {
        "company_country": "Germany",
        "industry_sector": "ai_ml",
        "company_size": "large",
        "extracted_by": "model-v2",
    }
    ok = db.update_company_enrichment(cid, fields2, enriched_by="model-v2")
    assert not ok, "Second enrichment should be a no-op (fields already set)"

    # Verify values unchanged
    with db._conn() as conn:
        row = conn.execute(
            "SELECT company_country, industry_sector, company_size, enriched_by "
            "FROM companies WHERE id = ?", (cid,)
        ).fetchone()
    assert row["company_country"] == "Switzerland", "company_country should not be overwritten"
    assert row["industry_sector"] == "fintech", "industry_sector should not be overwritten"
    assert row["company_size"] == "startup", "company_size should not be overwritten"
    assert row["enriched_by"] == "model-v1", "enriched_by should not be overwritten"

    # Create a fresh company with "unknown" values — enrichment should overwrite them
    cid2 = db.upsert_company("EmptyCo")
    with db._conn() as conn:
        conn.execute(
            "UPDATE companies SET company_country='unknown', industry_sector='other', company_size='unknown' WHERE id = ?",
            (cid2,))
    ok = db.update_company_enrichment(cid2, {
        "company_country": "France",
        "industry_sector": "tech_saas",
        "company_size": "large",
    }, enriched_by="model-v3")
    assert ok, "Should overwrite 'unknown'/'other' defaults"
    with db._conn() as conn:
        row = conn.execute(
            "SELECT company_country, industry_sector, company_size FROM companies WHERE id = ?",
            (cid2,)).fetchone()
    assert row["company_country"] == "France", f"Should overwrite unknown, got {row['company_country']!r}"
    assert row["industry_sector"] == "tech_saas", f"Should overwrite other, got {row['industry_sector']!r}"
    assert row["company_size"] == "large", f"Should overwrite unknown, got {row['company_size']!r}"


def test_backfill_pulls_latest_extracted():
    """Backfill picks the most recent non-null values across a company's jobs."""
    import tempfile
    import sqlite3 as _sqlite3

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        # Build a pre-Phase-4 database manually: companies + jobs with old columns.
        # We bypass JobStorage so Phase 4 doesn't run prematurely.
        raw = _sqlite3.connect(tmp_path)
        raw.executescript("""
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                name_normalized TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'prospect',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT,
                company_id INTEGER REFERENCES companies(id),
                url TEXT,
                source TEXT,
                location TEXT,
                base_location TEXT,
                posted_date TEXT,
                description TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                summary TEXT,
                work_mode TEXT,
                geo_zone TEXT,
                company_size TEXT,
                contract_type TEXT,
                company_country TEXT,
                industry_sector TEXT,
                language_required TEXT,
                extracted_at TEXT,
                extracted_by TEXT
            );
            CREATE TABLE search_profiles (id TEXT PRIMARY KEY, name TEXT, criteria TEXT);
            CREATE TABLE job_scores (
                job_id TEXT NOT NULL, profile_id TEXT NOT NULL,
                score INTEGER, reason TEXT, scored_by TEXT, scored_at TEXT,
                PRIMARY KEY (job_id, profile_id)
            );
        """)
        raw.execute("INSERT INTO companies VALUES (1, 'MultiJobCo', 'multijobco', 'prospect', '2026-01-01', '2026-01-01', '2026-01-01')")
        raw.commit()
        cid = 1

        # Job 1: older → "Switzerland / fintech"
        raw.execute(
            "INSERT INTO jobs (id, title, company, company_id, url, source, location, first_seen, last_seen, "
            "company_country, industry_sector, company_size, extracted_at, extracted_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("job1", "PM", "MultiJobCo", cid, "http://j1", "test", "Remote",
             "2026-01-01", "2026-01-01", "Switzerland", "fintech", "startup",
             "2026-01-01T00:00:00", "v1"))
        # Job 2: newer → "Germany / ai_ml" (should win)
        raw.execute(
            "INSERT INTO jobs (id, title, company, company_id, url, source, location, first_seen, last_seen, "
            "company_country, industry_sector, company_size, extracted_at, extracted_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("job2", "SPM", "MultiJobCo", cid, "http://j2", "test", "Remote",
             "2026-02-01", "2026-02-01", "Germany", "ai_ml", "scaleup",
             "2026-02-01T00:00:00", "v2"))
        # Job 3: newest but all NULLs → ignored
        raw.execute(
            "INSERT INTO jobs (id, title, company, company_id, url, source, location, first_seen, last_seen, "
            "company_country, industry_sector, company_size, extracted_at, extracted_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("job3", "Dir PM", "MultiJobCo", cid, "http://j3", "test", "Remote",
             "2026-03-01", "2026-03-01", None, None, None, "2026-03-01T00:00:00", "v3"))
        raw.commit()
        raw.close()

        # Now init via JobStorage — Phase 4 backfill should run
        db2 = JobStorage(tmp_path)

        with db2._conn() as conn:
            row = conn.execute(
                "SELECT company_country, industry_sector, company_size, "
                "enriched_at, enriched_by FROM companies WHERE id = ?",
                (cid,)
            ).fetchone()
        # Job 2 is most recent with non-null values → should win
        assert row["company_country"] == "Germany", \
            f"Expected Germany (newest non-null), got {row['company_country']!r}"
        assert row["industry_sector"] == "ai_ml", \
            f"Expected ai_ml (newest non-null), got {row['industry_sector']!r}"
        assert row["company_size"] == "scaleup", \
            f"Expected scaleup (newest non-null), got {row['company_size']!r}"
        assert row["enriched_at"] == "2026-02-01T00:00:00", \
            f"Expected job2's extracted_at, got {row['enriched_at']!r}"
        assert row["enriched_by"] == "v2", \
            f"Expected job2's extracted_by, got {row['enriched_by']!r}"

    finally:
        os.unlink(tmp_path)


def test_digest_joins_company_fields():
    """get_digest returns rows with company_country/sector/size from companies JOIN."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())

    cid = db.upsert_company("JoinTestCo")
    # Enrich the company directly
    db.update_company_enrichment(cid, {
        "company_country": "France",
        "industry_sector": "tech_saas",
        "company_size": "large",
    }, enriched_by="test")

    job = _job(company="JoinTestCo")
    db.save_scored(job, _score(7), PROFILE_ID, company_id=cid)

    rows = db.get_digest(PROFILE_ID, min_score=5)
    assert len(rows) == 1
    r = rows[0]
    assert r["company_country"] == "France", \
        f"Expected France from companies JOIN, got {r['company_country']!r}"
    assert r["industry_sector"] == "tech_saas", \
        f"Expected tech_saas from companies JOIN, got {r['industry_sector']!r}"
    assert r["company_size"] == "large", \
        f"Expected large from companies JOIN, got {r['company_size']!r}"


# ── Contact extraction regex tests ─────────────────────────────────────────────

def test_email_regex_extracts_basic():
    """Email regex catches name@domain.com in a paragraph."""
    from scrapers.contact_extract import extract_contact_references

    refs = extract_contact_references(
        "Contact us at john.doe@example.com for more info."
    )
    emails = [r["email"] for r in refs if r["email"]]
    assert "john.doe@example.com" in emails, f"Expected email not found in {refs}"


def test_email_regex_flags_role_accounts():
    """Role-account emails like careers@x.com are returned with role_account=True."""
    from scrapers.contact_extract import extract_contact_references

    refs = extract_contact_references(
        "Send your CV to careers@startup.io or contact hr@example.ch."
    )
    assert len(refs) == 2
    for ref in refs:
        if ref["email"] == "careers@startup.io":
            assert ref["role_account"] is True
        elif ref["email"] == "hr@example.ch":
            assert ref["role_account"] is True
        else:
            raise AssertionError(f"Unexpected email: {ref['email']}")


def test_linkedin_regex_normalizes_url():
    """LinkedIn regex extracts and normalizes various URL forms."""
    from scrapers.contact_extract import extract_contact_references

    refs = extract_contact_references(
        "Find us at https://www.linkedin.com/in/janedoe/ or "
        "http://linkedin.com/in/john-smith?utm=foo or "
        "LINKEDIN.COM/IN/ALICE-WANG"
    )
    linkedins = [r["linkedin_url"] for r in refs if r["linkedin_url"]]
    assert len(linkedins) == 3, f"Expected 3 LinkedIn URLs, got {linkedins}"
    assert "https://www.linkedin.com/in/janedoe" in linkedins
    assert "https://www.linkedin.com/in/john-smith" in linkedins
    assert "https://www.linkedin.com/in/ALICE-WANG" in linkedins


def test_extract_contact_references_dedupes():
    """Same email twice in description = one result."""
    from scrapers.contact_extract import extract_contact_references

    refs = extract_contact_references(
        "Email alice@test.com for questions. CC alice@test.com as well."
    )
    emails = [r["email"] for r in refs if r["email"]]
    assert emails == ["alice@test.com"], f"Expected 1 email, got {emails}"


def test_extract_contact_references_empty_input():
    """Empty or None input returns []."""
    from scrapers.contact_extract import extract_contact_references

    assert extract_contact_references("") == []
    assert extract_contact_references(None) == []


# ── Contact + interaction storage tests ────────────────────────────────────────

def test_upsert_contact_inserts():
    """Basic insert: a contact row is created."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    contact_id = db.upsert_contact(
        company_id=cid,
        email="alice@testco.com",
        first_name="Alice",
        last_name="Smith",
    )
    assert contact_id > 0
    row = db.get_contact(contact_id)
    assert row is not None
    assert row["email"] == "alice@testco.com"
    assert row["first_name"] == "Alice"
    assert row["is_unverified"] == 0  # default


def test_upsert_contact_dedupes_by_linkedin():
    """Same LinkedIn URL across calls = one row (LinkedIn is globally unique)."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    url = "https://www.linkedin.com/in/alice-test"

    id1 = db.upsert_contact(company_id=cid, linkedin_url=url, first_name="Alice")
    id2 = db.upsert_contact(company_id=cid, linkedin_url=url, first_name="Alice T.")

    assert id1 == id2
    contacts = db.get_company_contacts(cid)
    assert len(contacts) == 1


def test_upsert_contact_dedupes_by_email_within_company():
    """Same email at same company_id = one row."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    id1 = db.upsert_contact(company_id=cid, email="bob@testco.com")
    id2 = db.upsert_contact(company_id=cid, email="bob@testco.com", first_name="Bob")

    assert id1 == id2
    contacts = db.get_company_contacts(cid)
    assert len(contacts) == 1


def test_upsert_contact_separate_at_different_companies():
    """Same email at two different companies = two rows (people change jobs)."""
    db = JobStorage(":memory:")
    cid1 = db.upsert_company("Alpha")
    cid2 = db.upsert_company("Beta")

    id1 = db.upsert_contact(company_id=cid1, email="same@person.com")
    id2 = db.upsert_contact(company_id=cid2, email="same@person.com")

    assert id1 != id2
    assert len(db.get_company_contacts(cid1)) == 1
    assert len(db.get_company_contacts(cid2)) == 1


def test_upsert_contact_never_overwrites_non_null():
    """Pre-existing non-NULL fields are preserved on re-upsert."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    db.upsert_contact(company_id=cid, email="jane@testco.com",
                       role_title="CTO", first_name="Jane")
    # Re-upsert with different role_title
    db.upsert_contact(company_id=cid, email="jane@testco.com",
                       role_title="VP Engineering")

    contacts = db.get_company_contacts(cid)
    assert len(contacts) == 1
    # Original "CTO" should NOT be overwritten
    assert contacts[0]["role_title"] == "CTO", \
        f"Expected CTO to be preserved, got {contacts[0]['role_title']!r}"


def test_upsert_contact_is_unverified_flag():
    """is_unverified=True is persisted."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    contact_id = db.upsert_contact(
        company_id=cid, email="new@testco.com", is_unverified=True,
    )
    row = db.get_contact(contact_id)
    assert row["is_unverified"] == 1


def test_upsert_contact_validates_role_family():
    """Invalid role_family raises ValueError."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    try:
        db.upsert_contact(company_id=cid, role_family="invalid_role")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "role_family" in str(e)


def test_upsert_contact_validates_seniority():
    """Invalid seniority raises ValueError."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    try:
        db.upsert_contact(company_id=cid, seniority="grand_poobah")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "seniority" in str(e)


def test_upsert_contact_dedupes_by_name_within_company():
    """Same normalized name at same company = one row."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    id1 = db.upsert_contact(company_id=cid, full_name="Sarah Müller")
    id2 = db.upsert_contact(company_id=cid, first_name="Sarah", last_name="Müller")

    assert id1 == id2


def test_log_interaction_basic():
    """log_interaction creates a row and returns its id."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    iid = db.log_interaction(
        company_id=cid, type="note",
        direction="none", subject="Internal note",
    )
    assert iid > 0


def test_log_interaction_validates_type():
    """Invalid interaction type raises ValueError."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    try:
        db.log_interaction(company_id=cid, type="invalid_type")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "type" in str(e)


def test_log_interaction_validates_outcome():
    """Invalid outcome raises ValueError; NULL outcome is fine."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    # NULL outcome should work
    iid = db.log_interaction(company_id=cid, type="note", outcome=None)
    assert iid > 0

    # Invalid outcome
    try:
        db.log_interaction(company_id=cid, type="note", outcome="maybe")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "outcome" in str(e)


def test_log_interaction_validates_direction():
    """Invalid direction raises ValueError."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    try:
        db.log_interaction(company_id=cid, type="note", direction="sideways")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "direction" in str(e)


def test_get_company_contacts_respects_include_unverified():
    """include_unverified=False hides unverified contacts."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    db.upsert_contact(company_id=cid, email="a@test.com", is_unverified=True)
    db.upsert_contact(company_id=cid, email="b@test.com", is_unverified=False)

    assert len(db.get_company_contacts(cid, include_unverified=True)) == 2
    assert len(db.get_company_contacts(cid, include_unverified=False)) == 1


def test_get_company_interactions_limit():
    """get_company_interactions respects the limit parameter."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")

    for _ in range(5):
        db.log_interaction(company_id=cid, type="note")

    assert len(db.get_company_interactions(cid, limit=3)) == 3
    assert len(db.get_company_interactions(cid, limit=50)) == 5


# ── Derived relationship status tests ──────────────────────────────────────────

def test_derived_relationship_status_none():
    """Fresh contact with only discovered_on_posting → 'none'."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    contact_id = db.upsert_contact(company_id=cid, email="x@test.com")

    db.log_interaction(company_id=cid, contact_id=contact_id,
                        type="discovered_on_posting")
    status = db.get_contact_relationship_status(contact_id)
    assert status == "none", f"Expected 'none', got {status!r}"


def test_derived_relationship_status_cold_contacted():
    """Contact with outreach_sent → 'cold_contacted'."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    ct = db.upsert_contact(company_id=cid, email="y@test.com")

    db.log_interaction(company_id=cid, contact_id=ct, type="outreach_sent",
                        direction="outbound")
    status = db.get_contact_relationship_status(ct)
    assert status == "cold_contacted", f"Expected 'cold_contacted', got {status!r}"


def test_derived_relationship_status_replied():
    """Contact with outreach_sent then reply_received → 'replied'."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    ct = db.upsert_contact(company_id=cid, email="z@test.com")

    db.log_interaction(company_id=cid, contact_id=ct, type="outreach_sent",
                        direction="outbound")
    db.log_interaction(company_id=cid, contact_id=ct, type="reply_received",
                        direction="inbound")
    status = db.get_contact_relationship_status(ct)
    assert status == "replied", f"Expected 'replied', got {status!r}"


def test_derived_relationship_status_applied():
    """Contact with application_submitted → 'applied'."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    ct = db.upsert_contact(company_id=cid, email="w@test.com")

    db.log_interaction(company_id=cid, contact_id=ct,
                        type="application_submitted", direction="outbound")
    status = db.get_contact_relationship_status(ct)
    assert status == "applied", f"Expected 'applied', got {status!r}"


def test_derived_relationship_status_interviewing():
    """Contact with interview → 'interviewing'."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    ct = db.upsert_contact(company_id=cid, email="v@test.com")

    db.log_interaction(company_id=cid, contact_id=ct, type="interview")
    status = db.get_contact_relationship_status(ct)
    assert status == "interviewing", f"Expected 'interviewing', got {status!r}"


def test_derived_relationship_status_declined():
    """Contact with decision_received outcome=negative → 'declined'."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    ct = db.upsert_contact(company_id=cid, email="u@test.com")

    db.log_interaction(company_id=cid, contact_id=ct,
                        type="decision_received", outcome="negative",
                        direction="inbound")
    status = db.get_contact_relationship_status(ct)
    assert status == "declined", f"Expected 'declined', got {status!r}"


def test_derived_relationship_status_offer():
    """Contact with decision_received outcome=positive → 'offer'."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    ct = db.upsert_contact(company_id=cid, email="t@test.com")

    db.log_interaction(company_id=cid, contact_id=ct,
                        type="decision_received", outcome="positive",
                        direction="inbound")
    status = db.get_contact_relationship_status(ct)
    assert status == "offer", f"Expected 'offer', got {status!r}"


def test_derived_relationship_status_no_contact():
    """Non-existent contact returns 'none'."""
    db = JobStorage(":memory:")
    status = db.get_contact_relationship_status(99999)
    assert status == "none"


def test_company_relationship_summary():
    """get_company_relationship_summary aggregates correctly."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("TestCo")
    ct1 = db.upsert_contact(company_id=cid, email="a@test.com")
    ct2 = db.upsert_contact(company_id=cid, email="b@test.com")

    db.log_interaction(company_id=cid, contact_id=ct1, type="outreach_sent",
                        direction="outbound")
    db.log_interaction(company_id=cid, contact_id=ct2,
                        type="application_submitted", direction="outbound")

    summary = db.get_company_relationship_summary(cid)
    assert summary["total_contacts"] == 2
    assert summary["by_status"]["cold_contacted"] == 1
    assert summary["by_status"]["applied"] == 1
    assert summary["last_interaction_at"] is not None


# ── Auto-interaction tests ─────────────────────────────────────────────────────

def test_job_tracking_applied_logs_interaction():
    """Transitioning a job to 'applied' creates an application_submitted interaction."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    cid = db.upsert_company("TestCo")
    job = _job(company="TestCo")
    db.save_scored(job, _score(7), PROFILE_ID, company_id=cid)

    db.set_status(job.id, "applied")

    interactions = db.get_company_interactions(cid)
    assert len(interactions) == 1
    assert interactions[0]["type"] == "application_submitted"
    assert interactions[0]["direction"] == "outbound"
    assert interactions[0]["job_id"] == job.id


def test_job_tracking_rejected_logs_interaction():
    """Transitioning a job to 'rejected' creates a decision_received interaction."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    cid = db.upsert_company("TestCo")
    job = _job(company="TestCo")
    db.save_scored(job, _score(7), PROFILE_ID, company_id=cid)

    db.set_status(job.id, "rejected")

    interactions = db.get_company_interactions(cid)
    assert len(interactions) == 1
    assert interactions[0]["type"] == "decision_received"
    assert interactions[0]["outcome"] == "negative"
    assert interactions[0]["direction"] == "inbound"


def test_job_tracking_no_company_id_no_interaction():
    """Status transition on a job without company_id does not crash."""
    db = JobStorage(":memory:")
    db.upsert_profile(_FakeProfile())
    job = _job(company="NoCo")
    # Save directly without company_id
    db.save_scored(job, _score(7), PROFILE_ID, company_id=None)

    # Should not raise
    db.set_status(job.id, "applied")

    # No interaction logged (no company_id to anchor it)
    rows = db.get_company_interactions(99999)
    assert len(rows) == 0


# ── End-to-end contact discovery test ──────────────────────────────────────────

def test_extract_pass_discovers_contacts_via_regex():
    """End-to-end: a job with email + LinkedIn in description gets contacts."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("Acme")
    # Simulate a job that is already in the DB with a company_id and needs extraction
    job = JobPosting(
        source="test",
        title="PM Lead",
        company="Acme",
        location="Remote",
        url="https://example.com/jobs/99",
        posted_date=None,
        description=(
            "We are hiring a Product Lead! "
            "Contact sarah@acme.io for details. "
            "Our hiring manager: https://www.linkedin.com/in/sarah-acme"
        ),
        base_location="United States",
    )
    db.save_unscored(job, company_id=cid)

    # Simulate what _discover_contacts does
    from scrapers.contact_extract import extract_contact_references

    refs = extract_contact_references(job.description or "")
    for ref in refs:
        contact_id = db.upsert_contact(
            company_id=cid,
            email=ref.get("email"),
            linkedin_url=ref.get("linkedin_url"),
            email_status="role_account" if ref.get("role_account") else "unknown",
            is_unverified=True,
        )
        db.log_interaction(
            company_id=cid,
            contact_id=contact_id,
            job_id=job.id,
            type="discovered_on_posting",
            direction="none",
            body_excerpt=(job.description or "")[:200],
        )

    contacts = db.get_company_contacts(cid)
    assert len(contacts) == 2, f"Expected 2 contacts, got {len(contacts)}"
    for c in contacts:
        assert c["is_unverified"] == 1

    interactions = db.get_company_interactions(cid)
    assert len(interactions) == 2
    for ix in interactions:
        assert ix["type"] == "discovered_on_posting"


# ── Badge tests ──────────────────────────────────────────────────────────────

def test_score_badge_hot():
    assert "🔥" in score_badge(9)
    assert "🔥" in score_badge(10)
    assert "9/10" in score_badge(9)
    assert "10/10" in score_badge(10)


def test_score_badge_solid():
    assert "⭐" in score_badge(7)
    assert "⭐" in score_badge(8)
    assert "7/10" in score_badge(7)
    assert "8/10" in score_badge(8)


def test_score_badge_maybe():
    assert "👀" in score_badge(6)
    assert "👀" in score_badge(1)
    assert "👀" in score_badge(0)
    assert "6/10" in score_badge(6)


def test_score_badge_none():
    assert "❓" in score_badge(None)
    assert "—/10" in score_badge(None)


def test_company_status_badge_valid():
    assert "👀" in company_status_badge("watching")
    assert "watching" in company_status_badge("watching")
    assert "📤" in company_status_badge("active_outreach")
    assert "active outreach" in company_status_badge("active_outreach")
    assert "⛔" in company_status_badge("blacklisted")


def test_company_status_badge_unknown():
    assert "❓" in company_status_badge("made_up_status")


def test_relationship_badge_all():
    assert "🏆 Offer" == relationship_badge("offer")
    assert "🎯 Interviewing" == relationship_badge("interviewing")
    assert "📝 Applied" == relationship_badge("applied")
    assert "💬 Replied" == relationship_badge("replied")
    assert "📤 Contacted" == relationship_badge("cold_contacted")
    assert "—" == relationship_badge("none")


def test_relationship_badge_unknown():
    assert relationship_badge("made_up") == "made_up"


def test_sector_label_known():
    from tracker_views.shared import SECTOR_LABELS
    for label, code in SECTOR_LABELS.items():
        assert sector_label(code) == label


def test_sector_label_unknown():
    assert sector_label("nonexistent_code") == "nonexistent_code"


def test_unverified_badge():
    assert "⚠️" in unverified_badge()
    assert "Unverified" in unverified_badge()


# ── Storage query tests (PR4) ────────────────────────────────────────────────

def test_get_companies_all():
    """get_companies returns companies with counts and last interaction."""
    db = JobStorage(":memory:")
    cid1 = db.upsert_company("Alpha Inc")
    cid2 = db.upsert_company("Beta LLC")
    db.upsert_company("Gamma Ltd")

    # Populate Alpha: 1 job, 1 contact, 1 interaction
    db.upsert_profile(_FakeProfile())
    job = _job(company="Alpha Inc")
    db.save_scored(job, _score(7), PROFILE_ID, company_id=cid1)
    db.upsert_contact(company_id=cid1, first_name="Alice", email="alice@alpha.com",
                      is_unverified=False)
    db.log_interaction(company_id=cid1, type="outreach_sent", direction="outbound",
                       subject="Hello")

    companies = db.get_companies(exclude_blacklisted=False)
    assert len(companies) >= 2

    alpha = next(c for c in companies if c["name"] == "Alpha Inc")
    assert alpha["job_count"] == 1
    assert alpha["contact_count"] == 1
    assert alpha["interaction_count"] == 1
    assert alpha["last_interaction_at"] is not None

    gamma = next(c for c in companies if c["name"] == "Gamma Ltd")
    assert gamma["job_count"] == 0
    assert gamma["contact_count"] == 0
    assert gamma["interaction_count"] == 0
    assert gamma["last_interaction_at"] is None


def test_get_companies_blacklist_filter():
    """get_companies excludes blacklisted companies by default."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("BlockedCo")
    db.set_company_status(cid, "blacklisted")
    db.upsert_company("GoodCo")

    companies = db.get_companies()
    names = {c["name"] for c in companies}
    assert "BlockedCo" not in names
    assert "GoodCo" in names

    # With exclude_blacklisted=False, should include it
    all_companies = db.get_companies(exclude_blacklisted=False)
    all_names = {c["name"] for c in all_companies}
    assert "BlockedCo" in all_names


def test_get_companies_search():
    """get_companies search filters by name."""
    db = JobStorage(":memory:")
    db.upsert_company("Alphabet Inc")
    db.upsert_company("Beta Corp")
    db.upsert_company("Alpine SA")

    results = db.get_companies(search="Alph")
    names = {c["name"] for c in results}
    assert "Alphabet Inc" in names
    assert "Beta Corp" not in names
    assert "Alpine SA" not in names


def test_get_all_contacts_basic():
    """get_all_contacts returns contacts with company info."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("Acme Corp")
    ct_id = db.upsert_contact(company_id=cid, first_name="Bob", last_name="Smith",
                              email="bob@acme.com", role_title="CTO",
                              is_unverified=False)

    contacts = db.get_all_contacts()
    assert len(contacts) == 1
    ct = contacts[0]
    assert ct["first_name"] == "Bob"
    assert ct["last_name"] == "Smith"
    assert ct["email"] == "bob@acme.com"
    assert ct["company_name"] == "Acme Corp"
    assert ct["role_title"] == "CTO"


def test_get_all_contacts_filters():
    """get_all_contacts respects company_id filter."""
    db = JobStorage(":memory:")
    cid1 = db.upsert_company("Acme Corp")
    cid2 = db.upsert_company("Beta LLC")

    db.upsert_contact(company_id=cid1, first_name="Alice", is_unverified=False)
    db.upsert_contact(company_id=cid2, first_name="Bob", is_unverified=True)

    by_company = db.get_all_contacts(company_id=cid1)
    assert len(by_company) == 1
    assert by_company[0]["first_name"] == "Alice"

    unverified = db.get_all_contacts(is_unverified=True)
    assert len(unverified) == 1
    assert unverified[0]["first_name"] == "Bob"


def test_get_all_contacts_search():
    """get_all_contacts search filters by name/email/role."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("Acme Corp")
    db.upsert_contact(company_id=cid, first_name="Charlie", last_name="Brown",
                      email="charlie@acme.com", is_unverified=False)
    db.upsert_contact(company_id=cid, first_name="Dana", last_name="White",
                      email="dana@other.com", is_unverified=False)

    results = db.get_all_contacts(search="charlie")
    assert len(results) == 1
    assert results[0]["first_name"] == "Charlie"

    results2 = db.get_all_contacts(search="dana@other")
    assert len(results2) == 1
    assert results2[0]["first_name"] == "Dana"


def test_get_contact_interactions():
    """get_contact_interactions returns interactions for a specific contact."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("Acme Corp")
    ct1 = db.upsert_contact(company_id=cid, first_name="Alice", is_unverified=False)
    ct2 = db.upsert_contact(company_id=cid, first_name="Bob", is_unverified=False)

    db.log_interaction(company_id=cid, contact_id=ct1, type="outreach_sent",
                       direction="outbound", subject="Hello Alice")
    db.log_interaction(company_id=cid, contact_id=ct2, type="call",
                       direction="outbound", subject="Call with Bob")

    alice_ix = db.get_contact_interactions(ct1)
    assert len(alice_ix) == 1
    assert alice_ix[0]["subject"] == "Hello Alice"

    bob_ix = db.get_contact_interactions(ct2)
    assert len(bob_ix) == 1
    assert bob_ix[0]["subject"] == "Call with Bob"


def test_get_dashboard_data_empty():
    """get_dashboard_data returns empty lists/zeros with fresh DB."""
    db = JobStorage(":memory:")
    data = db.get_dashboard_data()
    assert data["follow_ups_due_today"] == []
    assert data["recent_inbound"] == []
    assert data["unverified_contacts_count"] == 0
    assert data["stale_active_outreach"] == []


def test_get_dashboard_data_follow_ups():
    """get_dashboard_data returns follow-ups due today."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("Acme Corp")
    from datetime import date as dt_date
    today = dt_date.today().isoformat()
    db.log_interaction(company_id=cid, type="outreach_sent", direction="outbound",
                       subject="Follow up", follow_up_due_at=today)

    data = db.get_dashboard_data()
    assert len(data["follow_ups_due_today"]) >= 1
    assert data["follow_ups_due_today"][0]["subject"] == "Follow up"


def test_get_dashboard_data_unverified_count():
    """get_dashboard_data counts unverified contacts."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("Acme Corp")
    db.upsert_contact(company_id=cid, first_name="Alice", is_unverified=True)
    db.upsert_contact(company_id=cid, first_name="Bob", is_unverified=False)
    db.upsert_contact(company_id=cid, first_name="Eve", is_unverified=True)

    data = db.get_dashboard_data()
    assert data["unverified_contacts_count"] == 2


def test_get_dashboard_data_stale_outreach():
    """get_dashboard_data finds companies with stale active_outreach status."""
    db = JobStorage(":memory:")
    cid = db.upsert_company("StaleCo")
    db.set_company_status(cid, "active_outreach")
    # No interactions at all → stale

    data = db.get_dashboard_data()
    stale_names = {c["name"] for c in data["stale_active_outreach"]}
    assert "StaleCo" in stale_names


# ── Filter tests ─────────────────────────────────────────────────────────────

def _job_dict(**overrides) -> dict:
    """Minimal job dict fixture for filter tests."""
    d = {
        "id": "test-1",
        "title": "Test Job",
        "company": "TestCo",
        "status": "new",
        "score": 7,
        "posted_date": date.today().isoformat(),
        "last_seen": date.today().isoformat(),
        "location": "Remote",
        "work_mode": "remote",
        "geo_zone": "europe",
        "company_size": "startup",
        "industry_sector": "web3_crypto",
        "language_required": "english",
        "source": "LinkedIn",
    }
    d.update(overrides)
    return d


def test_apply_filters_min_score():
    jobs = [_job_dict(score=3), _job_dict(score=7)]
    result = apply_filters(jobs, min_score=5)
    assert len(result) == 1
    assert result[0]["score"] == 7


def test_apply_filters_show_stale():
    """When show_stale=False, stale unengaged jobs are excluded."""
    from datetime import date as dt_date, timedelta
    old = dt_date.today() - timedelta(days=60)
    jobs = [
        _job_dict(id="fresh", posted_date=dt_date.today().isoformat()),
        _job_dict(id="stale", posted_date=old.isoformat()),
    ]
    result = apply_filters(jobs, show_stale=False)
    ids = {j["id"] for j in result}
    assert "fresh" in ids
    assert "stale" not in ids


def test_apply_filters_show_stale_true():
    """When show_stale=True, stale jobs are included."""
    from datetime import date as dt_date, timedelta
    old = dt_date.today() - timedelta(days=60)
    jobs = [
        _job_dict(id="fresh", posted_date=dt_date.today().isoformat()),
        _job_dict(id="stale", posted_date=old.isoformat()),
    ]
    result = apply_filters(jobs, show_stale=True)
    ids = {j["id"] for j in result}
    assert "stale" in ids


def test_apply_filters_archived_view():
    """show_archived_view=True returns only archived jobs."""
    jobs = [
        _job_dict(id="a", status="archived"),
        _job_dict(id="b", status="new"),
        _job_dict(id="c", status="applied"),
    ]
    result = apply_filters(jobs, show_archived_view=True)
    assert len(result) == 1
    assert result[0]["id"] == "a"


def test_apply_filters_status_filter():
    """status_filter returns only jobs with matching status."""
    jobs = [
        _job_dict(id="a", status="new"),
        _job_dict(id="b", status="queued"),
        _job_dict(id="c", status="ready"),
    ]
    result = apply_filters(jobs, status_filter=["new", "queued"], show_stale=True)
    ids = {j["id"] for j in result}
    assert ids == {"a", "b"}


def test_apply_filters_status_excludes_archived_by_default():
    """When status_filter is set without 'archived', archived jobs are excluded."""
    jobs = [
        _job_dict(id="a", status="new"),
        _job_dict(id="b", status="archived"),
    ]
    result = apply_filters(jobs, status_filter=["new"], show_stale=True)
    ids = {j["id"] for j in result}
    assert ids == {"a"}


def test_apply_filters_combined():
    """Multiple filters work together."""
    from datetime import date as dt_date, timedelta
    old = dt_date.today() - timedelta(days=60)
    jobs = [
        _job_dict(id="a", score=8, status="new",
                  posted_date=dt_date.today().isoformat()),
        _job_dict(id="b", score=3, status="new",
                  posted_date=dt_date.today().isoformat()),
        _job_dict(id="c", score=9, status="archived",
                  posted_date=dt_date.today().isoformat()),
        _job_dict(id="d", score=8, status="new",
                  posted_date=old.isoformat()),
    ]
    result = apply_filters(jobs, min_score=5, status_filter=["new"], show_stale=False)
    ids = {j["id"] for j in result}
    assert ids == {"a"}


# ── Main ──────────────────────────────────────────────────────────────────────

TESTS = [
    test_db_initialises,
    test_schema_matches_model,
    test_job_scores_has_no_status_column,
    test_job_applications_has_required_columns,
    test_upsert_and_retrieve,
    test_cache_split,
    test_no_rescore_on_second_run,
    test_id_stability,
    test_status_update,
    test_status_is_profile_independent,
    test_invalid_status_rejected,
    test_status_history_append_only,
    test_status_history_present_in_tracker,
    test_application_persistence,
    test_get_digest,
    test_rejected_excluded_from_scoring,
    test_best_score_view_status,
    test_score_new_fields_round_trip,
    test_pre_migration_null_defaults,
    test_filter_country_known_not_in_allowlist_excluded,
    test_filter_country_known_in_allowlist_kept,
    test_filter_country_unknown_always_passes,
    test_filter_country_none_means_no_restriction,
    test_filter_sector_excluded,
    test_filter_sector_not_excluded_kept,
    test_filter_sector_empty_list_keeps_all,
    test_filter_language_excluded,
    test_filter_language_not_excluded_kept,
    test_filter_language_empty_list_keeps_all,
    test_filter_all_inactive_keeps_all,
    test_migration_idempotent,
    test_backfill_correctness,
    test_dedupe_against_db,
    test_dedupe_against_db_normalization,
    test_normalize_company_name,
    test_upsert_company_dedupes,
    test_blacklist_filter_excludes_jobs,
    test_company_status_history_logs_transitions,
    test_company_enrichment_write_once,
    test_backfill_pulls_latest_extracted,
    test_digest_joins_company_fields,
    # Contact extraction regex tests
    test_email_regex_extracts_basic,
    test_email_regex_flags_role_accounts,
    test_linkedin_regex_normalizes_url,
    test_extract_contact_references_dedupes,
    test_extract_contact_references_empty_input,
    # Contact + interaction storage tests
    test_upsert_contact_inserts,
    test_upsert_contact_dedupes_by_linkedin,
    test_upsert_contact_dedupes_by_email_within_company,
    test_upsert_contact_separate_at_different_companies,
    test_upsert_contact_never_overwrites_non_null,
    test_upsert_contact_is_unverified_flag,
    test_upsert_contact_validates_role_family,
    test_upsert_contact_validates_seniority,
    test_upsert_contact_dedupes_by_name_within_company,
    test_log_interaction_basic,
    test_log_interaction_validates_type,
    test_log_interaction_validates_outcome,
    test_log_interaction_validates_direction,
    test_get_company_contacts_respects_include_unverified,
    test_get_company_interactions_limit,
    # Derived relationship status tests
    test_derived_relationship_status_none,
    test_derived_relationship_status_cold_contacted,
    test_derived_relationship_status_replied,
    test_derived_relationship_status_applied,
    test_derived_relationship_status_interviewing,
    test_derived_relationship_status_declined,
    test_derived_relationship_status_offer,
    test_derived_relationship_status_no_contact,
    test_company_relationship_summary,
    # Auto-interaction tests
    test_job_tracking_applied_logs_interaction,
    test_job_tracking_rejected_logs_interaction,
    test_job_tracking_no_company_id_no_interaction,
    # End-to-end
    test_extract_pass_discovers_contacts_via_regex,
    # Badge tests
    test_score_badge_hot,
    test_score_badge_solid,
    test_score_badge_maybe,
    test_score_badge_none,
    test_company_status_badge_valid,
    test_company_status_badge_unknown,
    test_relationship_badge_all,
    test_relationship_badge_unknown,
    test_sector_label_known,
    test_sector_label_unknown,
    test_unverified_badge,
    # Storage query tests (PR4)
    test_get_companies_all,
    test_get_companies_blacklist_filter,
    test_get_companies_search,
    test_get_all_contacts_basic,
    test_get_all_contacts_filters,
    test_get_all_contacts_search,
    test_get_contact_interactions,
    test_get_dashboard_data_empty,
    test_get_dashboard_data_follow_ups,
    test_get_dashboard_data_unverified_count,
    test_get_dashboard_data_stale_outreach,
    # Filter tests
    test_apply_filters_min_score,
    test_apply_filters_show_stale,
    test_apply_filters_show_stale_true,
    test_apply_filters_archived_view,
    test_apply_filters_status_filter,
    test_apply_filters_status_excludes_archived_by_default,
    test_apply_filters_combined,
]


def run_storage_tests() -> list[tuple[str, bool, str]]:
    global _results
    _results = []
    for fn in TESTS:
        _run(fn)
    return _results


if __name__ == "__main__":
    print("Storage tests (in-memory DB)\n")
    results = run_storage_tests()

    passed = sum(1 for _, ok, _ in results if ok)
    total  = len(results)

    print(f"\n{'─'*50}")
    print(f"  {passed}/{total} passed")
    if passed < total:
        for name, ok, err in results:
            if not ok:
                print(f"  ❌ {name}: {err}")

    sys.exit(0 if passed == total else 1)
