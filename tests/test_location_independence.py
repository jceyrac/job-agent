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
        [sys.executable, "-c", "import core.paths"],
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
# `core/paths.py` is exempt from (a) by construction (it is the single source of
# truth for locations; its `__file__` derives PROJECT_ROOT, never a data path).
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


_SUBPROCESS_FUNCS = ("run", "Popen", "call", "check_call", "check_output")


def _subprocess_from_imports(tree: ast.AST) -> set[str]:
    """Bare names bound by `from subprocess import …`. A bare `run`/`Popen`/… is
    only a subprocess call when imported this way — runtime files define their own
    `run` functions (migrate_*, monitoring_agent, cv_agent/cli)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _is_subprocess_func(func: ast.AST, from_imported: set[str]) -> bool:
    """True when `func` is a subprocess call: `subprocess.run`/`Popen`/…, or a
    bare name the file imported from subprocess via from-import."""
    if isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_FUNCS:
        if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            return True
    if isinstance(func, ast.Name) and func.id in _SUBPROCESS_FUNCS:
        return func.id in from_imported
    return False


def _subprocess_py_launches_in_source(source: str):
    """Yield (lineno, literal) for string constants ending in `.py` anywhere inside
    a subprocess call — args, keywords, and nested expressions (`os.path.join`,
    `Path(ROOT) / "x.py"`, f-string parts). Path separators are allowed, so a
    joined filename like `os.path.join(ROOT, "score.py")` is still caught. A lone
    `".py"` is skipped: it is only ever an f-string tail fragment, and the full
    filename is what matters (an f-string like `f"{dir}/score.py"` still yields
    the `"/score.py"` constant, which is flagged)."""
    tree = ast.parse(source)
    from_imported = _subprocess_from_imports(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_subprocess_func(node.func, from_imported):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if child.value != ".py" and child.value.endswith(".py"):
                    yield child.lineno, child.value


def _subprocess_py_launches(path: str):
    with open(path, encoding="utf-8") as f:
        source = f.read()
    yield from _subprocess_py_launches_in_source(source)


# ── FR-006 (a) __file__-derived data/output/.env paths ──────────────────────

def test_no_file_derived_data_paths():
    offenders = []
    for path in _runtime_py_files():
        if _rel(path) == os.path.join("core", "paths.py"):
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
        for lineno, value in _subprocess_py_launches(path):
            offenders.append(f"{_rel(path)}:{lineno}: {value!r}")
    assert not offenders, "subprocess launches of .py files found:\n" + "\n".join(offenders)


def test_no_py_subprocess_launch_negative_bare():
    src = 'import subprocess, sys\nsubprocess.run([sys.executable, "score.py"])\n'
    assert list(_subprocess_py_launches_in_source(src)) == [(2, "score.py")]


def test_no_py_subprocess_launch_negative_os_path_join():
    src = (
        'import os, subprocess, sys\n'
        'subprocess.run([sys.executable, os.path.join(ROOT, "score.py")])\n'
    )
    assert list(_subprocess_py_launches_in_source(src)) == [(2, "score.py")]


def test_no_py_subprocess_launch_negative_pathlib_div():
    src = (
        'import subprocess, sys\n'
        'from pathlib import Path\n'
        'subprocess.run([sys.executable, Path(ROOT) / "score.py"])\n'
    )
    assert list(_subprocess_py_launches_in_source(src)) == [(3, "score.py")]


def test_no_py_subprocess_launch_outside_subprocess_passes():
    src = 'import os\nos.path.join(DIR, "greenhouse.py")\n'
    assert list(_subprocess_py_launches_in_source(src)) == []


def test_no_py_subprocess_launch_bare_run_via_from_import():
    src = 'from subprocess import run\nrun(["python", "score.py"])\n'
    assert list(_subprocess_py_launches_in_source(src)) == [(2, "score.py")]


def test_no_py_subprocess_launch_local_run_not_subprocess():
    src = 'def run(p):\n    pass\nrun(os.path.join(DIR, "migrate.py"))\n'
    assert list(_subprocess_py_launches_in_source(src)) == []
