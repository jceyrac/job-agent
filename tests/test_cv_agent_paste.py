"""Paste-path guards (spec 036, US1 addition — closes the spec-029 ``--paste`` gap).

Three layers, matching the fix:

1. ``--paste --auto`` requires ``--title``/``--company`` (fail fast, before any
   LLM call); interactive ``--paste`` collects them up-front so the gate stays
   unchanged but job_id is still correct.
2. ``JobPosting.id`` raises when it would degenerate to a colliding id (no URL,
   empty title *or* empty company) — the case that produced ``b8d4448902a15c307dd9``.
3. ``renderer`` refuses to overwrite an existing ``<Company - Title>`` folder
   whose stored ``job_id`` differs.
"""

import io
from unittest.mock import patch

import pytest

from core.cv_agent.cli import parse_args, run, _paste_thread_id
from core.cv_agent.renderer import _refuse_clobber
from core.models import JobPosting


# ── 1. --paste --auto requires --title/--company (fail fast) ────────────────

def test_paste_auto_requires_title_and_company():
    with patch("sys.stdin", io.StringIO("some posting text")):
        rc = run(parse_args(["--paste", "--auto"]))
    assert rc == 2


def test_paste_auto_requires_company():
    with patch("sys.stdin", io.StringIO("some posting text")):
        rc = run(parse_args(["--paste", "--auto", "--title", "Product Lead"]))
    assert rc == 2


def test_paste_auto_requires_title():
    with patch("sys.stdin", io.StringIO("some posting text")):
        rc = run(parse_args(["--paste", "--auto", "--company", "Acme"]))
    assert rc == 2


# ── 1b. seeding: --title/--company flow into the initial state ──────────────

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


def _run_paste(argv, stdin_text, inputs=()):
    app = _FakeApp([_FakeSnapshot((), {"decision": "proceed",
                                       "output_paths": {"pdf": "/tmp/x/y.pdf"}})])
    out, err = io.StringIO(), io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin_text)), \
         patch("builtins.input", side_effect=list(inputs)), \
         patch("core.cv_agent.cli.SqliteSaver"), \
         patch("core.cv_agent.cli.compile_graph", return_value=app), \
         patch("core.cv_agent.cli.llm.is_configured", return_value=True), \
         patch("core.cv_agent.cli.JobStorage"), \
         patch("core.cv_agent.cli._resolve_profile_id", return_value="unified_jc"), \
         patch("sys.stdout", out), \
         patch("sys.stderr", err):
        rc = run(parse_args(argv))
    return rc, app, out.getvalue(), err.getvalue()


def test_paste_auto_seeds_title_company():
    rc, app, out, _ = _run_paste(
        ["--paste", "--auto", "--title", "Product Lead", "--company", "Pennylane"],
        "posting text",
    )
    assert rc == 0
    assert app.invokes[0]["job_title"] == "Product Lead"
    assert app.invokes[0]["job_company"] == "Pennylane"
    # The paste thread id is now the job_id (not sha256 of the pasted text).
    expected = JobPosting(source="paste", title="Product Lead", company="Pennylane",
                          location="", url="").id
    assert app.invokes[0]["thread_id"] == expected
    assert "CV_AGENT_RESULT review" in out


def test_paste_interactive_prompts_title_company():
    rc, app, _, _ = _run_paste(
        ["--paste"],
        "posting text",
        inputs=["Product Lead", "Acme"],
    )
    assert rc == 0
    assert app.invokes[0]["job_title"] == "Product Lead"
    assert app.invokes[0]["job_company"] == "Acme"


# ── 2. JobPosting.id refuses the degenerate (colliding) case ────────────────

def test_jobposting_id_raises_on_empty_title_company_url():
    j = JobPosting(source="paste", title="", company="", location="", url="")
    with pytest.raises(ValueError):
        _ = j.id


def test_jobposting_id_raises_on_empty_title():
    # No URL: an empty title alone must raise (not just when *both* are empty),
    # otherwise it would collide with every other empty-title posting.
    j = JobPosting(source="paste", title="", company="Pennylane", location="", url="")
    with pytest.raises(ValueError):
        _ = j.id


def test_jobposting_id_raises_on_empty_company():
    j = JobPosting(source="paste", title="Product Lead", company="", location="", url="")
    with pytest.raises(ValueError):
        _ = j.id


def test_two_empty_pastes_do_not_collide():
    # Pre-fix, two empty-title/company pastes both produced sha256("::paste")
    # (e.g. b8d4448902a15c307dd9). Both must now raise instead of colliding.
    a = JobPosting(source="paste", title="", company="", location="", url="")
    b = JobPosting(source="paste", title="", company="", location="", url="")
    with pytest.raises(ValueError):
        _ = a.id
    with pytest.raises(ValueError):
        _ = b.id


def test_distinct_pastes_produce_distinct_ids():
    a = JobPosting(source="paste", title="Product Lead", company="Pennylane",
                   location="", url="")
    b = JobPosting(source="paste", title="Product Lead", company="CRS",
                   location="", url="")
    assert len(a.id) == 20
    assert a.id != b.id


def test_paste_thread_id_equals_job_id():
    # The checkpoint thread key for a paste is the job's own id, so
    # `--resume <job_id> --approve` recovers the paused run (spec 036, US1).
    assert _paste_thread_id("Product Lead", "Pennylane") == \
        JobPosting(source="paste", title="Product Lead", company="Pennylane",
                   location="", url="").id
    assert _paste_thread_id("Product Lead", "CRS") == \
        JobPosting(source="paste", title="Product Lead", company="CRS",
                   location="", url="").id


# ── 3. renderer refuses to clobber a folder owned by another job ────────────

def test_refuse_clobber_different_job_id(tmp_path):
    outdir = tmp_path / "Acme - Product Lead"
    outdir.mkdir()
    (outdir / "job_id").write_text("a" * 20, encoding="utf-8")

    with pytest.raises(FileExistsError):
        _refuse_clobber(str(outdir), "b" * 20)


def test_refuse_clobber_same_job_id_is_allowed(tmp_path):
    outdir = tmp_path / "Acme - Product Lead"
    outdir.mkdir()
    (outdir / "job_id").write_text("a" * 20, encoding="utf-8")

    _refuse_clobber(str(outdir), "a" * 20)   # no raise


def test_refuse_clobber_no_marker_is_allowed(tmp_path):
    outdir = tmp_path / "Acme - Product Lead"
    outdir.mkdir()

    _refuse_clobber(str(outdir), "b" * 20)   # no prior owner → allowed
