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
import json
import os
from unittest.mock import patch

from core.cv_agent.cli import parse_args, prompt_analysis_gate, run
from core.cv_agent.nodes import analysis_gate, approval_gate


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

    with patch("core.cv_agent.nodes.interrupt", side_effect=_interrupt):
        analysis_gate(_state())

    assert captured["job_title"] == ""
    assert captured["job_company"] == ""


def test_payload_surfaces_existing_title_company():
    captured = {}

    def _interrupt(payload):
        captured.update(payload)
        return {"decision": "proceed"}

    with patch("core.cv_agent.nodes.interrupt", side_effect=_interrupt):
        analysis_gate(_state(job={"title": "Product Owner Lead", "company": "Jobgether"}))

    assert captured["job_title"] == "Product Owner Lead"
    assert captured["job_company"] == "Jobgether"


def test_decision_overrides_patch_job():
    def _interrupt(payload):
        return {"decision": "proceed", "title": "Product Owner Lead", "company": "Jobgether"}

    with patch("core.cv_agent.nodes.interrupt", side_effect=_interrupt):
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


# ── approval_gate → review.json (spec 036, US4) ─────────────────────────────

def _review_state(tmp_path) -> dict:
    """A state reaching approval_gate, with a rendered cv_data_<slug>.json on disk."""
    local_dir = str(tmp_path)
    json_path = os.path.join(local_dir, "cv_data_test.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "title": "Senior PM",
            "profile": "fintech PM",
            "photo": "/secret/photo.png",
            "interests": [["Sports", "boxing"]],
        }, f)
    return {
        "job_id": "a" * 20,
        "job": {"title": "Product Manager", "company": "Acme",
                "url": "https://example.com/job"},
        "entry_kind": "id",
        "requirements": {"critical_requirements": ["payments"], "ats_keywords": ["PSD2"],
                         "letter_required": True, "recruiter_contact": None},
        "fit_analysis": {"fit_recap": "ok", "angle": "payments", "gaps": [],
                         "proposed_profile": "swiss", "profile_confidence": 0.8},
        "proposed_profile": "swiss",
        "critique": {"needs_revision": False, "notes": []},
        "lint": [{"id": "pages", "status": "pass", "detail": ""}],
        "lint_ok": True,
        "revision_count": 1,
        "refine_notes": ["tone down"],
        "output_paths": {"json": json_path, "docx": "x.docx", "pdf": "x.pdf",
                         "local_dir": local_dir},
        "thread_id": "b" * 20,
    }


def test_approval_gate_writes_review_json(tmp_path):
    state = _review_state(tmp_path)

    def _interrupt(payload):
        assert payload.get("output_paths")  # payload unchanged
        return {"approved": True}

    with patch("core.cv_agent.nodes.interrupt", side_effect=_interrupt):
        approval_gate(state)

    path = os.path.join(state["output_paths"]["local_dir"], "review.json")
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        bundle = json.load(f)

    assert set(bundle) == {
        "job", "requirements", "fit_analysis", "cv", "critique", "lint", "lint_ok",
        "revision_count", "refine_notes", "output_paths", "thread_id",
    }
    assert "photo" not in bundle["cv"]
    assert "interests" not in bundle["cv"]
    assert bundle["thread_id"] == "b" * 20
    # Narrowed fields: requirements drops letter_required/recruiter_contact;
    # fit_analysis drops profile_confidence.
    assert set(bundle["requirements"]) == {"critical_requirements", "ats_keywords"}
    assert set(bundle["fit_analysis"]) == {"fit_recap", "angle", "gaps", "proposed_profile"}
    assert os.path.getsize(path) <= 20 * 1024


# ── CLI: --auto / --resume (spec 036, US1/US2) ──────────────────────────────
#
# The CLI driver is tested against a fake compiled graph so the gate-loop
# branching is exercised without DB/LLM/render. ``_FakeApp`` replays a scripted
# sequence of ``get_state`` snapshots and records every ``invoke``.

class _FakeSnapshot:
    def __init__(self, next_nodes=(), values=None):
        self.next = tuple(next_nodes)
        self.values = values or {}
        self.tasks = []


class _FakeApp:
    def __init__(self, states):
        self._states = list(states)
        self._n = 0
        self.invokes = []

    def get_state(self, config):
        idx = min(self._n, len(self._states) - 1)
        self._n += 1
        return self._states[idx]

    def invoke(self, payload, config):
        self.invokes.append(payload)


def _run_fake(app, argv):
    """Run the CLI with the fake app + patches; return (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with patch("core.cv_agent.cli.SqliteSaver"), \
         patch("core.cv_agent.cli.compile_graph", return_value=app), \
         patch("core.cv_agent.cli.llm.is_configured", return_value=True), \
         patch("core.cv_agent.cli.JobStorage"), \
         patch("core.cv_agent.cli._resolve_profile_id", return_value="unified_jc"), \
         patch("sys.stdout", out), \
         patch("sys.stderr", err):
        rc = run(parse_args(argv))
    return rc, out.getvalue(), err.getvalue()


def test_auto_runs_to_approval_gate_without_approving():
    app = _FakeApp([
        _FakeSnapshot(("analysis_gate",)),
        _FakeSnapshot(("approval_gate",)),
        _FakeSnapshot((), {"decision": "proceed",
                           "output_paths": {"local_dir": "/tmp/x", "pdf": "/tmp/x/y.pdf"},
                           "thread_id": "b" * 20}),
    ])
    rc, out, _ = _run_fake(app, ["--auto", "https://example.com/job"])

    assert rc == 0
    assert len(app.invokes) == 2           # initial + analysis resume only
    resume = app.invokes[1].resume
    assert resume["decision"] == "proceed"
    assert "approved" not in resume        # never auto-approves
    assert "CV_AGENT_RESULT review" in out
    assert "b" * 20 in out


def test_auto_passes_overrides_to_analysis_resume():
    app = _FakeApp([
        _FakeSnapshot(("analysis_gate",)),
        _FakeSnapshot(("approval_gate",)),
        _FakeSnapshot((), {"decision": "proceed",
                           "output_paths": {"local_dir": "/tmp/x"}, "thread_id": "b" * 20}),
    ])
    rc, _, _ = _run_fake(app, [
        "--auto", "https://example.com/job",
        "--contact", "french", "--directives", "emphasise payments",
        "--title", "Product Lead", "--company", "Acme",
    ])

    assert rc == 0
    # --title/--company now seed job_title/job_company (job-id inputs), not the
    # CV header override.
    assert app.invokes[0]["job_title"] == "Product Lead"
    assert app.invokes[0]["job_company"] == "Acme"
    resume = app.invokes[1].resume
    assert resume["proposed_profile"] == "french"
    assert resume["user_directives"] == "emphasise payments"
    assert resume["title_override"] == ""


def test_resume_approve_prints_ok():
    app = _FakeApp([
        _FakeSnapshot(("approval_gate",)),
        _FakeSnapshot(()),
        _FakeSnapshot((), {"output_paths": {"pdf": "/tmp/x/y.pdf"}, "thread_id": "a" * 20}),
    ])
    rc, out, _ = _run_fake(app, ["--resume", "a" * 20, "--approve"])

    assert rc == 0
    assert app.invokes[0].resume["approved"] is True
    assert "CV_AGENT_RESULT ok" in out


def test_resume_reject_reprints_review():
    app = _FakeApp([
        _FakeSnapshot(("approval_gate",)),
        _FakeSnapshot(("approval_gate",)),
        _FakeSnapshot((), {"output_paths": {"local_dir": "/tmp/x"}, "thread_id": "a" * 20}),
    ])
    rc, out, _ = _run_fake(app, ["--resume", "a" * 20, "--reject", "tone down"])

    assert rc == 0
    assert app.invokes[0].resume["approved"] is False
    assert app.invokes[0].resume["approval_notes"] == "tone down"
    assert "CV_AGENT_RESULT review" in out


def test_resume_unknown_thread_exits_5():
    app = _FakeApp([_FakeSnapshot(())])   # no next → unknown/completed thread
    rc, _, err = _run_fake(app, ["--resume", "a" * 20, "--approve"])

    assert rc == 5
    assert app.invokes == []              # never resumed
    assert "CV_AGENT_RESULT error" in err


def test_resume_requires_exactly_one_of_approve_reject():
    _, _, err = _run_fake(_FakeApp([]), ["--resume", "a" * 20])
    assert "exactly one" in err
    _, _, err = _run_fake(_FakeApp([]), ["--resume", "a" * 20, "--approve", "--reject", "x"])
    assert "exactly one" in err


def test_resume_takes_no_reference():
    _, _, err = _run_fake(
        _FakeApp([]), ["--resume", "a" * 20, "--approve", "https://example.com/job"],
    )
    assert "takes no job reference" in err


