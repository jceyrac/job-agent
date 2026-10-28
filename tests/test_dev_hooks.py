"""Tests for the dev-quality-gate hook scripts (spec 044b, FR-004).

The two hook scripts live in `.claude/hooks/` and are pure-stdlib executables
driven by a single JSON object on stdin. These tests load each script as a
module and drive its `run(payload)` function with the subprocess side-effects
mocked — they never run the real test suite recursively, never invoke a real
git/pytest, and never reach the network.

Exit-code contract (`contracts/hooks.md`): 0 = pass, 2 = blocking failure,
any other non-zero = non-blocking failure.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"

NON_BLOCKING = 3  # any non-zero exit that is not 2 — must not loop/block


def _load_hook(name: str):
    """Import `.claude/hooks/<name>` as a module, executing only its defs."""
    path = HOOKS_DIR / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stop_tests(monkeypatch):
    mod = _load_hook("stop_tests.py")
    # Point the venv interpreter at the running interpreter so the
    # `os.path.exists` gate passes deterministically; subprocess is mocked
    # in every test, so nothing is actually executed.
    monkeypatch.setattr(mod, "VENV_PYTHON", sys.executable)
    return mod


# --- User Story 1: the Stop hook (`stop_tests.py`) ---------------------------


def test_stop_no_py_change_skips_pytest(stop_tests, monkeypatch):
    """A doc-only change (no modified/untracked `.py`) exits 0, no pytest."""
    ran: list = []
    monkeypatch.setattr(
        stop_tests, "_git_status", lambda: " M README.md\n?? notes.txt\n"
    )
    monkeypatch.setattr(
        stop_tests, "_run_pytest", lambda: ran.append("pytest") or (0, "")
    )
    assert stop_tests.run({"stop_hook_active": False}) == 0
    assert ran == []


def test_stop_red_suite_blocks_once(stop_tests, monkeypatch, capsys):
    """Red suite + `stop_hook_active=false` → exit 2 with the failure summary."""
    monkeypatch.setattr(stop_tests, "_git_status", lambda: " M core/foo.py\n")
    monkeypatch.setattr(
        stop_tests,
        "_run_pytest",
        lambda: (1, "FAILED tests/test_a.py::test_x - assert 1 == 2\n"),
    )
    assert stop_tests.run({"stop_hook_active": False}) == 2
    assert "assert 1 == 2" in capsys.readouterr().err


def test_stop_red_after_forced_attempt_is_non_blocking(stop_tests, monkeypatch, capsys):
    """Red suite + `stop_hook_active=true` → non-blocking, no infinite loop."""
    monkeypatch.setattr(stop_tests, "_git_status", lambda: " M core/foo.py\n")
    monkeypatch.setattr(
        stop_tests, "_run_pytest", lambda: (1, "FAILED tests/test_a.py::test_x\n")
    )
    assert stop_tests.run({"stop_hook_active": True}) == NON_BLOCKING
    assert "still red after one forced attempt" in capsys.readouterr().err


def test_stop_venv_missing_is_non_blocking(stop_tests, monkeypatch, capsys):
    """A missing venv interpreter → non-blocking error, never a silent pass."""
    monkeypatch.setattr(stop_tests, "_git_status", lambda: " M core/foo.py\n")
    monkeypatch.setattr(
        stop_tests, "VENV_PYTHON", "/nonexistent/.venv/bin/python"
    )
    assert stop_tests.run({"stop_hook_active": False}) == NON_BLOCKING
    assert "cannot run the test suite" in capsys.readouterr().err


def test_stop_pytest_missing_is_non_blocking(stop_tests, monkeypatch, capsys):
    """pytest failing to launch (FileNotFoundError) → non-blocking error."""
    monkeypatch.setattr(stop_tests, "_git_status", lambda: " M core/foo.py\n")

    def _boom():
        raise FileNotFoundError("python")

    monkeypatch.setattr(stop_tests, "_run_pytest", _boom)
    assert stop_tests.run({"stop_hook_active": False}) == NON_BLOCKING
    assert "missing" in capsys.readouterr().err


def test_stop_summary_is_bounded(stop_tests, monkeypatch, capsys):
    """A long failure output is truncated to a bounded tail."""
    monkeypatch.setattr(stop_tests, "_git_status", lambda: " M core/foo.py\n")
    long_output = "\n".join(f"line {i}" for i in range(200))
    monkeypatch.setattr(stop_tests, "_run_pytest", lambda: (1, long_output))
    assert stop_tests.run({"stop_hook_active": False}) == 2
    err = capsys.readouterr().err
    assert "line 0" not in err
    assert "line 199" in err
    assert len(err.splitlines()) <= stop_tests.SUMMARY_TAIL


def test_stripped_env_removes_llm_keys(stop_tests, monkeypatch):
    """`LLM_API_KEY`/`DEEPSEEK_API_KEY` are removed; other vars are kept."""
    monkeypatch.setenv("LLM_API_KEY", "sk-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.setenv("KEEP_ME", "yes")
    env = stop_tests._stripped_env()
    assert "LLM_API_KEY" not in env
    assert "DEEPSEEK_API_KEY" not in env
    assert env["KEEP_ME"] == "yes"


def test_pytest_runs_with_stripped_env(stop_tests, monkeypatch):
    """The pytest subprocess is launched with the LLM keys stripped from env."""
    monkeypatch.setenv("LLM_API_KEY", "sk-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(stop_tests.subprocess, "run", fake_run)
    code, _ = stop_tests._run_pytest()
    assert code == 0
    assert captured["env"] is not None
    assert "LLM_API_KEY" not in captured["env"]
    assert "DEEPSEEK_API_KEY" not in captured["env"]


def test_stop_budget_warning_is_non_blocking(stop_tests, monkeypatch, capsys):
    """A pass that exceeds the 60 s budget warns (stderr) but still exits 0."""
    monkeypatch.setattr(stop_tests, "_git_status", lambda: " M core/foo.py\n")
    monkeypatch.setattr(stop_tests, "_run_pytest", lambda: (0, ""))
    ticks = iter([0.0, 61.0])
    monkeypatch.setattr(stop_tests.time, "monotonic", lambda: next(ticks))
    assert stop_tests.run({"stop_hook_active": False}) == 0
    assert "budget" in capsys.readouterr().err
