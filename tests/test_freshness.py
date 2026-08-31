"""Offline tests for the configurable freshness window (Spec 028).

Locks Constitution IX (30-day fallback everywhere) and the FR-004 `date_from`
honoring in `JobFilterEngine`. In-memory DB only; no network I/O.
"""

from datetime import date, timedelta

from filters import JobFilterEngine
from models import JobFilter, JobPosting
from storage import JobStorage


def _job(title: str = "PM", posted_days_ago: int = 5) -> JobPosting:
    return JobPosting(
        source="test",
        title=title,
        company="Acme",
        location="Remote",
        url=f"https://example.com/{title}",
        posted_date=date.today() - timedelta(days=posted_days_ago),
    )


# ── get_freshness_days fallback/coercion (FR-002, §IX) ───────────────────────

def test_freshness_default_when_absent():
    assert JobStorage(":memory:").get_freshness_days() == 30


def test_freshness_coerces_valid_int():
    db = JobStorage(":memory:")
    db.set_config("freshness_days", "10")
    assert db.get_freshness_days() == 10


def test_freshness_fallback_on_malformed():
    db = JobStorage(":memory:")
    for bad in ("abc", "", "-5", "0"):
        db.set_config("freshness_days", bad)
        assert db.get_freshness_days() == 30, f"{bad!r} should fall back to 30"


# ── JobFilterEngine honors date_from (FR-004) ────────────────────────────────

def test_filter_honors_date_from_cutoff():
    jobs = [
        _job("Fresh PM", posted_days_ago=5),
        _job("Stale PM", posted_days_ago=20),
    ]
    job_filter = JobFilter(date_from=date.today() - timedelta(days=10))
    results, excluded_date, _ = JobFilterEngine.apply(jobs, job_filter)

    titles = [j.title for j in results]
    assert "Fresh PM" in titles
    assert "Stale PM" not in titles
    assert excluded_date == 1


def test_filter_falls_back_to_30_days_when_date_from_none():
    jobs = [
        _job("Fresh PM", posted_days_ago=5),
        _job("Old PM", posted_days_ago=45),
    ]
    results, excluded_date, _ = JobFilterEngine.apply(jobs, JobFilter())

    titles = [j.title for j in results]
    assert "Fresh PM" in titles
    assert "Old PM" not in titles
    assert excluded_date == 1
