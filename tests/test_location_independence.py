"""Location Independence guard + repo-scan tests (spec 032).

FR-002 guard behaviour is tested via subprocess because the guard runs at
import time (the env must be set before `import paths`).

FR-006 repo-scan checks walk runtime `*.py` files and fail if a removed
location-dependency pattern is reintroduced.
"""

import ast
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PATH_ENV_VARS = ("JOB_AGENT_DATA_DIR", "JOB_AGENT_OUTPUT_DIR", "JOB_AGENT_REQUIRE_DB")


def _clean_env() -> dict:
    env = dict(os.environ)
    for k in _PATH_ENV_VARS:
        env.pop(k, None)
    return env


def _import_paths(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import paths"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ── FR-002 guard behaviour (SC-003) ─────────────────────────────────────────

def test_require_db_raises_when_missing_and_creates_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = os.path.join(tmp, "does_not_exist")
        env = _clean_env()
        env["JOB_AGENT_DATA_DIR"] = data_dir
        env["JOB_AGENT_REQUIRE_DB"] = "1"
        proc = _import_paths(env)
        assert proc.returncode != 0
        assert "RuntimeError" in proc.stderr
        assert data_dir in proc.stderr
        assert "JOB_AGENT_DATA_DIR" in proc.stderr
        assert "JOB_AGENT_REQUIRE_DB" in proc.stderr
        assert not os.path.exists(data_dir)


def test_require_db_ok_when_db_present():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = os.path.join(tmp, "data")
        os.makedirs(data_dir)
        db_path = os.path.join(data_dir, "jobs.db")
        with open(db_path, "w") as f:
            f.write("")
        env = _clean_env()
        env["JOB_AGENT_DATA_DIR"] = data_dir
        env["JOB_AGENT_REQUIRE_DB"] = "1"
        proc = _import_paths(env)
        assert proc.returncode == 0, proc.stderr


def test_unset_creates_data_dir():
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = os.path.join(tmp, "fresh")
        env = _clean_env()
        env["JOB_AGENT_DATA_DIR"] = data_dir
        proc = _import_paths(env)
        assert proc.returncode == 0, proc.stderr
        assert os.path.isdir(data_dir)


# ── FR-006 repo-scan helpers ────────────────────────────────────────────────

SCAN_SKIP_DIRS = {".venv", ".git", "__pycache__", "tests", "specs"}

# FR-006 allow-list: nothing is deferred anymore — spec 033 (step 1b) converted
# the last CWD-relative `--db` defaults to `paths.DB_PATH`. Keep this empty so
# `test_no_cwd_data_literals` fails on any reintroduction.
# `paths.py` is exempt from (a) by construction (it is the single source of
# truth). `scrape.py:21`, `monitoring_agent.py` ROOT and
# `scripts/duplicate_report.py` ROOT use `__file__` for non-data/non-output
# purposes, so the token-based (a) check does not match them.
B_ALLOW = set()


def _runtime_py_files() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT)


def _docstring_ids(tree: ast.AST) -> set:
    """Ids of Constant str nodes that are module/class/function docstrings."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def _code_str_literals(path: str):
    """Yield (lineno, literal) for string constants in code (docstrings excluded)."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    doc_ids = _docstring_ids(tree)
    for node in ast.walk(tree):
        # ast.walk also descends into JoinedStr.values, so f-string constant parts
        # are yielded here as plain Constant nodes — no separate JoinedStr branch.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in doc_ids:
                continue
            yield node.lineno, node.value


def _collect_str_ids(node: ast.AST, ids: set[int]) -> None:
    """Collect the ids of string Constant nodes inside `node` (incl. f-string parts)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        ids.add(id(node))
    elif isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                ids.add(id(value))


def _os_path_join_str_ids(tree: ast.AST) -> set[int]:
    """ids of string constants that are `os.path.join(...)` filename arguments."""
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # `os.path.join(...)` — func is Attribute(join) on Attribute(path) on Name(os).
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "join"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "path"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "os"
        ):
            for arg in node.args:
                _collect_str_ids(arg, ids)
    return ids


def _py_launch_literals(path: str):
    """Yield (lineno, literal) for bare `name.py` literals that are *not*
    `os.path.join` filename arguments — i.e. the `[sys.executable, "name.py", …]`
    launch form (a filename joined to a directory is not a launch)."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    doc_ids = _docstring_ids(tree)
    join_ids = _os_path_join_str_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in doc_ids or id(node) in join_ids:
                continue
            yield node.lineno, node.value


# ── FR-006 (a) __file__-derived data/output/.env paths ──────────────────────

def test_no_file_derived_data_paths():
    offenders = []
    for path in _runtime_py_files():
        if _rel(path) == "paths.py":
            continue
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if "__file__" not in line:
                    continue
                for tok in ('"data"', "'data'", '"outputs"', "'outputs'", '".env"', "'.env'"):
                    if tok in line:
                        offenders.append(f"{_rel(path)}:{lineno}: {line.strip()}")
                        break
    assert not offenders, "file-derived data/output/.env paths found:\n" + "\n".join(offenders)


# ── FR-006 (b) CWD-relative `data/` literals ────────────────────────────────

def test_no_cwd_data_literals():
    offenders = []
    for path in _runtime_py_files():
        if _rel(path) in B_ALLOW:
            continue
        for lineno, value in _code_str_literals(path):
            if value.startswith("data/"):
                offenders.append(f"{_rel(path)}:{lineno}: {value!r}")
    assert not offenders, "CWD-relative data/ literals found:\n" + "\n".join(offenders)


# ── FR-006 (c) subprocess launches of `*.py` files ─────────────────────────

def test_no_py_subprocess_launches():
    offenders = []
    for path in _runtime_py_files():
        if _rel(path) in B_ALLOW:
            continue
        for lineno, value in _py_launch_literals(path):
            # A bare module filename (no path separator, no whitespace) ending
            # in ".py" is the `[sys.executable, "<name>.py", …]` launch form.
            # `value != ".py"` skips f-string tail fragments like
            # `f"scrapers/ats/{provider}.py"` (yields a lone ".py" Constant).
            if value != ".py" and value.endswith(".py") and not any(c in value for c in " /\\"):
                offenders.append(f"{_rel(path)}:{lineno}: {value!r}")
    assert not offenders, "subprocess launches of .py files found:\n" + "\n".join(offenders)
