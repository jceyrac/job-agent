"""tests/test_score_prefilter.py — scoring SQL pre-filter drops title clauses (spec 040, FR-006).

Title judgement is the first Tier 0 rule in the scorer, so the SQL pre-filter
handed to ``get_jobs_for_scoring`` must NOT carry ``title_contains`` /
``exclude_title_contains``. If it does, title-mismatched jobs are silently
skipped and never receive a ``tier_0`` score — the exact regression this guards.
"""
from core.profiles import SearchProfile
from core.score import _scoring_pre_filter


def _profile(pre_filter):
    return SearchProfile(
        id="t",
        name="T",
        allowed_geo_zones=[],
        allowed_work_modes=[],
        location_keywords=[],
        company_sizes=[],
        pre_filter=pre_filter,
    )


def test_drops_title_clauses_keeps_location():
    p = _profile({
        "title_contains": ["product manager", "head of product"],
        "exclude_title_contains": ["junior", "intern"],
        "exclude_location_contains": ["united states", "new york"],
    })
    assert _scoring_pre_filter(p) == {
        "exclude_location_contains": ["united states", "new york"],
    }


def test_empty_pre_filter_is_empty():
    assert _scoring_pre_filter(_profile({})) == {}


def test_none_pre_filter_is_empty():
    assert _scoring_pre_filter(_profile(None)) == {}


def test_only_title_clauses_yield_empty():
    p = _profile({
        "title_contains": ["product manager"],
        "exclude_title_contains": ["junior"],
    })
    assert _scoring_pre_filter(p) == {}
