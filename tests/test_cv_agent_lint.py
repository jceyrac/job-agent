"""Seeded-defect unit tests for ``core.cv_agent.lint`` (spec 036, US3).

Each of the nine checks has a test that plants exactly one defect and asserts the
check flips to ``fail``, plus one clean fixture asserting ``all_pass``. Fixtures
are small hand-written ``rendered_data`` dicts + generated PDFs (``pypdf``).
"""

import json

import pytest
from pypdf import PdfWriter
from unittest.mock import patch

from core.cv_agent.lint import retryable_failures, run_lint
from core.cv_agent.nodes import lint, route_after_lint


# ── fixtures ────────────────────────────────────────────────────────────────

def _master() -> dict:
    """A minimal master whose facts the clean rendered CV mirrors exactly."""
    return {
        "contact": "CH",
        "profile": "Senior PM with 10+ years in fintech.",
        "competencies": [["Product", "Vision & roadmap"]],
        "roles": [
            {"title": "Product Manager", "dates": "Apr 2024 – Jun 2026",
             "sub": "Vaudoise – Lausanne, Switzerland", "bullets": ["Owned roadmap."]},
        ],
        "education": [["Master's", "EPF (2005)"]],
        "interests": [["Sports", "boxing"]],
    }


def _clean_rendered() -> dict:
    """The rendered data file shape — the master's facts plus derived fields."""
    return {
        "contact": "CH",
        "filename": "Jerome_Ceyrac_CV_Test",
        "title": "Senior Product Manager",
        "profile": "Senior PM with 10+ years in fintech.",
        "competencies": [["Product", "Vision & roadmap"]],
        "roles": [
            {"title": "Product Manager", "dates": "Apr 2024 – Jun 2026",
             "sub": "Vaudoise – Lausanne, Switzerland", "bullets": ["Owned roadmap."]},
        ],
        "education": [["Master's", "EPF (2005)"]],
        "interests": [["Sports", "boxing"]],
    }


def _write_pdf(path, n_pages: int) -> str:
    """Generate a blank n-page PDF and return its path."""
    writer = PdfWriter()
    for _ in range(n_pages):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        writer.write(f)
    return str(path)


def _result(results, check_id):
    return next(r for r in results if r["id"] == check_id)


@pytest.fixture
def one_page_pdf(tmp_path):
    return _write_pdf(tmp_path / "one.pdf", 1)


@pytest.fixture
def three_page_pdf(tmp_path):
    return _write_pdf(tmp_path / "three.pdf", 3)


# ── clean fixture ───────────────────────────────────────────────────────────

def test_all_checks_pass_on_clean_fixture(one_page_pdf):
    results, all_pass = run_lint(_clean_rendered(), one_page_pdf, _master())
    assert all_pass is True
    assert {r["id"] for r in results} == {
        "pages", "em_dash", "facts_numbers", "roles_match_master",
        "current_role_first", "contact_consistency", "in_progress_courses",
        "banned_phrases", "interests_present",
    }
    assert all(r["status"] == "pass" for r in results)


# ── pages ───────────────────────────────────────────────────────────────────

def test_pages_fails_over_two_pages(three_page_pdf):
    results, all_pass = run_lint(_clean_rendered(), three_page_pdf, _master())
    assert _result(results, "pages")["status"] == "fail"
    assert all_pass is False


# ── em_dash ─────────────────────────────────────────────────────────────────

def test_em_dash_fails(one_page_pdf):
    data = _clean_rendered()
    data["profile"] = "Senior PM — fintech."  # U+2014 em-dash
    results, _ = run_lint(data, one_page_pdf, _master())
    assert _result(results, "em_dash")["status"] == "fail"


# ── facts_numbers ───────────────────────────────────────────────────────────

def test_facts_numbers_fails_on_unknown_token(one_page_pdf):
    data = _clean_rendered()
    data["profile"] = "Senior PM managing 99 engineers."  # "99" not in master
    results, _ = run_lint(data, one_page_pdf, _master())
    assert _result(results, "facts_numbers")["status"] == "fail"


# ── roles_match_master ──────────────────────────────────────────────────────

def test_roles_match_master_fails_on_mismatch(one_page_pdf):
    data = _clean_rendered()
    data["roles"] = [
        {"title": "Product Manager", "dates": "Jan 2020 – Mar 2020",
         "sub": "Acme – Nowhere", "bullets": ["Owned roadmap."]},
    ]
    results, _ = run_lint(data, one_page_pdf, _master())
    assert _result(results, "roles_match_master")["status"] == "fail"


# ── current_role_first ──────────────────────────────────────────────────────

def test_current_role_first_fails_when_present_not_first(one_page_pdf):
    data = _clean_rendered()
    data["roles"] = [
        {"title": "Old PM", "dates": "Jan 2020 – Dec 2020",
         "sub": "Vaudoise – Lausanne, Switzerland", "bullets": ["Owned roadmap."]},
        {"title": "Current PM", "dates": "Jan 2024 – Present",
         "sub": "Vaudoise – Lausanne, Switzerland", "bullets": ["Owned roadmap."]},
    ]
    results, _ = run_lint(data, one_page_pdf, _master())
    assert _result(results, "current_role_first")["status"] == "fail"


# ── contact_consistency ─────────────────────────────────────────────────────

def test_contact_consistency_fails_ch_base_with_fr_contact(one_page_pdf):
    data = _clean_rendered()
    data["contact"] = "FR"
    data["profile"] = "Based in Lausanne, fintech PM."
    results, _ = run_lint(data, one_page_pdf, _master())
    assert _result(results, "contact_consistency")["status"] == "fail"


# ── in_progress_courses ─────────────────────────────────────────────────────

def test_in_progress_courses_fails_unlabelled(one_page_pdf):
    data = _clean_rendered()
    data["education"] = [["Self-directed", "AI Agents – Hugging Face AI Agents (2026)"]]
    results, _ = run_lint(data, one_page_pdf, _master())
    assert _result(results, "in_progress_courses")["status"] == "fail"


# ── banned_phrases ──────────────────────────────────────────────────────────

def test_banned_phrases_fails(one_page_pdf):
    data = _clean_rendered()
    data["profile"] = "Senior PM, fully willing to relocate."
    results, _ = run_lint(data, one_page_pdf, _master())
    assert _result(results, "banned_phrases")["status"] == "fail"


# ── interests_present ───────────────────────────────────────────────────────

def test_interests_present_fails_when_missing(one_page_pdf):
    data = _clean_rendered()
    data.pop("interests", None)
    results, _ = run_lint(data, one_page_pdf, _master())
    assert _result(results, "interests_present")["status"] == "fail"


# ── retryable_failures ──────────────────────────────────────────────────────

def test_retryable_failures_selects_retryable_set(one_page_pdf):
    data = _clean_rendered()
    data["profile"] = "Senior PM — fintech, fully willing to relocate, 99 engineers."
    data.pop("interests", None)
    results, _ = run_lint(data, one_page_pdf, _master())
    retryable = retryable_failures(results)
    assert {r["id"] for r in retryable} == {"em_dash", "facts_numbers"}


# ── route_after_lint (US5) ──────────────────────────────────────────────────

def test_route_after_lint_retryable_under_cap_returns_tailor():
    state = {"lint": [{"id": "em_dash", "status": "fail", "detail": "x"}],
             "revision_count": 0}
    assert route_after_lint(state) == "tailor_cv"


def test_route_after_lint_retryable_at_cap_returns_publish():
    state = {"lint": [{"id": "em_dash", "status": "fail", "detail": "x"}],
             "revision_count": 3}
    assert route_after_lint(state) == "publish"


def test_route_after_lint_nonretryable_returns_publish():
    state = {"lint": [{"id": "interests_present", "status": "fail", "detail": ""}],
             "revision_count": 0}
    assert route_after_lint(state) == "publish"


def test_lint_node_appends_retryable_failures_to_refine_notes(tmp_path):
    json_path = tmp_path / "cv_data_test.json"
    json_path.write_text(json.dumps({"title": "x"}), encoding="utf-8")
    state = {"output_paths": {"json": str(json_path), "pdf": str(tmp_path / "x.pdf")}}
    results = [
        {"id": "em_dash", "status": "fail", "detail": "bad dash"},
        {"id": "interests_present", "status": "fail", "detail": ""},
    ]
    with patch("core.cv_agent.nodes.run_lint", return_value=(results, False)), \
         patch("core.cv_agent.nodes.load_master", return_value={}), \
         patch("core.cv_agent.nodes._load_lint_allowlist", return_value={}):
        out = lint(state)

    assert out["lint_ok"] is False
    assert out["refine_notes"] == ["lint: em_dash — bad dash"]

