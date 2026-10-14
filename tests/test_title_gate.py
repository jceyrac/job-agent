"""Tests for title_gate.title_matches_profile and title_gate.title_excluded."""

from core.title_gate import title_excluded, title_matches_profile


def test_felfel_regression():
    """Régression canonique : un "Senior Tech Product Owner" passe quand
    "product owner" fait partie des titres du profil."""
    assert title_matches_profile("Senior Tech Product Owner", ["product owner"])


def test_empty_titles_is_wide_net():
    """Quand les titres du profil sont vides, le gate est ouvert (filet large)."""
    assert title_matches_profile("Anything At All", []) is True
    assert title_matches_profile("Anything At All", None) is True


def test_non_matching_title_rejected():
    """Un titre hors profil est rejeté."""
    assert title_matches_profile(
        "Warehouse Forklift Operator",
        ["product owner", "product manager"]
    ) is False


def test_empty_title_rejected():
    """Un titre vide est toujours rejeté."""
    assert title_matches_profile("", ["product owner"]) is False


# ── title_excluded — word-boundary exclude helper (spec 040) ───────────────────

def test_excluded_word_matches():
    assert title_excluded("Product Manager Intern", ["intern", "junior"]) == "intern"


def test_excluded_word_boundary_no_substring_false_positive():
    """'international' must NOT match 'intern' (word boundary)."""
    assert title_excluded("International Product Manager", ["intern"]) is None


def test_excluded_word_case_insensitive():
    assert title_excluded("Product Manager JUNIOR", ["junior"]) == "junior"


def test_excluded_word_empty_inputs():
    assert title_excluded("Product Manager", []) is None
    assert title_excluded("Product Manager", None) is None
    assert title_excluded("", ["intern"]) is None


def test_excluded_word_returns_first_offending_word():
    assert title_excluded("Junior Intern", ["junior", "intern"]) == "junior"


def test_excluded_word_skips_blank_words():
    assert title_excluded("Intern", ["", "intern"]) == "intern"
