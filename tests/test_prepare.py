"""
tests/test_prepare.py — unit tests for prepare.py

All tests use an in-memory SQLite DB and mock LLM calls so they
never touch data/jobs.db or consume API quota.

Usage:
    python tests/test_prepare.py
"""

import json
import sys
import os
import traceback
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import JobPosting
from storage import JobStorage
from profiles import ALL_PROFILES

PROFILE_ID = "web3_remote"

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


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _job(url="https://example.com/prepare-test", **kwargs) -> JobPosting:
    defaults = dict(
        source="TestSource",
        title="Senior Product Manager",
        company="TestCo",
        location="Zürich, Switzerland",
        url=url,
        posted_date=date.today(),
        description="We are looking for a Senior PM to lead our fintech API platform. "
                    "Experience with PSD2, Open Banking, and B2B SaaS required. "
                    "Based in Switzerland, hybrid role with 2-3 office days in Zürich.",
        work_mode="hybrid",
        base_location="Zürich, Switzerland",
        company_size="scaleup",
        contract_type="permanent",
        geo_zone="europe",
        company_country="Switzerland",
        industry_sector="fintech",
        language_required="en",
    )
    defaults.update(kwargs)
    return JobPosting(**defaults)


def _make_mock_llm(response_text: str, model_name: str = "llama-3.3-70b-versatile"):
    """Return a MagicMock that mimics the Groq chat completion response."""
    mock = MagicMock()
    mock.choices = [MagicMock()]
    mock.choices[0].message.content = response_text
    return mock


def _setup_db_with_job(**job_overrides) -> tuple[JobStorage, JobPosting]:
    """Create an in-memory DB with one scored, extracted job."""
    db = JobStorage(":memory:")
    profile = ALL_PROFILES[PROFILE_ID]
    db.upsert_profile(profile)

    job = _job(**job_overrides)
    score = {
        "score": 8,
        "reason": "Strong fintech PM fit",
        "summary": "Senior PM role at a Swiss fintech scaleup.",
        "work_mode": job.work_mode,
        "geo_zone": job.geo_zone,
        "company_size": job.company_size,
        "contract_type": job.contract_type,
        "scored_by": "llama-3.1-8b-instant",
        "company_country": job.company_country or "Switzerland",
        "industry_sector": job.industry_sector or "fintech",
        "language_required": job.language_required or "en",
    }
    db.save_scored(job, score, PROFILE_ID)
    db.set_status(job.id, "ready")
    return db, job


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_upsert_idempotency():
    """save_prepared_application called twice must not create duplicate rows."""
    db, job = _setup_db_with_job()

    cv_bullets = {"bullets": [{"text": "Led API product at Powens", "rationale": "Matches JD"}]}
    research = {"what_company_does": "A fintech scaleup"}
    screening = {"why_company": "I admire your work in fintech"}

    # First save
    db.save_prepared_application(
        job_id=job.id, profile_id=PROFILE_ID,
        cover_letter="Dear Hiring Manager...",
        cv_bullets_selected=cv_bullets,
        company_research=research,
        screening_answers=screening,
        language="en",
        prepared_by="llama-3.3-70b-versatile",
    )

    # Second save with different content
    db.save_prepared_application(
        job_id=job.id, profile_id=PROFILE_ID,
        cover_letter="Updated cover letter...",
        cv_bullets_selected=cv_bullets,
        company_research=research,
        screening_answers=screening,
        language="en",
        prepared_by="llama-3.3-70b-versatile",
    )

    app = db.get_application(job.id)
    assert app is not None, "get_application returned None"
    assert app["cover_letter"] == "Updated cover letter...", \
        f"Second save should overwrite: got {app['cover_letter']!r}"

    # Verify only one row exists
    with db._conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM job_applications WHERE job_id = ?", (job.id,)
        ).fetchone()[0]
    assert count == 1, f"Expected 1 row, got {count}"


def test_language_fallback():
    """When language_required is NULL, prepare should default to 'en'."""
    from prepare import _language_name

    # Test the language helper used by prepare.py
    assert _language_name("en") == "English"
    assert _language_name("fr") == "French"
    assert _language_name("de") == "German"
    assert _language_name("unknown") == "English"  # unknown maps to English


def test_bullet_json_parse_retry():
    """_safe_json_parse handles malformed JSON and recovers from code blocks."""
    from prepare import _safe_json_parse

    # Valid JSON
    result = _safe_json_parse('{"bullets": [], "omit": []}', "test")
    assert result == {"bullets": [], "omit": []}

    # JSON in markdown code block
    result = _safe_json_parse('```json\n{"bullets": [{"text": "x"}], "omit": []}\n```', "test")
    assert result is not None
    assert result["bullets"][0]["text"] == "x"

    # JSON embedded in noisy text
    result = _safe_json_parse('Here is your response:\n\n{"bullets": [], "omit": []}\n\nHope that helps!', "test")
    assert result == {"bullets": [], "omit": []}

    # Completely invalid
    result = _safe_json_parse("Not JSON at all. Just some text.", "test")
    assert result is None


def test_slugify():
    """_slugify produces clean URL-safe slugs."""
    from prepare import _slugify

    assert _slugify("Hello World") == "hello-world"
    assert _slugify("SIX Group (Switzerland)") == "six-group-switzerland"
    assert _slugify("Aave Companies") == "aave-companies"
    assert _slugify("  Spaces  & Symbols!!!  ") == "spaces-symbols"


def test_build_user_prompt():
    """_build_user_prompt includes all key fields from the job dict."""
    from prepare import _build_user_prompt

    job = {
        "title": "Senior PM",
        "company": "TestCo",
        "location": "Remote",
        "base_location": "Switzerland",
        "url": "https://example.com/job",
        "industry_sector": "fintech",
        "work_mode": "remote",
        "geo_zone": "europe",
        "company_size": "startup",
        "contract_type": "permanent",
        "company_country": "Switzerland",
        "language_required": "en",
        "summary": "A great fintech role.",
        "description": "Lead our API platform.",
    }

    prompt = _build_user_prompt(job)

    assert "Title: Senior PM" in prompt
    assert "Company: TestCo" in prompt
    assert "Industry sector: fintech" in prompt
    assert "Language required: en" in prompt
    assert "Lead our API platform." in prompt
    assert "Summary: A great fintech role." in prompt


# ── Main ──────────────────────────────────────────────────────────────────────

TESTS = [
    test_upsert_idempotency,
    test_language_fallback,
    test_bullet_json_parse_retry,
    test_slugify,
    test_build_user_prompt,
]


def run_prepare_tests() -> list[tuple[str, bool, str]]:
    global _results
    _results = []
    for fn in TESTS:
        _run(fn)
    return _results


if __name__ == "__main__":
    print("Prepare tests\n")
    results = run_prepare_tests()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    print(f"\n{'─' * 50}")
    print(f"  {passed}/{total} passed")
    if passed < total:
        for name, ok, err in results:
            if not ok:
                print(f"  ❌ {name}: {err}")

    sys.exit(0 if passed == total else 1)
