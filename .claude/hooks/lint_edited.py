#!/usr/bin/env python
"""PostToolUse hook — lint an edited `.py` file with ruff (spec 044b).

Claude Code invokes this after `Write`/`Edit` with a JSON object on stdin
(`{"tool_input": {"file_path": str}}`). Non-`.py` and out-of-project paths are
ignored; a ruff finding exits 2 so the error is fed back to Claude in the same
step. Pure stdlib.

Exit codes (`contracts/hooks.md`): 0 = pass, 2 = blocking failure (stderr shown
to Claude), any other non-zero = non-blocking failure.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

BLOCKING = 2
NON_BLOCKING = 3


def _is_in_project(path: str) -> bool:
    """True if `path` resolves under the project directory."""
    try:
        root = os.path.realpath(PROJECT_DIR)
        return os.path.realpath(path).startswith(root + os.sep)
    except OSError:
        return False


def _ruff_available() -> bool:
    """True if the `ruff` package is importable (dev extra installed)."""
    return importlib.util.find_spec("ruff") is not None


def _run_ruff(path: str) -> tuple[int, str]:
    """Run `ruff check <path>`; return (returncode, combined stdout+stderr)."""
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", path],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run(payload: dict) -> int:
    """Run the lint hook; return the process exit code."""
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path")
    if not path or not path.endswith(".py") or not _is_in_project(path):
        return 0

    if not _ruff_available():
        print(
            "lint_edited: ruff not installed — run `pip install -e '.[dev]'`",
            file=sys.stderr,
        )
        return NON_BLOCKING

    code, output = _run_ruff(path)
    if code == 0:
        return 0
    print(output, file=sys.stderr)
    return BLOCKING


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    return run(payload if isinstance(payload, dict) else {})


if __name__ == "__main__":
    sys.exit(main())
