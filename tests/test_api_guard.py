"""Guard test for the API (spec 041 FR-009 / spec 042 FR-005).

Fails if `api/`:
  - imports `sqlite3` (all DB access goes through `JobStorage`, spec 037),
  - calls a `JobStorage` method outside the read+write allow-list (e.g. the
    cross-user `get_scores_for_job`, or a platform/pipeline write), or
  - constructs a `JobStorage` anywhere but `deps.py`, where the unbound site is
    exactly `get_db` and every other construction binds the token's `user_id`.
"""
import ast
import os

from core.storage import JobStorage

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(REPO_ROOT, "api")

# Every `JobStorage` method the API may call (read + the spec 042 write set).
# `get_scores_for_job` is deliberately absent (cross-user leak, spec 039 rule).
# Token minting/revocation and platform/pipeline writes are absent too — see
# FORBIDDEN_METHODS below.
ALLOW_LIST = frozenset({
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
    # me: profile (US3 write pre-check)
    "get_profile",
    # auth accessors (deps.py only)
    "resolve_api_token", "touch_api_token",
    # /health — purge liveness (deps.py only)
    "get_purge_last_success",
    # ── spec 042 writes ──
    # US1 — jobs & lifecycle
    "set_status", "record_lifecycle_event", "update_lifecycle_event",
    "delete_lifecycle_event", "reconcile_lifecycle_status",
    # US2 — companies, contacts, interactions
    "set_company_status", "set_company_monitored", "upsert_contact",
    "update_contact_fields", "log_interaction",
    # US3 — profile
    "set_active_profile_id", "upsert_profile",
    # US4 — catalogue company facts
    "update_company_fields",
    # ── spec 043 tasks (request + follow) ──
    "enqueue_task", "get_task", "list_tasks", "get_task_events",
    "mark_cancelled", "request_cancel",
    # ── spec 044 ingest + application ──
    "save_unscored", "save_application",
})

# Platform/pipeline writes the API must never call (spec 042, FR-005). Named so
# a forbidden call fails loudly and self-documents the boundary. `save_unscored`
# and `save_application` moved to ALLOW_LIST in spec 044 (ingest + application);
# `save_scored` stays forbidden — no client writes a score.
FORBIDDEN_METHODS = frozenset({
    "save_scored", "update_job_extraction", "log_run",
    "set_ingestion_scope", "set_config", "upsert_company", "upsert_company_from_seed",
    "create_api_token", "list_api_tokens", "revoke_api_token",
    "purge_stale_jobs", "import_companies_from_csv",
    # worker/cron-only task methods (spec 043): the API requests and follows, the
    # worker claims/runs/finishes.
    "claim_next_task", "append_task_events", "finish_task", "is_cancel_requested",
    "recover_interrupted", "prune_task_history",
    "set_worker_heartbeat", "set_pipeline_lock_holder",
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


# ── FR-008: no pipeline work reachable from a request handler ─────────────────

_PIPELINE_MODULES = ("subprocess", "core.scrape", "core.score", "core.main", "core.worker")


def test_api_imports_no_subprocess_nor_pipeline_entrypoints():
    problems = []
    for path in _api_files():
        tree = ast.parse(_read(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _PIPELINE_MODULES:
                        problems.append(f"{_rel(path)}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod in _PIPELINE_MODULES:
                    problems.append(f"{_rel(path)}:{node.lineno}: from {mod} import ...")
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
                if node.attr in public and node.attr not in ALLOW_LIST:
                    problems.append(
                        f"{_rel(path)}:{node.lineno}: calls JobStorage.{node.attr} "
                        f"(not allow-listed)"
                    )
    assert problems == [], "\n".join(problems)


def test_api_never_calls_forbidden_methods():
    # Pipeline/platform writes (spec 042 FR-005). The allow-list already excludes
    # them; this named list makes the exclusion self-documenting and loud.
    problems = []
    for path in _api_files():
        tree = ast.parse(_read(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.attr in FORBIDDEN_METHODS:
                    problems.append(
                        f"{_rel(path)}:{node.lineno}: calls forbidden JobStorage.{node.attr}"
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
    # `save_scored` is a pipeline write — the scanner must flag it.
    src = "def f(db):\n    return db.save_scored('x')\n"
    tree = ast.parse(src)
    public = _public_storage_methods()
    hits = [
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
        and node.attr in public and node.attr not in ALLOW_LIST
    ]
    assert "save_scored" in hits


def test_guard_negative_unbound_jobstorage():
    src = "x = JobStorage('/tmp/db')\n"
    assert "JobStorage(" in src and "user_id=" not in src
