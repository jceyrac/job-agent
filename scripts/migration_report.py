#!/usr/bin/env python3
"""Read-only migration diagnostics for spec 039 (shared catalogue vs user space).

Prints, for a DB file (never mutating it):
  (a) row count per catalogue table,
  (b) row count + rows WITHOUT a `user_id` per user-space table (must be 0 after
      the one-shot migration; "n/a" when the column is absent, i.e. pre-migration),
  (c) `users` and `user_companies` row counts.

Uses a plain read-only `sqlite3` connection (SELECT / PRAGMA only), the same shape
as ``scripts/fingerprint.py`` `_table_counts`. `scripts/` is outside the spec-037
raw-SQL guard, so this is allowed (D11). Run against a **copy** in practice.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

# User-space tables per data-model.md — every row must carry a `user_id` after
# the migration. Order is stable for easy before/after diffing.
USER_SPACE_TABLES = (
    "search_profiles",
    "job_tracking",
    "job_applications",
    "status_history",
    "contacts",
    "interactions",
    "company_status_history",
)

# Catalogue tables reported for row-count parity.
CATALOGUE_TABLES = (
    "jobs",
    "job_scores",
    "companies",
    "runs",
    "migrations",
    "config",
)


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def _count(conn: sqlite3.Connection, table: str, existing: set[str]) -> int | None:
    if table not in existing:
        return None
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def _has_column(conn: sqlite3.Connection, table: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return "user_id" in cols


def report(path: str) -> str:
    conn = sqlite3.connect(path)
    try:
        existing = _existing_tables(conn)
        lines: list[str] = []

        lines.append("Catalogue tables:")
        for table in CATALOGUE_TABLES:
            n = _count(conn, table, existing)
            lines.append(f"  {table:24} {n if n is not None else 'n/a':>6}")

        users = _count(conn, "users", existing)
        user_companies = _count(conn, "user_companies", existing)
        lines.append(f"  {'users':24} {users if users is not None else 'n/a':>6}")
        lines.append(f"  {'user_companies':24} {user_companies if user_companies is not None else 'n/a':>6}")

        lines.append("User-space tables (rows without user_id must be 0 after migration):")
        for table in USER_SPACE_TABLES:
            total = _count(conn, table, existing)
            if total is None:
                lines.append(f"  {table:24} n/a")
                continue
            if not _has_column(conn, table):
                missing = "n/a (column absent — pre-migration)"
            else:
                missing = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE user_id IS NULL'
                ).fetchone()[0]
            lines.append(f"  {table:24} total={total:<6} without_user_id={missing}")

        return "\n".join(lines)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only spec-039 migration diagnostics.",
    )
    parser.add_argument("--db", required=True)
    args = parser.parse_args(argv)

    if not os.path.isfile(args.db):
        print(f"ERROR: DB not found: {args.db}", file=sys.stderr)
        return 1

    try:
        print(report(args.db))
    except sqlite3.Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
