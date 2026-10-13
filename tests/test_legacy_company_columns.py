"""
tests/test_legacy_company_columns.py — guard against legacy user-level columns on companies.

After the user-space split (spec 039), ``companies.status`` / ``companies.notes`` /
``companies.monitored`` are dead weight (expand/contract — dropped at step 10). The
user's relationship now lives on ``user_companies`` and the platform capability on
``companies.monitoring_status``, so no *non-migration* SQL may read or write the
legacy columns.

This suite:
  * scans every ``JobStorage`` method body and fails on any non-migration reference
    to ``companies.<col>``, the ``c.<col>`` alias (``c`` is always the companies
    alias), or a bare ``<col>`` in a companies INSERT/UPDATE;
  * proves the scanner itself catches each form (negative fixtures), and ignores the
    legitimate ``user_companies`` / ``monitoring_status`` forms.
"""
import inspect
import re

from core.storage import JobStorage

# Explicit allow-list (by function name): migration code legitimately reads/writes
# the legacy columns while backfilling, and is recorded in `migrations`.
_MIGRATION_FUNCTIONS = frozenset({
    "_init_db",
    "_record_migration",
    "_migrate_application_lifecycle",
    "_migrate_user_space_separation",
    "_ensure_default_user",
    "_rekey_job_tracking",
    "_rekey_job_applications",
    "_add_user_id_column",
    "_backfill_user_companies",
    "_revalue_monitoring_status",
    "_rebuild_per_user_indexes",
    "_migrate_monitored_companies",
    "_migrate_company_researcher",
    "_seed_greenhouse_boards",
})

# A standalone legacy-column token — not part of a longer identifier such as
# `monitoring_status`, `research_notes`, or `company_status_history`.
_BARE_COL_RE = re.compile(r"(?<![\w_])(?:status|notes|monitored)(?![\w_])")

# Form 1 — direct: companies.<col> (not user_companies.<col>)
_DIRECT_RE = re.compile(r"(?<![\w_])companies\.(?:status|notes|monitored)\b")
# Form 2 — aliased: c.<col> (c is the companies alias throughout core/)
_ALIASED_RE = re.compile(r"\bc\.(?:status|notes|monitored)\b")
# Form 3 — bare column in a companies INSERT column list
_BARE_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+companies\s*\((?P<cols>[^)]*)\)", re.IGNORECASE)
# Form 4 — bare column in a companies UPDATE SET clause
_BARE_UPDATE_RE = re.compile(
    r"UPDATE\s+companies\s+SET\s+(?P<set>.*?)(?:\s+WHERE\b|;|$)",
    re.IGNORECASE | re.DOTALL)


def _strip_docstring(src: str) -> str:
    """Drop the leading method docstring so prose doesn't false-positive."""
    return re.sub(r'"""(?:.|\n)*?"""', "", src, count=1)


def _legacy_column_refs(src: str) -> list[str]:
    """Return human-readable hits for any legacy companies-column reference."""
    src = _strip_docstring(src)
    hits: list[str] = []
    hits += [f"direct: {m.group(0)}" for m in _DIRECT_RE.finditer(src)]
    hits += [f"aliased: {m.group(0)}" for m in _ALIASED_RE.finditer(src)]
    for m in _BARE_INSERT_RE.finditer(src):
        if _BARE_COL_RE.search(m.group("cols")):
            hits.append("bare INSERT column in companies")
    for m in _BARE_UPDATE_RE.finditer(src):
        if _BARE_COL_RE.search(m.group("set")):
            hits.append("bare UPDATE column in companies")
    return hits


def _method_sources():
    """Yield (name, source) for every JobStorage method outside the allow-list."""
    for name in dir(JobStorage):
        if name in _MIGRATION_FUNCTIONS:
            continue
        obj = getattr(JobStorage, name)
        if not callable(obj):
            continue
        try:
            yield name, inspect.getsource(obj)
        except (OSError, TypeError):
            continue


def test_no_legacy_company_columns_in_non_migration_sql():
    offenders: dict[str, list[str]] = {}
    for name, src in _method_sources():
        hits = _legacy_column_refs(src)
        if hits:
            offenders[name] = hits

    assert not offenders, (
        "non-migration methods reference legacy companies.status/notes/monitored:\n"
        + "\n".join(f"  {n}: {h}" for n, hs in offenders.items() for h in hs)
    )


# ── Negative fixtures — the scanner must flag each form ───────────────────────

def test_scanner_detects_direct_reference():
    assert _legacy_column_refs("SELECT companies.monitored FROM companies")


def test_scanner_detects_aliased_reference():
    assert _legacy_column_refs("SELECT c.status FROM companies c")


def test_scanner_does_not_false_positive_on_user_companies_column():
    # user_companies.monitored must NOT read as companies.monitored (substring trap).
    assert _legacy_column_refs(
        "SELECT 1 FROM user_companies WHERE company_id = ? AND monitored = 1") == []


def test_scanner_detects_bare_insert_column():
    assert _legacy_column_refs(
        "INSERT INTO companies (name, notes) VALUES (?, ?)")


def test_scanner_detects_bare_update_column():
    assert _legacy_column_refs(
        "UPDATE companies SET monitored = TRUE WHERE id = 1")


# ── Positive fixtures — the scanner must ignore legitimate forms ──────────────

def test_scanner_ignores_user_companies_and_capability_columns():
    src = (
        "SELECT COALESCE(uc.status, 'prospect') AS status, uc.notes, "
        "COALESCE(uc.monitored, 0) AS monitored, c.monitoring_status "
        "FROM companies c "
        "LEFT JOIN user_companies uc ON uc.company_id = c.id AND uc.user_id = ?"
    )
    assert _legacy_column_refs(src) == []
