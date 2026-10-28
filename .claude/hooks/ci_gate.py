#!/usr/bin/env python
"""PreToolUse hook — block staging/merge/deploy of a SHA whose CI is not green.

Claude Code invokes this before every `Bash` command with a JSON object on
stdin (`{"tool_input": {"command": str}}`). It recognises the four release
commands — `git merge <sha>`, `git push … main`, `scripts/staging.sh up <ref>`,
`./scripts/deploy.sh` — resolves the target commit, and asks GitHub Actions
(`gh run list --commit <sha> --json status,conclusion`) whether the latest run
for that exact commit is completed + successful. If it is, the command may
proceed; if the run is red, still in progress, or absent (commit not pushed),
the command is blocked. Pure stdlib.

Exit codes (`contracts/hooks.md`): 0 = pass, 2 = blocking failure (stderr shown
to Claude), any other non-zero = non-blocking failure.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

BLOCKING = 2
NON_BLOCKING = 3

# Tokens that end a simple command inside a compound shell command.
_SHELL_BREAKS = {"&&", "||", ";", "|", "&", "2>", "1>"}
# `git` options that consume the following token as a value.
_GIT_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--exec-path"}

_LABELS = {
    "staging": "staging",
    "merge": "merge",
    "deploy": "deploy",
    "push_main": "push to main",
}


def _tokens(command: str) -> list[str]:
    """Split a shell command into tokens (quoting-aware, falls back to words)."""
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _segments(tokens: list[str]) -> list[list[str]]:
    """Split tokens into simple commands on shell control operators."""
    segments, current = [], []
    for token in tokens:
        if token in _SHELL_BREAKS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _is_main_ref(token: str) -> bool:
    return (
        token == "main"
        or token.endswith(":main")
        or token.endswith("/main")
    )


def _git_subcommand(seg: list[str]) -> str | None:
    """The subcommand of a `git …` segment, skipping git-level options."""
    i = 1
    while i < len(seg):
        token = seg[i]
        if token in _GIT_VALUE_FLAGS:
            i += 2
            continue
        if token.startswith("-"):
            i += 1
            continue
        return token
    return None


def _classify(seg: list[str]) -> tuple[str, str | None] | None:
    """Classify one simple command; return `(kind, ref)` or None if not gated."""
    if not seg:
        return None

    head = seg[0]
    if head.endswith("staging.sh"):
        if len(seg) >= 3 and seg[1] == "up":
            return ("staging", seg[2])
        return None
    if head.endswith("deploy.sh"):
        return ("deploy", None)

    if head == "git":
        sub = _git_subcommand(seg)
        if sub == "merge":
            for candidate in reversed(seg[2:]):
                if not candidate.startswith("-"):
                    return ("merge", candidate)
            return None  # e.g. `git merge --abort` — no commit to promote
        if sub == "push":
            for ref in seg[2:]:
                if _is_main_ref(ref):
                    return ("push_main", None)
            return None
    return None


def _gate_target(command: str) -> tuple[str, str | None] | None:
    """Return the first gated `(kind, ref)` in the command, else None."""
    for seg in _segments(_tokens(command)):
        target = _classify(seg)
        if target is not None:
            return target
    return None


def _rev_parse(ref: str) -> str | None:
    """Resolve `ref` to a full 40-char commit SHA, or None."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out else None


def _resolve_sha(kind: str, ref: str | None) -> str | None:
    """Resolve the target SHA for a gated command."""
    if kind == "deploy":
        candidates = ["origin/main", "main", "HEAD"]
    elif kind == "push_main":
        candidates = ["HEAD"]
    else:  # staging / merge carry an explicit ref
        candidates = [ref] if ref else []
    for candidate in candidates:
        sha = _rev_parse(candidate)
        if sha:
            return sha
    return None


def _gh_runs(sha: str) -> tuple[int | None, str]:
    """Run `gh run list` for a commit; `(None, "")` means gh is not installed."""
    try:
        proc = subprocess.run(
            ["gh", "run", "list", "--commit", sha, "--json", "status,conclusion"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return (None, "")
    return (proc.returncode, proc.stdout or "")


def _short(sha: str) -> str:
    return sha[:8]


def run(payload: dict) -> int:
    """Run the CI-gate logic; return the process exit code."""
    command = ((payload.get("tool_input") or {}).get("command") or "").strip()
    if not command:
        return 0

    target = _gate_target(command)
    if target is None:
        return 0

    kind, ref = target
    label = _LABELS.get(kind, "this")
    sha = _resolve_sha(kind, ref)
    if sha is None:
        shown = ref or ("origin/main/HEAD" if kind == "deploy" else "HEAD")
        print(
            f"ci_gate: cannot resolve the {label} target commit '{shown}' — "
            "skipping the CI check (non-blocking)",
            file=sys.stderr,
        )
        return NON_BLOCKING

    code, output = _gh_runs(sha)
    if code is None:
        print(
            "ci_gate: `gh` not found — cannot verify CI status; "
            "continuing (non-blocking)",
            file=sys.stderr,
        )
        return NON_BLOCKING
    if code != 0:
        print(
            f"ci_gate: `gh run list` failed (exit {code}) — cannot verify CI "
            "status; continuing (non-blocking)",
            file=sys.stderr,
        )
        return NON_BLOCKING

    try:
        runs = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        print(
            "ci_gate: `gh run list` returned unexpected output — cannot verify "
            "CI status; continuing (non-blocking)",
            file=sys.stderr,
        )
        return NON_BLOCKING
    if not isinstance(runs, list):
        print(
            "ci_gate: `gh run list` returned unexpected output — cannot verify "
            "CI status; continuing (non-blocking)",
            file=sys.stderr,
        )
        return NON_BLOCKING

    if not runs:
        print(
            f"ci_gate: no GitHub Actions run for {_short(sha)} — the commit was "
            f"not pushed (or CI did not trigger). Push it and wait for a green "
            f"run before you {label}.",
            file=sys.stderr,
        )
        return BLOCKING

    latest = runs[0] if isinstance(runs[0], dict) else {}
    status = latest.get("status")
    conclusion = latest.get("conclusion")
    if status != "completed":
        print(
            f"ci_gate: the CI run for {_short(sha)} is still "
            f"{status or 'running'} — wait for it to complete before you "
            f"{label}.",
            file=sys.stderr,
        )
        return BLOCKING
    if conclusion != "success":
        print(
            f"ci_gate: the CI run for {_short(sha)} is "
            f"{conclusion or 'not successful'} (not success) — fix the red "
            f"build before you {label}.",
            file=sys.stderr,
        )
        return BLOCKING
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    return run(payload if isinstance(payload, dict) else {})


if __name__ == "__main__":
    sys.exit(main())
