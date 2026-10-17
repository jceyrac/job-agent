"""Guard test for the tracker (spec 043 FR-010 / US4).

Fails if any module under `tracker_views/` imports `subprocess` or `threading`.
Long-running work is requested through the task queue and run by the worker
(spec 043); the tracker must not launch a subprocess or helper thread itself —
that is the constitution's resolved "subprocess lancés par le tracker" violation.
"""
import ast
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACKER_VIEWS_DIR = os.path.join(REPO_ROOT, "tracker_views")

# Imports that would re-introduce the violation (spec 043, FR-010).
_FORBIDDEN_MODULES = ("subprocess", "threading")


def _tracker_files():
    for dirpath, dirnames, filenames in os.walk(TRACKER_VIEWS_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_tracker_views_import_no_subprocess_nor_threading():
    problems = []
    for path in _tracker_files():
        tree = ast.parse(_read(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _FORBIDDEN_MODULES:
                        problems.append(f"{_rel(path)}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "") in _FORBIDDEN_MODULES:
                    problems.append(f"{_rel(path)}:{node.lineno}: from {node.module} import ...")
    assert problems == [], "\n".join(problems)
