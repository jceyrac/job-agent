"""Tests for title_gate.title_matches_profile."""

from title_gate import title_matches_profile


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
