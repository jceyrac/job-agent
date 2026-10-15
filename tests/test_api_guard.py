"""Guard test for the read-only API (spec 041, FR-009).

Fails if `api/`:
  - imports `sqlite3` (all DB access goes through `JobStorage`, spec 037),
  - calls a `JobStorage` method outside the read allow-list (e.g. the
    cross-user `get_scores_for_job`, or any writer), or
  - constructs a `JobStorage` anywhere but `deps.py`, where the unbound site is
    exactly `get_db` and every other construction binds the token's `user_id`.
"""
import ast
import os

from core.storage import JobStorage

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(REPO_ROOT, "api")

# Every `JobStorage` method the API may call. `get_scores_for_job` is
# deliberately absent (cross-user leak, spec 039 rule). Writers/CLI-only
# accessors (`create_api_token`, `list_api_tokens`, `revoke_api_token`, …) are
# absent too — the API never mints or revokes tokens in v1.
READ_ALLOW_LIST = frozenset({
    # catalogue
    "get_catalogue_jobs", "get_catalogue_job", "get_company_facts",
    # me: identity / feed
    "get_user", "get_active_profile_id", "get_all_profiles", "get_all_for_tracker",
    # me: job detail
    "get_job_for_prepare", "get_user_scores_for_job", "get_lifecycle_summary",
    "get_lifecycle_events", "get_application",
    # me: stats / dashboard / applications
    "get_stats", "get_dashboard_data", "get_all_applications",
    # me: companies
    "get_companies", "get_company_by_id", "get_company_relationship_summary",
    "get_company_contacts", "get_company_interactions",
    # me: contacts
    "get_all_contacts", "get_contact", "get_contact_interactions",
    # auth accessors (deps.py only)
    "resolve_api_token", "touch_api_token",
})

_AUTH_ACCESSORS = frozenset({"resolve_api_token", "touch_api_token"})


def _api_files():
    for dirpath, dirnames, filenames in os.walk(API_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _rel(path: str) -> str:
    return os.path.relpath(path, REPO_ROOT)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _public_storage_methods() -> set[str]:
    return {m for m in dir(JobStorage) if not m.startswith("_") and callable(getattr(JobStorage, m))}


# ── FR-009: no sqlite3 import ────────────────────────────────────────────────

def test_api_imports_no_sqlite3():
    problems = []
    for path in _api_files():
        tree = ast.parse(_read(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sqlite3" or alias.name.startswith("sqlite3."):
                        problems.append(f"{_rel(path)}:{node.lineno}: import sqlite3")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").startswith("sqlite3"):
                    problems.append(f"{_rel(path)}:{node.lineno}: from sqlite3 import ...")
    assert problems == [], "\n".join(problems)


# ── FR-009: only allow-listed read methods ───────────────────────────────────

def test_api_calls_only_allowlisted_methods():
    public = _public_storage_methods()
    problems = []
    for path in _api_files():
        tree = ast.parse(_read(path))
        for node in ast.walk(tree):
            # `db.<method>` (the storage variable is a plain name in routers/deps).
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.attr in public and node.attr not in READ_ALLOW_LIST:
                    problems.append(
                        f"{_rel(path)}:{node.lineno}: calls JobStorage.{node.attr} "
                        f"(not allow-listed)"
                    )
    assert problems == [], "\n".join(problems)


def test_api_never_calls_get_scores_for_job():
    # The cross-user leak (spec 039). The allow-list already excludes it; this is
    # a named, self-documenting assertion.
    for path in _api_files():
        assert "get_scores_for_job" not in _read(path), (
            f"{_rel(path)} calls the cross-user get_scores_for_job"
        )


# ── FR-009: JobStorage built only in deps.py, bound to the token user ───────

def test_jobstorage_built_only_in_deps():
    problems = []
    for path in _api_files():
        rel = _rel(path)
        src = _read(path)
        for i, line in enumerate(src.splitlines(), start=1):
            if "JobStorage(" in line:
                if rel != "api/deps.py":
                    problems.append(f"{rel}:{i}: JobStorage() built outside deps.py")
                    continue
                # Within deps.py, an unbound construction is allowed only in get_db.
                if "user_id=" not in line:
                    if "def get_db" not in src[: src.find(line)]:
                        problems.append(f"{rel}:{i}: unbound JobStorage() outside get_db")
    assert problems == [], "\n".join(problems)


# ── negative self-tests (the scanner must actually fire) ─────────────────────

def test_guard_negative_disallowed_method():
    src = "def f(db):\n    return db.set_status('x', 'applied')\n"
    tree = ast.parse(src)
    public = _public_storage_methods()
    hits = [
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        and node.attr in public and node.attr not in READ_ALLOW_LIST
    ]
    assert "set_status" in hits


def test_guard_negative_unbound_jobstorage():
    src = "x = JobStorage('/tmp/db')\n"
    assert "JobStorage(" in src and "user_id=" not in src
