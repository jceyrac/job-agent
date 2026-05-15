"""
tests/test_scorer_parsing.py — unit tests for scorer._parse_result()

Tests the three new fields (company_country, industry_sector, language_required)
including happy-path, fallback, and coercion of invalid values.
No LLM calls — mocks raw JSON strings.

Usage:
    python tests/test_scorer_parsing.py
"""

import sys
import os
import json
import traceback
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scorer import _parse_result, _parse_extraction_result, evaluate_for_profile
from models import JobPosting

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


def _raw(**overrides) -> str:
    base = {
        "score": 7,
        "reason": "Solid PM role",
        "summary": "A fintech company.",
        "work_mode": "hybrid",
        "company_size": "scaleup",
        "contract_type": "permanent",
        "geo_zone": "europe",
        "company_country": "Switzerland",
        "industry_sector": "fintech",
        "language_required": "english",
    }
    base.update(overrides)
    return json.dumps(base)


def test_all_three_fields_present():
    r = _parse_result(_raw())
    assert r["company_country"]   == "Switzerland"
    assert r["industry_sector"]   == "fintech"
    assert r["language_required"] == "english"


def test_company_country_whitespace_stripped():
    r = _parse_result(_raw(company_country="  Germany  "))
    assert r["company_country"] == "Germany"


def test_company_country_missing_defaults_to_unknown():
    raw = json.loads(_raw())
    del raw["company_country"]
    r = _parse_result(json.dumps(raw))
    assert r["company_country"] == "unknown"


def test_company_country_null_defaults_to_unknown():
    r = _parse_result(_raw(company_country=None))
    assert r["company_country"] == "unknown"


def test_industry_sector_valid_all_codes():
    valid = [
        "web3_crypto", "fintech", "tech_saas", "ai_ml", "e_commerce",
        "healthcare", "pharma", "retail", "manufacturing", "government",
        "consulting", "education", "media", "energy", "other",
    ]
    for code in valid:
        r = _parse_result(_raw(industry_sector=code))
        assert r["industry_sector"] == code, f"Expected {code!r}, got {r['industry_sector']!r}"


def test_industry_sector_invalid_coerced_to_other():
    r = _parse_result(_raw(industry_sector="food_tech"))
    assert r["industry_sector"] == "other"


def test_industry_sector_missing_defaults_to_other():
    raw = json.loads(_raw())
    del raw["industry_sector"]
    r = _parse_result(json.dumps(raw))
    assert r["industry_sector"] == "other"


def test_industry_sector_null_defaults_to_other():
    r = _parse_result(_raw(industry_sector=None))
    assert r["industry_sector"] == "other"


def test_language_required_valid_all_codes():
    valid = ["english", "french", "german", "italian", "spanish", "multiple", "unknown"]
    for lang in valid:
        r = _parse_result(_raw(language_required=lang))
        assert r["language_required"] == lang


def test_language_required_invalid_coerced_to_unknown():
    r = _parse_result(_raw(language_required="portuguese"))
    assert r["language_required"] == "unknown"


def test_language_required_missing_defaults_to_unknown():
    raw = json.loads(_raw())
    del raw["language_required"]
    r = _parse_result(json.dumps(raw))
    assert r["language_required"] == "unknown"


def test_language_required_null_defaults_to_unknown():
    r = _parse_result(_raw(language_required=None))
    assert r["language_required"] == "unknown"


def test_existing_fields_unaffected():
    r = _parse_result(_raw(score=9, work_mode="remote", geo_zone="us_only"))
    assert r["score"]      == 9
    assert r["work_mode"]  == "remote"
    assert r["geo_zone"]   == "us_only"


# ══════════════════════════════════════════════════════════════════════════════
# Extraction parsing tests (_parse_extraction_result)
# ══════════════════════════════════════════════════════════════════════════════


def _raw_extraction(**overrides) -> str:
    """Generate extraction-only JSON (no score/reason)."""
    base = {
        "company_country":   "Switzerland",
        "industry_sector":   "fintech",
        "language_required": "english",
        "work_mode":         "remote",
        "geo_zone":          "europe",
        "company_size":      "startup",
        "contract_type":     "permanent",
        "summary":           "A fintech startup based in Zurich.",
    }
    base.update(overrides)
    return json.dumps(base)


def test_extraction_all_fields_present():
    r = _parse_extraction_result(_raw_extraction())
    assert r["company_country"]   == "Switzerland"
    assert r["industry_sector"]   == "fintech"
    assert r["language_required"] == "english"
    assert r["work_mode"]         == "remote"
    assert r["geo_zone"]          == "europe"
    assert r["company_size"]      == "startup"
    assert r["contract_type"]     == "permanent"
    assert r["summary"]           == "A fintech startup based in Zurich."


def test_extraction_no_score_or_reason():
    """Extraction result must not contain score/reason fields."""
    r = _parse_extraction_result(_raw_extraction())
    assert "score" not in r
    assert "reason" not in r


def test_extraction_sector_defaults():
    r = _parse_extraction_result(_raw_extraction(industry_sector="food_tech"))
    assert r["industry_sector"] == "other"


def test_extraction_language_defaults():
    r = _parse_extraction_result(_raw_extraction(language_required="portuguese"))
    assert r["language_required"] == "unknown"


def test_extraction_country_null_defaults():
    r = _parse_extraction_result(_raw_extraction(company_country=None))
    assert r["company_country"] == "unknown"


def test_extraction_summary_empty_defaults():
    r = _parse_extraction_result(_raw_extraction(summary=None))
    assert r["summary"] == "Description non disponible — consulter l'offre directement."


def test_extraction_all_work_modes():
    for wm in ("remote", "hybrid", "on-site", "unknown"):
        r = _parse_extraction_result(_raw_extraction(work_mode=wm))
        assert r["work_mode"] == wm, f"Expected {wm!r}, got {r['work_mode']!r}"


def test_extraction_all_geo_zones():
    for gz in ("europe", "us_only", "global_remote", "apac", "latam", "unknown"):
        r = _parse_extraction_result(_raw_extraction(geo_zone=gz))
        assert r["geo_zone"] == gz, f"Expected {gz!r}, got {r['geo_zone']!r}"


EXTRACTION_TESTS = [
    test_extraction_all_fields_present,
    test_extraction_no_score_or_reason,
    test_extraction_sector_defaults,
    test_extraction_language_defaults,
    test_extraction_country_null_defaults,
    test_extraction_summary_empty_defaults,
    test_extraction_all_work_modes,
    test_extraction_all_geo_zones,
]


# ══════════════════════════════════════════════════════════════════════════════
# Evaluation tests (evaluate_for_profile — Tier 0 deterministic filters)
# ══════════════════════════════════════════════════════════════════════════════


class _EvalProfile:
    excluded_languages  = ["german"]
    excluded_sectors    = ["pharma", "retail"]
    allowed_countries   = ["Switzerland"]
    banned_countries    = ["United States", "Canada"]
    hybrid_ok_countries = ["Switzerland"]
    allowed_work_modes  = ["remote", "hybrid", "unknown"]
    scoring_context     = "Test profile with strict filters."


class _EvalProfileNoFilters:
    excluded_languages = []
    excluded_sectors   = []
    allowed_countries  = None
    allowed_work_modes = ["remote", "hybrid", "unknown", "on-site"]
    scoring_context    = ""


def _eval_job(**overrides) -> JobPosting:
    defaults = dict(
        source="Test", title="PM", company="Acme", location="Remote",
        url="https://example.com/eval_job",
        description="A product management role.",
        summary="A PM role at Acme.",
        work_mode="remote",
        geo_zone="europe",
        company_size="startup",
        contract_type="permanent",
        company_country="Switzerland",
        industry_sector="fintech",
        language_required="english",
    )
    defaults.update(overrides)
    return JobPosting(**defaults)


def test_eval_tier0_language_exclusion():
    job = _eval_job(language_required="german")
    r = evaluate_for_profile(job, _EvalProfile())
    assert r["score"] == 1
    assert r["scored_by"] == "tier_0"
    assert "language" in r["reason"]


def test_eval_tier0_sector_exclusion():
    job = _eval_job(industry_sector="pharma")
    r = evaluate_for_profile(job, _EvalProfile())
    assert r["score"] == 1
    assert r["scored_by"] == "tier_0"
    assert "sector" in r["reason"]


def test_eval_tier0_country_filter():
    job = _eval_job(company_country="United States")
    r = evaluate_for_profile(job, _EvalProfile())
    assert r["score"] == 2
    assert r["scored_by"] == "tier_0"
    assert "country" in r["reason"]


def test_eval_tier0_country_unknown_passes():
    job = _eval_job(company_country="unknown")
    with patch("scorer._call_groq_fallback_chain") as mock:
        mock.return_value = ('{"score": 5, "reason": "Test pass"}', "test-model")
        r = evaluate_for_profile(job, _EvalProfile())
    assert r["scored_by"] != "tier_0"
    assert r["score"] == 5


def test_eval_tier0_work_mode_filter():
    job = _eval_job(work_mode="on-site")
    r = evaluate_for_profile(job, _EvalProfile())
    assert r["score"] == 1
    assert r["scored_by"] == "tier_0"
    assert "work_mode" in r["reason"]


def test_eval_tier0_first_match_wins():
    job = _eval_job(language_required="german", industry_sector="pharma",
                    company_country="United States")
    r = evaluate_for_profile(job, _EvalProfile())
    assert r["scored_by"] == "tier_0"
    assert r["score"] == 1
    assert "language" in r["reason"]


def test_eval_tier0_no_exclusion_passes():
    job = _eval_job()
    with patch("scorer._call_groq_fallback_chain") as mock:
        mock.return_value = ('{"score": 6, "reason": "Solid match"}', "test-model")
        r = evaluate_for_profile(job, _EvalProfileNoFilters())
    assert r["scored_by"] != "tier_0"
    assert r["score"] == 6


def test_eval_tier0_passthrough_fields_preserved():
    job = _eval_job(language_required="german")
    r = evaluate_for_profile(job, _EvalProfile())
    assert r["company_country"]   == "Switzerland"
    assert r["industry_sector"]   == "fintech"
    assert r["language_required"] == "german"
    assert r["work_mode"]         == "remote"
    assert r["geo_zone"]          == "europe"
    assert r["company_size"]      == "startup"
    assert r["contract_type"]     == "permanent"


def test_eval_tier0_allowed_countries_none_means_no_restriction():
    job = _eval_job(company_country="United States", language_required="french",
                    industry_sector="fintech")
    with patch("scorer._call_groq_fallback_chain") as mock:
        mock.return_value = ('{"score": 7, "reason": "Good fit"}', "test-model")
        r = evaluate_for_profile(job, _EvalProfileNoFilters())
    assert r["scored_by"] != "tier_0"
    assert r["score"] == 7


def test_eval_tier0_banned_country():
    """global_remote job with company_country='United States' is blocked by denylist."""
    job = _eval_job(company_country="United States", geo_zone="global_remote")
    r = evaluate_for_profile(job, _EvalProfileBannedOnly())
    assert r["score"] == 1
    assert r["scored_by"] == "tier_0"
    assert "banned country" in r["reason"]


def test_eval_tier0_hybrid_outside_ch():
    """Hybrid role in Ireland → score 2 with reason mentioning hybrid outside allowed countries."""
    job = _eval_job(work_mode="hybrid", company_country="Ireland")
    r = evaluate_for_profile(job, _EvalProfileHybridGate())
    assert r["score"] == 2
    assert r["scored_by"] == "tier_0"
    assert "hybrid outside allowed countries" in r["reason"]


def test_eval_tier0_hybrid_in_ch_passes():
    """Hybrid role in Switzerland still reaches LLM tier."""
    job = _eval_job(work_mode="hybrid", company_country="Switzerland")
    with patch("scorer._call_groq_fallback_chain") as mock:
        mock.return_value = ('{"score": 8, "reason": "Good hybrid CH fit"}', "test-model")
        r = evaluate_for_profile(job, _EvalProfile())
    assert r["scored_by"] != "tier_0"
    assert r["score"] == 8


def test_eval_tier0_remote_spain_passes():
    """Remote job from Spain (allowed country) still reaches LLM tier."""
    job = _eval_job(work_mode="remote", company_country="Spain")
    with patch("scorer._call_groq_fallback_chain") as mock:
        mock.return_value = ('{"score": 7, "reason": "Remote EU fit"}', "test-model")
        r = evaluate_for_profile(job, _EvalProfileEU())
    assert r["scored_by"] != "tier_0"
    assert r["score"] == 7


def test_eval_tier0_unknown_country_passes_both():
    """company_country='unknown' is NOT blocked by either new rule."""
    job = _eval_job(company_country="unknown", geo_zone="global_remote", work_mode="hybrid")
    with patch("scorer._call_groq_fallback_chain") as mock:
        mock.return_value = ('{"score": 5, "reason": "Uncertain but plausible"}', "test-model")
        r = evaluate_for_profile(job, _EvalProfile())
    assert r["scored_by"] != "tier_0"
    assert r["score"] == 5


class _EvalProfileNoRestrictions:
    excluded_languages  = []
    excluded_sectors    = []
    allowed_countries   = None
    banned_countries    = []
    hybrid_ok_countries = []
    allowed_work_modes  = ["remote", "hybrid", "unknown", "on-site"]
    scoring_context     = ""


class _EvalProfileBannedOnly:
    excluded_languages  = []
    excluded_sectors    = []
    allowed_countries   = None
    banned_countries    = ["United States", "Canada"]
    hybrid_ok_countries = []
    allowed_work_modes  = ["remote", "hybrid", "unknown"]
    scoring_context     = ""


class _EvalProfileHybridGate:
    excluded_languages  = []
    excluded_sectors    = []
    allowed_countries   = None
    banned_countries    = []
    hybrid_ok_countries = ["Switzerland"]
    allowed_work_modes  = ["remote", "hybrid", "unknown"]
    scoring_context     = ""


class _EvalProfileEU:
    excluded_languages  = []
    excluded_sectors    = []
    allowed_countries   = ["Switzerland", "France", "Spain", "Germany", "Ireland"]
    banned_countries    = []
    hybrid_ok_countries = []
    allowed_work_modes  = ["remote", "hybrid", "unknown"]
    scoring_context     = ""


def test_eval_tier0_empty_lists_noop():
    """Profile with empty banned/hybrid_ok lists → rules don't fire (web3_remote compat)."""
    job = _eval_job(work_mode="hybrid", company_country="Ireland", geo_zone="global_remote")
    with patch("scorer._call_groq_fallback_chain") as mock:
        mock.return_value = ('{"score": 6, "reason": "Ireland hybrid passes"}', "test-model")
        r = evaluate_for_profile(job, _EvalProfileNoRestrictions())
    assert r["scored_by"] != "tier_0"
    assert r["score"] == 6


class _EvalProfileWithDenylist:
    excluded_languages    = []
    excluded_sectors      = []
    allowed_countries     = None
    banned_countries      = []
    hybrid_ok_countries   = []
    denylisted_companies  = ["EWOR", "EWOR GmbH", "Mercor"]
    allowed_work_modes    = ["remote", "hybrid", "unknown"]
    scoring_context       = ""


def test_eval_tier0_denylist_company_blocked():
    """Job from 'EWOR GmbH' short-circuits to score 1 when profile opts in."""
    job = _eval_job(company="EWOR GmbH")
    r = evaluate_for_profile(job, _EvalProfileWithDenylist())
    assert r["score"] == 1
    assert r["scored_by"] == "tier_0"
    assert "denylisted company" in r["reason"]


def test_eval_tier0_denylist_stripe_passes():
    """Job from 'Stripe' passes through — not on the denylist."""
    job = _eval_job(company="Stripe")
    with patch("scorer._call_groq_fallback_chain") as mock:
        mock.return_value = ('{"score": 7, "reason": "Good fintech role"}', "test-model")
        r = evaluate_for_profile(job, _EvalProfileWithDenylist())
    assert r["scored_by"] != "tier_0"
    assert r["score"] == 7


def test_eval_tier0_denylist_noop_on_empty():
    """Job from 'EWOR GmbH' passes through on a profile that did not opt in."""
    job = _eval_job(company="EWOR GmbH")
    with patch("scorer._call_groq_fallback_chain") as mock:
        mock.return_value = ('{"score": 5, "reason": "No denylist here"}', "test-model")
        r = evaluate_for_profile(job, _EvalProfileNoRestrictions())
    assert r["scored_by"] != "tier_0"
    assert r["score"] == 5


EVALUATION_TESTS = [
    test_eval_tier0_language_exclusion,
    test_eval_tier0_sector_exclusion,
    test_eval_tier0_country_filter,
    test_eval_tier0_country_unknown_passes,
    test_eval_tier0_work_mode_filter,
    test_eval_tier0_first_match_wins,
    test_eval_tier0_no_exclusion_passes,
    test_eval_tier0_passthrough_fields_preserved,
    test_eval_tier0_allowed_countries_none_means_no_restriction,
    test_eval_tier0_banned_country,
    test_eval_tier0_hybrid_outside_ch,
    test_eval_tier0_hybrid_in_ch_passes,
    test_eval_tier0_remote_spain_passes,
    test_eval_tier0_unknown_country_passes_both,
    test_eval_tier0_empty_lists_noop,
    test_eval_tier0_denylist_company_blocked,
    test_eval_tier0_denylist_stripe_passes,
    test_eval_tier0_denylist_noop_on_empty,
]


TESTS = [
    test_all_three_fields_present,
    test_company_country_whitespace_stripped,
    test_company_country_missing_defaults_to_unknown,
    test_company_country_null_defaults_to_unknown,
    test_industry_sector_valid_all_codes,
    test_industry_sector_invalid_coerced_to_other,
    test_industry_sector_missing_defaults_to_other,
    test_industry_sector_null_defaults_to_other,
    test_language_required_valid_all_codes,
    test_language_required_invalid_coerced_to_unknown,
    test_language_required_missing_defaults_to_unknown,
    test_language_required_null_defaults_to_unknown,
    test_existing_fields_unaffected,
] + EXTRACTION_TESTS + EVALUATION_TESTS


if __name__ == "__main__":
    print("Scorer parsing tests\n")
    for fn in TESTS:
        _run(fn)

    passed = sum(1 for _, ok, _ in _results if ok)
    total  = len(_results)
    print(f"\n{'─'*50}")
    print(f"  {passed}/{total} passed")
    if passed < total:
        for name, ok, err in _results:
            if not ok:
                print(f"  ❌ {name}: {err}")
    sys.exit(0 if passed == total else 1)
