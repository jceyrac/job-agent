#!/usr/bin/env python3
"""Merge duplicate jobs found by exact (source, norm_title, norm_company) match.

Usage:
  # Dry run (default) — show what would be merged, no writes
  python scripts/dedupe_title_company.py

  # Apply changes
  python scripts/dedupe_title_company.py --apply
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paths import DB_PATH
from storage import normalize_title, normalize_company

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

NON_NEW_STATUSES = frozenset({"queued", "ready", "applied", "rejected", "archived", "expired"})


def _resolve_winner(group: list[dict]) -> tuple[dict | None, list[dict], str | None]:
    """Pick a winner from a duplicate group.

    Returns (winner, losers, flag_reason).
    flag_reason is None for clean merges, or a string for manual-review cases.
    """
    for job in group:
        job["_status"] = job.get("tracking_status") or "new"

    non_new = [j for j in group if j["_status"] != "new"]

    if len(non_new) > 1:
        statuses = {}
        for j in non_new:
            statuses[j["_status"]] = statuses.get(j["_status"], 0) + 1
        return None, [], f"multiple non-new statuses: {statuses}"

    if len(non_new) == 1:
        winner = non_new[0]
    else:
        # All "new" — keep most recently scraped
        winner = max(group, key=lambda j: j.get("last_seen") or "")

    losers = [j for j in group if j["id"] != winner["id"]]
    return winner, losers, None


def _merge_jobs(conn: sqlite3.Connection, winner: dict, losers: list[dict]) -> int:
    """Re-point foreign keys from losers to winner, then delete losers.

    Returns the number of rows deleted.
    """
    wid = winner["id"]

    loser_notes = []
    for loser in losers:
        lid = loser["id"]

        # ---- job_scores ----
        loser_scores = conn.execute(
            "SELECT profile_id FROM job_scores WHERE job_id = ?", (lid,)
        ).fetchall()
        for ls in loser_scores:
            existing = conn.execute(
                "SELECT 1 FROM job_scores WHERE job_id = ? AND profile_id = ?",
                (wid, ls["profile_id"]),
            ).fetchone()
            if existing:
                conn.execute(
                    "DELETE FROM job_scores WHERE job_id = ? AND profile_id = ?",
                    (lid, ls["profile_id"]),
                )
            else:
                conn.execute(
                    "UPDATE job_scores SET job_id = ? WHERE job_id = ? AND profile_id = ?",
                    (wid, lid, ls["profile_id"]),
                )

        # ---- job_tracking ----
        w_track = conn.execute(
            "SELECT notes, status FROM job_tracking WHERE job_id = ?", (wid,)
        ).fetchone()
        l_track = conn.execute(
            "SELECT notes, status FROM job_tracking WHERE job_id = ?", (lid,)
        ).fetchone()
        if l_track:
            loser_notes.append(l_track["notes"])
            if not w_track:
                conn.execute(
                    "UPDATE job_tracking SET job_id = ? WHERE job_id = ?", (wid, lid)
                )
            else:
                if not w_track["notes"] and l_track["notes"]:
                    conn.execute(
                        "UPDATE job_tracking SET notes = ? WHERE job_id = ?",
                        (l_track["notes"], wid),
                    )
                conn.execute("DELETE FROM job_tracking WHERE job_id = ?", (lid,))

        # ---- job_applications ----
        w_app = conn.execute(
            "SELECT 1 FROM job_applications WHERE job_id = ?", (wid,)
        ).fetchone()
        l_app = conn.execute(
            "SELECT 1 FROM job_applications WHERE job_id = ?", (lid,)
        ).fetchone()
        if l_app:
            if not w_app:
                conn.execute(
                    "UPDATE job_applications SET job_id = ? WHERE job_id = ?", (wid, lid)
                )
            else:
                conn.execute("DELETE FROM job_applications WHERE job_id = ?", (lid,))

        # ---- status_history ----
        conn.execute(
            "UPDATE OR IGNORE status_history SET job_id = ? WHERE job_id = ?", (wid, lid)
        )

        # ---- interactions ----
        conn.execute(
            "UPDATE OR IGNORE interactions SET job_id = ? WHERE job_id = ?", (wid, lid)
        )

        # ---- delete the loser ----
        conn.execute("DELETE FROM jobs WHERE id = ?", (lid,))

    # ---- copy loser notes to winner if winner has none ----
    w_track = conn.execute(
        "SELECT notes FROM job_tracking WHERE job_id = ?", (wid,)
    ).fetchone()
    if w_track and not w_track["notes"]:
        for note in loser_notes:
            if note:
                conn.execute(
                    "UPDATE job_tracking SET notes = ? WHERE job_id = ?", (note, wid)
                )
                break

    return len(losers)


def _backfill_norm_columns(conn: sqlite3.Connection) -> int:
    """Fill norm_title, norm_company, and canonical_url on rows missing them."""
    rows = conn.execute(
        "SELECT id, title, company, url, source, "
        "norm_title AS nt, norm_company AS nc, canonical_url AS cu "
        "FROM jobs "
        "WHERE norm_title IS NULL OR norm_company IS NULL OR canonical_url IS NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        updates = []
        params = []
        if row["title"] and not row["nt"]:
            updates.append("norm_title = ?")
            params.append(normalize_title(row["title"]))
        if row["company"] and not row["nc"]:
            updates.append("norm_company = ?")
            params.append(normalize_company(row["company"]))
        if row["url"] and row["source"] and not row["cu"]:
            from storage import normalize_url
            canon = normalize_url(row["url"], row["source"])
            if canon:
                updates.append("canonical_url = ?")
                params.append(canon)
        if updates:
            params.append(row["id"])
            conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?", params)
            updated += 1
    return updated


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Merge duplicate jobs by (source, norm_company, norm_title) exact match"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write changes to the DB (default: dry run only)",
    )
    args = parser.parse_args()

    dry_run = not args.apply

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # ── Load all jobs with tracking status ──
    rows = conn.execute("""
        SELECT j.*, t.status AS tracking_status, t.notes AS tracking_notes
        FROM jobs j
        LEFT JOIN job_tracking t ON j.id = t.job_id
    """).fetchall()

    total_before = len(rows)
    print(f"Total jobs: {total_before}")

    # ── Load + backfill norm columns in-memory for any rows missing them ──
    job_dicts = []
    for row in rows:
        r = dict(row)
        if not r.get("norm_title") and r.get("title"):
            r["norm_title"] = normalize_title(r["title"])
        if not r.get("norm_company") and r.get("company"):
            r["norm_company"] = normalize_company(r["company"])
        job_dicts.append(r)

    # ── Group by (source, norm_company, norm_title) ──
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    skipped = 0
    for r in job_dicts:
        source = r["source"]
        nc = r.get("norm_company") or ""
        nt = r.get("norm_title") or ""
        if not source or not nc or not nt:
            skipped += 1
            continue
        groups[(source, nc, nt)].append(r)

    if skipped:
        print(f"Skipped {skipped} rows with missing source/company/title")

    # ── Find duplicate groups ──
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    single_count = len(groups) - len(dup_groups)

    print(f"Unique (source, norm_company, norm_title) keys: {len(groups)}")
    print(f"  Groups with 1 row:    {single_count}")
    print(f"  Groups with >1 row:   {len(dup_groups)}")

    if not dup_groups:
        print("\nNo duplicates found. Nothing to merge.")
        if not dry_run:
            filled = _backfill_norm_columns(conn)
            if filled:
                conn.commit()
                print(f"Backfilled norm columns on {filled} rows.")
        else:
            null_count = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE norm_title IS NULL OR norm_company IS NULL"
            ).fetchone()[0]
            if null_count:
                print(f"Would backfill norm columns on ~{null_count} rows (use --apply).")
        conn.close()
        return

    # ── Process duplicate groups ──
    merges_by_source: dict[str, int] = defaultdict(int)
    manual_review: list[tuple[str, str, str, list[dict]]] = []
    total_deleted = 0

    # Collect display samples for dry-run
    dry_samples = []

    dup_list = sorted(dup_groups.items(), key=lambda x: -len(x[1]))
    for (source, norm_co, norm_ti), group in dup_list:
        winner, losers, flag = _resolve_winner(group)

        if flag:
            manual_review.append((source, norm_co, norm_ti, group))
            continue

        assert winner and losers, f"Expected winner + losers for group {source}/{norm_co}/{norm_ti}"

        if dry_run:
            if len(dry_samples) < 10:
                dry_samples.append((source, norm_co, norm_ti, winner, losers))
            merges_by_source[source] += 1
        else:
            deleted = _merge_jobs(conn, winner, losers)
            total_deleted += deleted
            merges_by_source[source] += 1

    # ── Summary ──
    total_to_delete = sum(
        len(v) - 1
        for k, v in dup_list
        if not any(
            (dict(r).get("tracking_status") or "new") != "new"
            for r in v
        )
        # re-count properly accounting for manual-review skips
    )
    # Recalculate properly
    total_to_delete = 0
    resolved = 0
    for (source, norm_co, norm_ti), group in dup_list:
        winner, losers, flag = _resolve_winner(group)
        if flag:
            continue
        total_to_delete += len(losers)
        resolved += 1

    print(f"\n{'─' * 60}")

    if dry_run:
        print(f"DRY RUN — would merge {total_to_delete} rows across {resolved} groups")
        for source, count in sorted(merges_by_source.items(), key=lambda x: -x[1]):
            would_merge = sum(
                len(losers)
                for s, nc, nt, w, losers in dry_samples
                if s == source
            )
            # recalc properly
            would_merge = 0
            for s, nc, nt, w, losers in dry_samples:
                if s == source:
                    would_merge += len(losers)
            print(f"  {source}: {count} groups, {would_merge} rows")
        # Full breakdown
        total_by_source = defaultdict(lambda: {"groups": 0, "rows": 0})
        for (source, _, _), group in dup_list:
            w, l, flag = _resolve_winner(group)
            if flag:
                continue
            total_by_source[source]["groups"] += 1
            total_by_source[source]["rows"] += len(l)
        print(f"\n  Full breakdown:")
        for source in sorted(total_by_source, key=lambda s: -total_by_source[s]["rows"]):
            d = total_by_source[source]
            print(f"    {source}: {d['groups']} groups, {d['rows']} rows")

        if manual_review:
            print(f"\n⚠️  {len(manual_review)} groups flagged for manual review:")
            for source, norm_co, norm_ti, group in manual_review:
                statuses = {dict(j).get("tracking_status", "new") for j in group}
                print(f"  [{source}] company={norm_co[:50]}  title={norm_ti[:60]}  — statuses: {statuses}")

        # Print samples
        print(f"\n{'─' * 60}")
        print("SAMPLE MERGES (first 10 groups):")
        for i, (source, norm_co, norm_ti, winner, losers) in enumerate(dry_samples):
            print(f"\n  [{i+1}] [{source}] company={norm_co[:60]}  title={norm_ti[:80]}")
            print(f"      KEEP: id={winner['id'][:12]}...  seen={winner['last_seen']}  status={winner.get('_status','?')}")
            for loser in losers:
                print(f"      DROP: id={loser['id'][:12]}...  seen={loser['last_seen']}  status={loser.get('_status','?')}")

        null_count = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE norm_title IS NULL OR norm_company IS NULL"
        ).fetchone()[0]
        if null_count:
            print(f"\nWould also backfill norm columns on {null_count} rows.")

        print("\nDry run complete. Use --apply to execute changes.")

    else:
        # Backfill norm columns on survivors
        filled = _backfill_norm_columns(conn)
        conn.commit()

        after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        print(f"Rows before: {total_before}")
        print(f"Rows after:  {after}")
        print(f"Deleted:     {total_deleted}")
        for source, count in sorted(merges_by_source.items(), key=lambda x: -x[1]):
            print(f"  {source}: {count} groups merged")
        if filled:
            print(f"Backfilled norm/url columns on {filled} rows.")
        if manual_review:
            print(f"\n⚠️  {len(manual_review)} groups skipped (manual review needed):")
            for source, norm_co, norm_ti, group in manual_review:
                statuses = {dict(j).get("tracking_status", "new") for j in group}
                print(f"  [{source}] {norm_co[:50]} — {norm_ti[:60]}  statuses: {statuses}")
        print("\nDone.")

    conn.close()


if __name__ == "__main__":
    main()
