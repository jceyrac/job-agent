"""Deterministic contact-profile classifier + precedence (spec 038).

Pure unit tests — no LLM, no network, no DB. Covers ``core.geo_ch``'s detector,
the fixed precedence, the renderer's missing-profile raise, and the golden-prompt
guard (``proposed_profile`` + the forbidden wording absent from
``ANALYZE_AND_PLAN_PROMPT``).
"""

import pytest

from core.geo_ch import classify_location, compute_contact_profile, normalize_country
from core.cv_agent.prompts import ANALYZE_AND_PLAN_PROMPT
from core.cv_agent.renderer import render_documents


# ── detector: classify_location ─────────────────────────────────────────────

def test_berlin_is_non_ch():
    assert classify_location("Berlin, Germany") == "non_CH"


def test_cologne_is_non_ch():
    assert classify_location("Köln / Cologne") == "non_CH"


def test_swiss_city_is_ch():
    assert classify_location("Lausanne, Switzerland") == "CH"


def test_remote_switzerland_is_ch():
    assert classify_location("Remote (Switzerland)") == "CH"


def test_remote_ch_only_is_ch():
    assert classify_location("Remote – CH only") == "CH"


def test_zurich_or_berlin_is_mixed():
    assert classify_location("Zurich or Berlin") == "mixed"


def test_empty_is_unknown():
    assert classify_location("") == "unknown"


def test_no_location_is_unknown():
    assert classify_location("We are a fast-growing fintech.") == "unknown"


def test_multilingual_swiss_names_are_ch():
    assert classify_location("Genève") == "CH"
    assert classify_location("Zürich") == "CH"
    assert classify_location("Suisse") == "CH"


def test_ch_postal_is_ch():
    assert classify_location("CH-8000 Zürich") == "CH"


# ── normalize_country ───────────────────────────────────────────────────────

def test_normalize_country_iso2():
    assert normalize_country("ch") == "CH"
    assert normalize_country("DE") == "DE"


def test_normalize_country_names():
    assert normalize_country("Suisse") == "CH"
    assert normalize_country("Germany") == "DE"
    assert normalize_country("france") == "FR"


def test_normalize_country_unknown_returns_none():
    assert normalize_country("Atlantis") is None
    assert normalize_country("") is None


# ── precedence: compute_contact_profile ─────────────────────────────────────

def _job(**overrides):
    job = {"location": "", "country_code": None, "description": ""}
    job.update(overrides)
    return job


def test_contact_override_wins_over_everything():
    assert compute_contact_profile("paste", _job(location="Lausanne"), "french", "CH") \
        == ("french", "explicit", None)


def test_country_override_swiss():
    assert compute_contact_profile("paste", _job(), None, "Switzerland") \
        == ("swiss", "explicit_country", None)


def test_country_override_non_swiss():
    assert compute_contact_profile("paste", _job(), None, "Germany") \
        == ("french", "explicit_country", None)


def test_country_override_beats_db_and_detector():
    assert compute_contact_profile("id", _job(country_code="CH", location="Lausanne"), None, "France") \
        == ("french", "explicit_country", None)


def test_db_country_swiss():
    assert compute_contact_profile("id", _job(country_code="CH"), None, None) \
        == ("swiss", "db_country", None)


def test_db_country_non_swiss():
    assert compute_contact_profile("id", _job(country_code="DE"), None, None) \
        == ("french", "db_country", None)


def test_db_country_ignored_for_paste():
    # paste/url entries never use the (LLM-extracted) country_code — detector only.
    assert compute_contact_profile("paste", _job(country_code="CH", location="Berlin"), None, None) \
        == ("french", "detected", None)


def test_detected_swiss():
    assert compute_contact_profile("paste", _job(location="Zürich"), None, None) \
        == ("swiss", "detected", None)


def test_detected_non_ch():
    assert compute_contact_profile("paste", _job(location="Berlin"), None, None) \
        == ("french", "detected", None)


def test_mixed_flags_and_defaults_to_french():
    assert compute_contact_profile("paste", _job(location="Zurich or Berlin"), None, None) \
        == ("french", "detected", "mixed_locations")


def test_unknown_defaults_to_french_with_flag():
    assert compute_contact_profile("paste", _job(location=""), None, None) \
        == ("french", "default", "unknown_country")


def test_location_field_plus_labeled_description():
    # Swiss HQ in the company blurb must not turn a Berlin role Swiss: only the
    # location field + labelled lines count, never the company description.
    job = _job(
        location="Berlin, Germany",
        description="About us\nHQ in Zurich, Switzerland.\nLocation: Berlin\n"
                    "We are hiring across Europe.",
    )
    assert compute_contact_profile("paste", job, None, None) == ("french", "detected", None)


def test_labeled_line_without_location_field():
    job = _job(location="", description="Standort: Zürich\nBeschreibung…")
    assert compute_contact_profile("paste", job, None, None) == ("swiss", "detected", None)


# ── renderer raises on a missing/invalid profile (FR-004) ───────────────────

def test_render_raises_on_missing_profile():
    with pytest.raises(ValueError):
        render_documents({}, None, "slug", {}, "/tmp/x")


def test_render_raises_on_invalid_profile():
    with pytest.raises(ValueError):
        render_documents({}, "dach", "slug", {}, "/tmp/x")


# ── golden prompt (FR-003) ──────────────────────────────────────────────────

def test_prompt_has_no_profile_decision():
    assert "proposed_profile" not in ANALYZE_AND_PLAN_PROMPT
    assert "profile_confidence" not in ANALYZE_AND_PLAN_PROMPT
    lowered = ANALYZE_AND_PLAN_PROMPT.lower()
    assert "dach" not in lowered
    assert "swiss employer" not in lowered
    assert "ch-viable" not in lowered
