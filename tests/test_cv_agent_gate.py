"""Tests for the Gate 1 title/company guard (spec 029 fix).

Two surfaces are covered:

1. ``cv_agent.nodes.analysis_gate`` — the payload handed to ``interrupt()`` must
   surface ``job_title``/``job_company``, and the returned job patch must apply
   when the decision carries title/company overrides.
2. ``cv_agent.cli.prompt_analysis_gate`` — when ``job_title``/``job_company`` are
   blank, the CLI must force non-blank input before ``proceed`` (in the direct
   ``[1] proceed`` branch as well as ``[2] adjust``).
"""

import io
from unittest.mock import patch

from cv_agent.cli import prompt_analysis_gate
from cv_agent.nodes import analysis_gate


def _state(**overrides):
    state = {
        "score": 6,
        "score_reason": "good fit",
        "fit_analysis": {"fit_recap": "ok", "angle": "payments", "gaps": []},
        "proposed_profile": "swiss",
        "profile_confidence": 0.8,
        "job": {"title": "", "company": "", "url": "https://example.com/job"},
    }
    state.update(overrides)
    return state


# ── analysis_gate ──────────────────────────────────────────────────────────

def test_payload_surfaces_empty_title_company():
    captured = {}

    def _interrupt(payload):
        captured.update(payload)
        return {"decision": "proceed"}

    with patch("cv_agent.nodes.interrupt", side_effect=_interrupt):
        analysis_gate(_state())

    assert captured["job_title"] == ""
    assert captured["job_company"] == ""


def test_payload_surfaces_existing_title_company():
    captured = {}

    def _interrupt(payload):
        captured.update(payload)
        return {"decision": "proceed"}

    with patch("cv_agent.nodes.interrupt", side_effect=_interrupt):
        analysis_gate(_state(job={"title": "Product Owner Lead", "company": "Jobgether"}))

    assert captured["job_title"] == "Product Owner Lead"
    assert captured["job_company"] == "Jobgether"


def test_decision_overrides_patch_job():
    def _interrupt(payload):
        return {"decision": "proceed", "title": "Product Owner Lead", "company": "Jobgether"}

    with patch("cv_agent.nodes.interrupt", side_effect=_interrupt):
        out = analysis_gate(_state())

    assert out["decision"] == "proceed"
    assert out["job"]["title"] == "Product Owner Lead"
    assert out["job"]["company"] == "Jobgether"


# ── prompt_analysis_gate ───────────────────────────────────────────────────

def test_proceed_forces_blank_title_company():
    payload = {"job_title": "", "job_company": ""}
    inputs = ["1", "Product Owner Lead", "Jobgether"]

    with patch("builtins.input", side_effect=inputs):
        out = prompt_analysis_gate(payload)

    assert out == {
        "decision": "proceed",
        "user_directives": "",
        "title": "Product Owner Lead",
        "company": "Jobgether",
    }


def test_proceed_keeps_filled_title_company():
    payload = {"job_title": "Product Owner Lead", "job_company": "Jobgether"}

    with patch("builtins.input", side_effect=["1"]):
        out = prompt_analysis_gate(payload)

    assert out == {"decision": "proceed", "user_directives": ""}


def test_adjust_forces_blank_title_company():
    payload = {"job_title": "", "job_company": ""}
    # [2] adjust → directives → profile keep → Company → Job title → CV header keep
    inputs = ["2", "emphasise payments", "", "Jobgether", "Product Owner Lead", ""]

    with patch("builtins.input", side_effect=inputs):
        out = prompt_analysis_gate(payload)

    assert out["decision"] == "proceed"
    assert out["user_directives"] == "emphasise payments"
    assert out["company"] == "Jobgether"
    assert out["title"] == "Product Owner Lead"


def test_missing_warning_printed():
    payload = {"job_title": "", "job_company": ""}

    with patch("builtins.input", side_effect=["1", "Product Owner Lead", "Jobgether"]), \
         patch("sys.stdout", new_callable=io.StringIO) as buf:
        prompt_analysis_gate(payload)

    assert "Title/Company missing" in buf.getvalue()
