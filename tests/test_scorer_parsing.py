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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scorer import _parse_result

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
]


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
