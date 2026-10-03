#!/usr/bin/env python3
"""Consistent online backup of jobs.db (+ cv_agent_checkpoints.sqlite when present).

Spec 031, FR-001..008. stdlib only — no new dependency.

Uses ``sqlite3.Connection.backup`` (never a raw file copy) so WAL content is
included and the result is a single self-contained file (FR-001); verifies each
backup with ``PRAGMA integrity_check`` (FR-003); writes a JSON manifest
(FR-003); prunes to the last ``--keep`` backups, touching only ``--backup-dir``
(FR-006).

Operational note: this is a spec-mandated operational tool. It opens the *live*
DB only for the online backup call (FR-001), and runs its integrity/count reads
against the backup file it just produced — never a diagnostic read against the
live DB.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

CORE_TABLES = (
    "jobs",
    "job_scores",
    "job_tracking",
    "interactions",
    "contacts",
    "companies",
)


def _utc_stamp() -> str:
    """Backup set stem, e.g. ``20261003_120000`` (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _git_ref() -> str:
    """Short HEAD of the checkout, or ``unknown`` when git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def backup_sqlite(src: str, dst: str) -> int:
    """Copy ``src`` into ``dst`` via the online backup API. Returns dst size (bytes)."""
    src_conn = sqlite3.connect(src)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return os.path.getsize(dst)


def integrity_check(path: str) -> str:
    """Return ``"ok"`` if ``path`` passes ``PRAGMA integrity_check``, else raise."""
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    if not row or row[0] != "ok":
        raise RuntimeError(f"integrity_check failed for {path}: {row}")
    return "ok"


def table_counts(path: str) -> dict[str, int]:
    """Row counts for the core tables in ``path`` (read-only diagnostic)."""
    conn = sqlite3.connect(path)
    try:
        return {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in CORE_TABLES
        }
    finally:
        conn.close()


def build_manifest(timestamp: str, git_ref: str, files: dict, counts: dict) -> dict:
    """Assemble the manifest dict (FR-003)."""
    return {
        "timestamp": timestamp,
        "git_ref": git_ref,
        "files": files,
        "counts": counts,
    }


def _stems_to_prune(stems: list[str], keep: int) -> list[str]:
    """Given backup stems (``YYYYMMDD_HHMMSS``), return the ones to delete:
    all but the newest ``keep``, oldest-first."""
    if keep < 0:
        keep = 0
    return sorted(stems)[: max(0, len(stems) - keep)]


def prune_backups(backup_dir: str, keep: int) -> list[str]:
    """Delete backup files for stems beyond the retention window.

    Only touches files inside ``backup_dir``; a manual backup stored elsewhere
    (e.g. ``jobs_backup_manual.db`` in ``/app/data/``) is never affected.
    Returns the deleted stems.
    """
    if not os.path.isdir(backup_dir):
        return []
    stems = []
    for name in os.listdir(backup_dir):
        if name.startswith("manifest_") and name.endswith(".json"):
            stems.append(name[len("manifest_"):-len(".json")])

    deleted = []
    for stem in _stems_to_prune(stems, keep):
        for filename in (
            f"jobs_{stem}.db",
            f"cv_agent_checkpoints_{stem}.sqlite",
            f"manifest_{stem}.json",
        ):
            path = os.path.join(backup_dir, filename)
            if os.path.isfile(path):
                os.remove(path)
                deleted.append(stem)
    return sorted(set(deleted))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Consistent online backup of jobs.db (+ checkpoints DB when present).",
    )
    parser.add_argument("--db", default="/app/data/jobs.db")
    parser.add_argument("--checkpoints", default=None,
                        help="path to cv_agent_checkpoints.sqlite (default: sibling of --db)")
    parser.add_argument("--backup-dir", default="/app/data/backups")
    parser.add_argument("--keep", type=int, default=5)
    parser.add_argument("--ref", default=None,
                        help="git ref to record in the manifest (default: short HEAD)")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.db):
        print(f"ERROR: source DB not found: {args.db}", file=sys.stderr)
        return 1

    os.makedirs(args.backup_dir, exist_ok=True)

    checkpoints = args.checkpoints
    if checkpoints is None:
        checkpoints = os.path.join(
            os.path.dirname(os.path.abspath(args.db)), "cv_agent_checkpoints.sqlite"
        )

    stamp = _utc_stamp()
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    git_ref = args.ref or _git_ref()

    files: dict = {}
    jobs_dst = os.path.join(args.backup_dir, f"jobs_{stamp}.db")

    try:
        size = backup_sqlite(args.db, jobs_dst)
        integrity = integrity_check(jobs_dst)
        counts = table_counts(jobs_dst)
        files["jobs"] = {
            "path": os.path.basename(jobs_dst),
            "size_bytes": size,
            "integrity": integrity,
        }
    except Exception as exc:  # noqa: BLE001 — surface any backup failure to the caller
        print(f"ERROR: jobs.db backup failed: {exc}", file=sys.stderr)
        return 1

    if os.path.isfile(checkpoints):
        cp_dst = os.path.join(args.backup_dir, f"cv_agent_checkpoints_{stamp}.sqlite")
        try:
            size = backup_sqlite(checkpoints, cp_dst)
            integrity = integrity_check(cp_dst)
            files["checkpoints"] = {
                "path": os.path.basename(cp_dst),
                "size_bytes": size,
                "integrity": integrity,
            }
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: checkpoints backup failed: {exc}", file=sys.stderr)
            return 1
    else:
        print(f"notice: checkpoints DB absent, skipping: {checkpoints}")
        files["checkpoints"] = None

    manifest = build_manifest(iso, git_ref, files, counts)
    manifest_path = os.path.join(args.backup_dir, f"manifest_{stamp}.json")
    with open(manifest_path, "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")

    pruned = prune_backups(args.backup_dir, args.keep)
    if pruned:
        print(f"pruned {len(pruned)} old backup(s): {', '.join(pruned)}")

    print(f"backup complete: {jobs_dst} (integrity {files['jobs']['integrity']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
