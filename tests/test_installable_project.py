"""Installable-project guard tests (spec 033, FR-007).

(a) no import-path manipulation survives in any runtime ``*.py`` — the editable
install is the single import mechanism. ``scripts/backup_db.py`` stays a
standalone-file exception (``deploy.sh`` runs it by path inside the container,
where no editable install is active) and ``specs/``/``tests/`` are out of scope.

(b) every root ``*.py`` module is declared in ``pyproject.toml``
``[tool.setuptools] py-modules``, so ``pip install -e .`` exposes exactly the
runtime surface and nothing is forgotten or accidentally published.
"""

import ast
import os
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# FR-007(a): the scan skips vendored/build trees and the allow-listed `specs/`
# documentation tree, plus the standalone backup script. `tests/` is scanned too —
# the AST check matches only a `sys.path` attribute expression, so it cannot
# self-flag on its own string literals.
SCAN_SKIP_DIRS = {".venv", ".git", "__pycache__", "specs"}
SYS_PATH_ALLOW = {"scripts/backup_db.py"}

# FR-007(b): the only root module legitimately excluded from py-modules is the
# gitignored personal-data filler, which must never be packaged.
ROOT_MODULE_ALLOW = {"fill_orp_pdf"}


def _runtime_py_files():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT)


def test_no_import_path_manipulation():
    offenders = []
    parse_errors = []
    for path in _runtime_py_files():
        if _rel(path) in SYS_PATH_ALLOW:
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            parse_errors.append(f"{_rel(path)}:{e.lineno}: {e.msg}")
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
                and node.attr == "path"
            ):
                offenders.append(f"{_rel(path)}:{node.lineno}")
                break
    assert not offenders, "import-path manipulation found:\n" + "\n".join(offenders)
    assert not parse_errors, "files that fail to parse (must be valid Python):\n" + "\n".join(parse_errors)


def _declared_py_modules() -> set:
    with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as f:
        data = tomllib.load(f)
    return set(data["tool"]["setuptools"]["py-modules"])


def test_root_modules_declared_in_pyproject():
    declared = _declared_py_modules()
    missing = []
    for fn in sorted(os.listdir(REPO_ROOT)):
        if not fn.endswith(".py"):
            continue
        name = fn[:-3]
        if name in ROOT_MODULE_ALLOW:
            continue
        if name not in declared:
            missing.append(fn)
    assert not missing, "root modules missing from pyproject.toml py-modules:\n" + "\n".join(missing)
