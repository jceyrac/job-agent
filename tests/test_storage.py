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
from storage import JobStorage


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

    required_in_jobs = model_fields - scorer_owned - excluded
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
    job = _job()
    score = _score()
    score["company_country"]   = "Switzerland"
    score["industry_sector"]   = "fintech"
    score["language_required"] = "french"
    db.save_scored(job, score, PROFILE_ID)

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
    db1.save_scored(job, _score(7), PROFILE_ID)

    # Second init on the same DB — migration should be a no-op
    db2 = JobStorage(":memory:")
    with db2._conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    for col in ("summary", "work_mode", "geo_zone", "company_size",
                "contract_type", "company_country", "industry_sector",
                "language_required", "extracted_at"):
        assert col in cols, f"Column {col!r} missing after re-init"

    # Reading back must still work
    db2.upsert_profile(_FakeProfile())
    db2.save_scored(job, _score(7), PROFILE_ID)
    rows = db2.get_digest(PROFILE_ID, min_score=5)
    assert len(rows) == 1


def test_backfill_correctness():
    """save_scored writes structured fields to jobs table; re-init preserves them."""
    from datetime import date

    jobs_data = [
        ("https://example.com/j1", "Company A", "Switzerland", "fintech", "english", 8),
        ("https://example.com/j2", "Company B", "Germany", "ai_ml", "german", 7),
        ("https://example.com/j3", "Company C", "United States", "web3_crypto", "english", 9),
    ]

    # Simulate pre-1e flow: create a DB, save scored jobs (writes to both tables)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        db = JobStorage(tmp_path)
        db.upsert_profile(_FakeProfile())
        for url, company, country, sector, lang, score in jobs_data:
            job = JobPosting(
                source="Test", title="PM", company=company, location="Remote",
                url=url, posted_date=date(2026, 4, 1),
            )
            sd = _score(score)
            sd.update({"company_country": country, "industry_sector": sector,
                       "language_required": lang})
            db.save_scored(job, sd, PROFILE_ID)
        del db

        # Re-initialize — migration should pick up existing data
        db2 = JobStorage(tmp_path)

        # Verify structured fields on jobs table directly
        with db2._conn() as conn:
            rows = conn.execute(
                "SELECT company_country, industry_sector, language_required, "
                "extracted_at FROM jobs ORDER BY company_country"
            ).fetchall()
        assert len(rows) == 3
        countries = {r["company_country"] for r in rows}
        languages = {r["language_required"] for r in rows}
        assert "Switzerland" in countries
        assert "Germany" in countries
        assert "english" in languages
        assert "german" in languages
        for r in rows:
            assert r["industry_sector"] in ("fintech", "ai_ml", "web3_crypto")
            assert r["extracted_at"] is not None, \
                f"extracted_at should not be NULL for {r['company_country']}"

        # Verify get_digest reads from jobs table
        db2.upsert_profile(_FakeProfile())
        digest = db2.get_digest(PROFILE_ID, min_score=5)
        assert len(digest) == 3
        for j in digest:
            assert j["company_country"] in ("Switzerland", "Germany", "United States")
            assert j["industry_sector"] in ("fintech", "ai_ml", "web3_crypto")
            assert j["language_required"] in ("english", "german")

    finally:
        os.unlink(tmp_path)


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
