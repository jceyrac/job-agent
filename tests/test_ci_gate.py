"""Tests for the CI-gate PreToolUse hook (spec 044c, FR-001–FR-004).

`.claude/hooks/ci_gate.py` is a pure-stdlib executable driven by one JSON object
on stdin. These tests load it as a module and drive `run(payload)` with the git
and `gh` subprocess side-effects mocked — they never touch the network, never
call a real `gh`, and never run `git`.

Exit-code contract (`contracts/hooks.md`): 0 = pass, 2 = blocking failure,
any other non-zero = non-blocking failure.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"

BLOCKING = 2
NON_BLOCKING = 3


def _load_hook(name: str):
    path = HOOKS_DIR / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ci_gate():
    return _load_hook("ci_gate.py")


def _payload(command: str) -> dict:
    return {"tool_input": {"command": command}}


def _gh(returncode, stdout):
    """Return a `_gh_runs` stub producing `(returncode, stdout)`."""
    return lambda sha: (returncode, stdout)


# --- target detection (pure, no subprocess) ---------------------------------


@pytest.mark.parametrize(
    "command,expected",
    [
        ("", None),
        ("git status", None),
        ("git log --oneline", None),
        ("git merge --abort", None),
        ("git push origin feature", None),
        ("scripts/staging.sh down", None),
        ("git merge --ff-only abc1234", ("merge", "abc1234")),
        ("git merge --no-ff deadbeef", ("merge", "deadbeef")),
        ("git merge -m 'ship' cafe1234", ("merge", "cafe1234")),
        ("git -C /opt/job-agent merge --ff-only abc1234", ("merge", "abc1234")),
        ("git push origin main", ("push_main", None)),
        ("git push -u origin main", ("push_main", None)),
        ("git push origin HEAD:main", ("push_main", None)),
        ("scripts/staging.sh up abc1234", ("staging", "abc1234")),
        ("./scripts/staging.sh up main", ("staging", "main")),
        ("./scripts/deploy.sh", ("deploy", None)),
        ("scripts/deploy.sh", ("deploy", None)),
        # compound commands: the first gated segment wins
        ("git checkout main && git merge --ff-only abc1234 && git push origin main",
         ("merge", "abc1234")),
        ("git checkout main && git push origin main", ("push_main", None)),
    ],
)
def test_gate_target(ci_gate, command, expected):
    assert ci_gate._gate_target(command) == expected


# --- pass / no-op -----------------------------------------------------------


def test_non_bash_or_empty_passes(ci_gate):
    assert ci_gate.run({}) == 0
    assert ci_gate.run({"tool_input": {}}) == 0


def test_unrelated_command_passes(ci_gate, monkeypatch):
    calls: list = []
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: calls.append((k, r)))
    assert ci_gate.run(_payload("git status")) == 0
    assert ci_gate.run(_payload("echo hello")) == 0
    assert calls == []


def test_green_run_passes(ci_gate, monkeypatch):
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: "a" * 40)
    monkeypatch.setattr(
        ci_gate, "_gh_runs", _gh(0, '[{"status": "completed", "conclusion": "success"}]')
    )
    assert ci_gate.run(_payload("git merge --ff-only abc1234")) == 0


def test_green_run_with_extra_runs_uses_latest(ci_gate, monkeypatch):
    """The newest (first) run decides; an older red run does not block."""
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: "a" * 40)
    monkeypatch.setattr(
        ci_gate,
        "_gh_runs",
        _gh(
            0,
            '[{"status": "completed", "conclusion": "success"},'
            ' {"status": "completed", "conclusion": "failure"}]',
        ),
    )
    assert ci_gate.run(_payload("git merge --ff-only abc1234")) == 0


# --- blocking failures ------------------------------------------------------


def test_red_run_blocks(ci_gate, monkeypatch, capsys):
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: "a" * 40)
    monkeypatch.setattr(
        ci_gate, "_gh_runs", _gh(0, '[{"status": "completed", "conclusion": "failure"}]')
    )
    assert ci_gate.run(_payload("git merge --ff-only abc1234")) == BLOCKING
    err = capsys.readouterr().err
    assert "failure" in err
    assert "fix the red build" in err


def test_in_progress_run_blocks(ci_gate, monkeypatch, capsys):
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: "a" * 40)
    monkeypatch.setattr(
        ci_gate, "_gh_runs", _gh(0, '[{"status": "in_progress", "conclusion": null}]')
    )
    assert ci_gate.run(_payload("scripts/staging.sh up abc1234")) == BLOCKING
    assert "in_progress" in capsys.readouterr().err


def test_no_run_blocks(ci_gate, monkeypatch, capsys):
    """An empty run list (commit not pushed) blocks with a clear message."""
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: "a" * 40)
    monkeypatch.setattr(ci_gate, "_gh_runs", _gh(0, "[]"))
    assert ci_gate.run(_payload("git merge --ff-only abc1234")) == BLOCKING
    err = capsys.readouterr().err
    assert "no GitHub Actions run" in err
    assert "not pushed" in err


# --- non-blocking "cannot verify" -------------------------------------------


def test_gh_missing_is_non_blocking(ci_gate, monkeypatch, capsys):
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: "a" * 40)
    monkeypatch.setattr(ci_gate, "_gh_runs", _gh(None, ""))
    assert ci_gate.run(_payload("git merge --ff-only abc1234")) == NON_BLOCKING
    assert "gh" in capsys.readouterr().err


def test_gh_failure_is_non_blocking(ci_gate, monkeypatch, capsys):
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: "a" * 40)
    monkeypatch.setattr(ci_gate, "_gh_runs", _gh(1, "auth error"))
    assert ci_gate.run(_payload("./scripts/deploy.sh")) == NON_BLOCKING
    assert "failed" in capsys.readouterr().err


def test_gh_bad_json_is_non_blocking(ci_gate, monkeypatch, capsys):
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: "a" * 40)
    monkeypatch.setattr(ci_gate, "_gh_runs", _gh(0, "not json at all"))
    assert ci_gate.run(_payload("git push origin main")) == NON_BLOCKING
    assert "unexpected output" in capsys.readouterr().err


def test_unresolvable_sha_is_non_blocking(ci_gate, monkeypatch, capsys):
    monkeypatch.setattr(ci_gate, "_resolve_sha", lambda k, r: None)
    monkeypatch.setattr(ci_gate, "_gh_runs", lambda sha: pytest.fail("gh must not run"))
    assert ci_gate.run(_payload("git merge --ff-only does-not-exist")) == NON_BLOCKING
    assert "cannot resolve" in capsys.readouterr().err


# --- resolution targets -----------------------------------------------------


def test_push_to_main_checks_head(ci_gate, monkeypatch, capsys):
    """`git push origin main` gates on HEAD, not on a token-derived ref."""
    resolved: dict = {}
    monkeypatch.setattr(ci_gate, "_rev_parse", lambda ref: resolved.setdefault(ref, ref + "x"))
    monkeypatch.setattr(ci_gate, "_gh_runs", _gh(0, '[{"status":"completed","conclusion":"success"}]'))
    assert ci_gate.run(_payload("git push origin main")) == 0
    assert "HEADx" in resolved.values()


def test_deploy_resolves_origin_main_first(ci_gate, monkeypatch):
    order: list = []
    monkeypatch.setattr(
        ci_gate,
        "_rev_parse",
        lambda ref: order.append(ref) or ("ok" if ref == "origin/main" else None),
    )
    monkeypatch.setattr(ci_gate, "_gh_runs", _gh(0, '[{"status":"completed","conclusion":"success"}]'))
    assert ci_gate.run(_payload("./scripts/deploy.sh")) == 0
    assert order == ["origin/main"]
