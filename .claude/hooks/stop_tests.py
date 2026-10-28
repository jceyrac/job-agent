#!/usr/bin/env python
"""Stop hook — run the test suite when a turn changed Python code (spec 044b).

Claude Code invokes this on `Stop` with a JSON object on stdin
(`{"stop_hook_active": bool}`). It runs the full suite when the working tree
has modified or untracked `.py` files, and blocks once on red. Pure stdlib.

Exit codes (`contracts/hooks.md`): 0 = pass, 2 = blocking failure (Claude
continues to fix), any other non-zero = non-blocking failure.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
VENV_PYTHON = os.path.join(PROJECT_DIR, ".venv", "bin", "python")
STRIPPED_KEYS = ("LLM_API_KEY", "DEEPSEEK_API_KEY")
PYTEST_CMD = ["-m", "pytest", "tests/", "-q", "-x", "--no-header", "-p", "no:cacheprovider"]
BUDGET_SECONDS = 60.0
SUMMARY_TAIL = 60

BLOCKING = 2
NON_BLOCKING = 3


def _git_status() -> str:
    """Return `git status --porcelain` output; '' if git is unavailable."""
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )
    return proc.stdout if proc.returncode == 0 else ""


def _py_changed(status: str) -> bool:
    """True if the porcelain output mentions a modified/untracked `.py` file."""
    for line in status.splitlines():
        # porcelain: "XY path", "R  old -> new" (renames); path is field 3.
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path.endswith(".py"):
            return True
    return False


def _stripped_env() -> dict[str, str]:
    """A copy of the environment with the LLM keys removed (offline spend guard)."""
    env = os.environ.copy()
    for key in STRIPPED_KEYS:
        env.pop(key, None)
    return env


def _run_pytest() -> tuple[int, str]:
    """Run the suite; return (returncode, combined stdout+stderr)."""
    proc = subprocess.run(
        [VENV_PYTHON, *PYTEST_CMD],
        cwd=PROJECT_DIR,
        env=_stripped_env(),
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _summarize(output: str) -> str:
    """Bound the failure output to the last non-blank lines."""
    lines = [ln for ln in output.splitlines() if ln.strip()]
    return "\n".join(lines[-SUMMARY_TAIL:])


def run(payload: dict) -> int:
    """Run the Stop-hook logic; return the process exit code."""
    if not _py_changed(_git_status()):
        return 0

    if not os.path.exists(VENV_PYTHON):
        print(
            f"stop_tests: venv interpreter not found at {VENV_PYTHON} — "
            "cannot run the test suite",
            file=sys.stderr,
        )
        return NON_BLOCKING

    start = time.monotonic()
    try:
        code, output = _run_pytest()
    except FileNotFoundError:
        print(
            "stop_tests: pytest interpreter missing — cannot run the test suite",
            file=sys.stderr,
        )
        return NON_BLOCKING
    elapsed = time.monotonic() - start

    if elapsed > BUDGET_SECONDS:
        print(
            f"⚠ suite exceeded {BUDGET_SECONDS:.0f}s budget — consider a subset spec",
            file=sys.stderr,
        )

    if code == 0:
        return 0

    summary = _summarize(output)
    if payload.get("stop_hook_active"):
        print(
            "suite still red after one forced attempt — review needed",
            file=sys.stderr,
        )
        print(summary, file=sys.stderr)
        return NON_BLOCKING
    print(summary, file=sys.stderr)
    return BLOCKING


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    return run(payload if isinstance(payload, dict) else {})


if __name__ == "__main__":
    sys.exit(main())
