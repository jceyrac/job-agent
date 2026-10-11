"""Guard test for spec 037 (Single DB Access Path, roadmap step 2a) — FR-005.

Fails when raw SQLite access reappears in runtime code outside `core/storage.py`:

- `import sqlite3` / `from sqlite3 import ...`
- `sqlite3.connect(...)`
- `_conn` attribute access on any object that is not `self`

Runtime = `core/` (minus `core/storage.py`), `tracker_views/`, `tracker.py`.
`scripts/` and `tests/` are out of scope (dev/diagnostic tooling — research.md D1).
"""

import ast
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The one file allowed to own the DB connection.
_ALLOWED_DB_FILE = os.path.join("core", "storage.py")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT)


def _runtime_files():
    for sub in ("core", "tracker_views"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, sub)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in sorted(filenames):
                if name.endswith(".py"):
                    yield os.path.join(dirpath, name)
    yield os.path.join(REPO_ROOT, "tracker.py")


def _db_violations(source: str) -> list[str]:
    """Return a human-readable violation per raw-SQL form found in `source`."""
    tree = ast.parse(source)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3" or alias.name.startswith("sqlite3."):
                    problems.append(f"line {node.lineno}: import sqlite3")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("sqlite3"):
                problems.append(f"line {node.lineno}: from sqlite3 import ...")
        elif isinstance(node, ast.Attribute) and node.attr == "connect":
            if isinstance(node.value, ast.Name) and node.value.id == "sqlite3":
                problems.append(f"line {node.lineno}: sqlite3.connect")
        elif isinstance(node, ast.Attribute) and node.attr == "_conn":
            if not (isinstance(node.value, ast.Name) and node.value.id == "self"):
                problems.append(f"line {node.lineno}: ._conn() on non-self object")
    return problems


# ---------------------------------------------------------------------------
# FR-005 — no raw SQL outside core/storage.py
# ---------------------------------------------------------------------------

def test_fr005_no_raw_sql_outside_storage():
    problems = []
    for path in _runtime_files():
        rel = _rel(path)
        if rel == _ALLOWED_DB_FILE:
            continue
        for msg in _db_violations(_read(path)):
            problems.append(f"{rel}: {msg}")
    assert problems == [], "\n".join(problems)


def test_fr005_negative_import():
    src = "import sqlite3\nconn = sqlite3.connect(':memory:')\n"
    v = _db_violations(src)
    assert any("import sqlite3" in x for x in v)


def test_fr005_negative_connect():
    src = "import sqlite3\nconn = sqlite3.connect('x.db')\n"
    assert any("sqlite3.connect" in x for x in _db_violations(src))


def test_fr005_negative_from_import():
    src = "from sqlite3 import connect\n"
    assert any("from sqlite3 import" in x for x in _db_violations(src))


def test_fr005_negative_conn_on_non_self():
    src = "db._conn()\n"
    assert any("_conn" in x for x in _db_violations(src))


def test_fr005_self_conn_allowed():
    # `self._conn()` is the private method itself (only lives in storage.py).
    src = "class X:\n    def f(self):\n        with self._conn() as c:\n            pass\n"
    assert _db_violations(src) == []
