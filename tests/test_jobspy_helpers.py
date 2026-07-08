"""Regression tests for scrapers/boards/_jobspy_helpers.py::dataframe_to_postings.

Covers the work_mode classification branches, especially the noisy is_remote
flag on LinkedIn (Scenario B: is_remote=True + concrete city location must NOT
produce "remote").
"""

import pandas as pd
from scrapers.boards._jobspy_helpers import dataframe_to_postings


def _make_row(**kwargs) -> pd.DataFrame:
    """Build a single-row DataFrame with defaults matching JobSpy output."""
    defaults = {
        "title": "Test Job",
        "company": "Test Corp",
        "location": "",
        "job_url": "https://example.com/job/1",
        "description": "",
        "date_posted": None,
        "work_from_home_type": None,
        "is_remote": False,
        "min_amount": None,
        "max_amount": None,
        "currency": None,
    }
    defaults.update(kwargs)
    return pd.DataFrame([defaults])


def test_is_remote_flag_with_concrete_city_is_not_remote():
    """MANUS-like regression: is_remote=True + city location → must NOT be 'remote'."""
    df = _make_row(location="Eindhoven Area", is_remote=True)
    postings = dataframe_to_postings(df, source="LinkedIn")
    assert len(postings) == 1
    assert postings[0].work_mode == "unknown", \
        f"Expected 'unknown' but got '{postings[0].work_mode}'"


def test_empty_location_plus_is_remote_yields_remote():
    df = _make_row(location="", is_remote=True)
    postings = dataframe_to_postings(df, source="LinkedIn")
    assert len(postings) == 1
    assert postings[0].work_mode == "remote"


def test_remote_in_location_string_yields_remote():
    df = _make_row(location="Remote - Europe", is_remote=True)
    postings = dataframe_to_postings(df, source="LinkedIn")
    assert len(postings) == 1
    assert postings[0].work_mode == "remote"


def test_hybrid_wfh_type_yields_hybrid():
    df = _make_row(location="Zürich", work_from_home_type="hybrid", is_remote=False)
    postings = dataframe_to_postings(df, source="LinkedIn")
    assert len(postings) == 1
    assert postings[0].work_mode == "hybrid"


def test_no_flags_concrete_city_yields_on_site():
    df = _make_row(location="Paris", is_remote=False, work_from_home_type=None)
    postings = dataframe_to_postings(df, source="LinkedIn")
    assert len(postings) == 1
    assert postings[0].work_mode == "on-site"


def test_is_remote_with_city_and_hybrid_wfh_wins():
    """hybrid in work_from_home_type takes priority over noisy is_remote."""
    df = _make_row(location="Amsterdam", work_from_home_type="hybrid", is_remote=True)
    postings = dataframe_to_postings(df, source="LinkedIn")
    assert len(postings) == 1
    assert postings[0].work_mode == "hybrid"
