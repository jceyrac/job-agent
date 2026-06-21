#!/usr/bin/env python3
"""One-off deduplication script for the jobs table using canonical_url.

Groups jobs by (source, canonical_url), merges duplicate groups, and writes
canonical_url onto every row so the upsert logic can use it going forward.

Usage:
  # Dry run (default) — show what would be merged, no writes
  python scripts/dedupe_jobs.py

  # Apply changes
  python scripts/dedupe_jobs.py --apply
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict

# Ensure the project root is on the path so we can import from storage
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paths import DB_PATH
from storage import normalize_url


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

NON_NEW_STATUSES = frozenset({"queued", "ready", "applied", "rejected", "archived", "expired"})



def _resolve_winner(group: list[dict]) -> tuple[dict | None, list[dict], str | None]:
    """Pick a winner from a duplicate group.

    Returns (winner, losers, flag_reason).
    flag_reason is None for clean merges, or a string for manual-review cases.
    """
    # Annotate with tracking status
    for job in group:
        job["_status"] = job.get("tracking_status") or "new"

    non_new = [j for j in group if j["_status"] != "new"]

    if len(non_new) > 1:
        # Multiple rows with user action — flag for manual review
        statuses = {j["_status"]: 0 for j in non_new}
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


def _merge_jobs(conn: sqlite3.Connection, winner: dict, losers: list[dict],
                canon_url: str) -> int:
    """Re-point foreign keys from losers to winner, then delete losers.

    Returns the number of rows deleted.
    """
    wid = winner["id"]

    # Collect loser notes (for potential copy to winner)
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
                # Winner already has a score for this profile — keep winner's
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
                # Both have tracking — preserve winner's status; merge notes
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

    # ---- update winner ----
    # Write canonical_url on the winner so future scrapes can find it via the
    # canonical_url lookup in _upsert_job_raw.  The winner keeps its original id;
    # PK migration is unnecessary because _upsert_job_raw checks canonical_url
    # before INSERT and reuses the existing id when found.
    conn.execute(
        "UPDATE jobs SET canonical_url = ? WHERE id = ?", (canon_url, wid)
    )

    # Copy loser notes to winner if winner has none
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


def _fill_canonical_urls(conn: sqlite3.Connection) -> int:
    """Fill canonical_url on rows that don't have it yet (single rows, not duplicates)."""
    rows = conn.execute(
        "SELECT id, url, source FROM jobs WHERE canonical_url IS NULL"
    ).fetchall()
    updated = 0
    for row in rows:
        canon = normalize_url(row["url"], row["source"])
        if canon:
            conn.execute(
                "UPDATE jobs SET canonical_url = ? WHERE id = ?", (canon, row["id"])
            )
            updated += 1
    return updated


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Deduplicate jobs by canonical URL (LinkedIn/Indeed normalization)"
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

    # ── Group by (source, canonical_url) ──
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    skipped_no_url = 0
    for row in rows:
        r = dict(row)
        canon = normalize_url(r["url"], r["source"])
        if not canon:
            skipped_no_url += 1
            continue
        r["_canonical_url"] = canon
        groups[(r["source"], canon)].append(r)

    if skipped_no_url:
        print(f"Skipped {skipped_no_url} rows with empty/unparseable URLs")

    # ── Find duplicate groups ──
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    single_groups = {k: v for k, v in groups.items() if len(v) == 1}

    print(f"Unique canonical URLs: {len(groups)}")
    print(f"  Groups with 1 row:    {len(single_groups)}")
    print(f"  Groups with >1 row:   {len(dup_groups)}")

    if not dup_groups:
        print("\nNo duplicates found. Nothing to merge.")

        # Still fill canonical_url on rows that don't have it
        if not dry_run:
            filled = _fill_canonical_urls(conn)
            if filled:
                conn.commit()
                print(f"Filled canonical_url on {filled} rows.")
        else:
            null_count = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE canonical_url IS NULL"
            ).fetchone()[0]
            if null_count:
                print(f"Would fill canonical_url on {null_count} rows (use --apply).")
        conn.close()
        return

    # ── Process duplicate groups ──
    merges_by_source: dict[str, int] = defaultdict(int)
    manual_review: list[tuple[str, str, list[dict]]] = []
    total_deleted = 0

    for (source, canon_url), group in sorted(dup_groups.items()):
        winner, losers, flag = _resolve_winner(group)

        if flag:
            manual_review.append((source, canon_url, group))
            continue

        assert winner and losers, f"Expected winner + losers for group {source}/{canon_url}"

        if dry_run:
            print(f"\n[{source}] {canon_url}")
            print(f"  Winner: {winner['id'][:12]}...  status={winner.get('_status','?')}  "
                  f"title=\"{winner.get('title','')[:60]}\"")
            for loser in losers:
                print(f"  Loser:  {loser['id'][:12]}...  status={loser.get('_status','?')}  "
                      f"title=\"{loser.get('title','')[:60]}\"")
        else:
            deleted = _merge_jobs(conn, winner, losers, canon_url)
            total_deleted += deleted
            merges_by_source[source] += 1

    # ── Summary ──
    print(f"\n{'─' * 50}")
    if dry_run:
        total_to_delete = sum(
            len(v) - 1 for v in dup_groups.values()
            if not any(
                len([j for j in v if j.get("_status", "new") != "new"]) > 1
                for _ in [None]  # dummy — _resolve_winner side-effect free in dry run
            )
        )
        # Recalculate properly
        total_to_delete = 0
        resolved = 0
        for (source, canon_url), group in sorted(dup_groups.items()):
            winner, losers, flag = _resolve_winner(group)
            if flag:
                continue
            total_to_delete += len(losers)
            merges_by_source[source] = merges_by_source.get(source, 0) + 1
            resolved += 1

        print(f"DRY RUN — would merge {total_to_delete} rows across {resolved} groups")
        for source, count in sorted(merges_by_source.items()):
            print(f"  {source}: {count} groups")
        if manual_review:
            print(f"\n⚠️  {len(manual_review)} groups flagged for manual review:")
            for source, canon_url, group in manual_review:
                statuses = {j.get("_status", "new") for j in group}
                print(f"  [{source}] {canon_url} — statuses: {statuses}")
        null_count = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE canonical_url IS NULL"
        ).fetchone()[0]
        if null_count:
            print(f"\nWould also fill canonical_url on {null_count} singleton rows.")
        print("\nDry run complete. Use --apply to execute changes.")

    else:
        # Fill canonical_url on singleton rows too
        filled = _fill_canonical_urls(conn)
        conn.commit()

        after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        print(f"Rows before: {total_before}")
        print(f"Rows after:  {after}")
        print(f"Deleted:     {total_deleted}")
        for source, count in sorted(merges_by_source.items()):
            print(f"  {source}: {count} groups merged")
        if filled:
            print(f"Filled canonical_url on {filled} singleton rows.")
        if manual_review:
            print(f"\n⚠️  {len(manual_review)} groups skipped (manual review needed):")
            for source, canon_url, group in manual_review:
                statuses = {j.get("_status", "new") for j in group}
                print(f"  [{source}] {canon_url} — statuses: {statuses}")
        print("\nDone.")

    conn.close()


if __name__ == "__main__":
    main()
