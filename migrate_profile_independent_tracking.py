"""migrate_profile_independent_tracking.py

Move status/notes out of job_scores into a profile-independent job_tracking table.
Move job_applications to be keyed on job_id only (drop profile_id from PK).
"""

import sqlite3
import sys

DB_PATH = "data/jobs.db"

STATUS_PRIORITY = {
    "applied":  0,
    "ready":    1,
    "queued":   2,
    "rejected": 3,
    "archived": 4,
    "saved":    5,
    "new":      6,
}


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

    score_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_scores)").fetchall()}
    if "status" not in score_cols:
        print("Already migrated — job_scores has no status column.")
        conn.close()
        return

    print("Starting profile-independent tracking migration...")

    # ── Step 1: Create job_tracking table ─────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_tracking (
            job_id  TEXT PRIMARY KEY,
            status  TEXT NOT NULL DEFAULT 'new',
            notes   TEXT
        )
    """)

    # ── Step 2: Merge status+notes across profiles into job_tracking ──────────
    rows = conn.execute(
        "SELECT job_id, status, notes FROM job_scores ORDER BY job_id"
    ).fetchall()

    # Group by job_id — pick most advanced status, merge distinct notes
    from collections import defaultdict
    by_job: dict[str, list] = defaultdict(list)
    for r in rows:
        by_job[r["job_id"]].append((r["status"] or "new", r["notes"] or ""))

    inserted = 0
    for job_id, entries in by_job.items():
        best_status = min(entries, key=lambda e: STATUS_PRIORITY.get(e[0], 99))[0]
        distinct_notes = list(dict.fromkeys(n for _, n in entries if n))
        merged_notes = "\n".join(distinct_notes) if distinct_notes else None
        conn.execute(
            "INSERT OR IGNORE INTO job_tracking (job_id, status, notes) VALUES (?, ?, ?)",
            (job_id, best_status, merged_notes),
        )
        inserted += 1

    print(f"  Created {inserted} job_tracking rows")

    # ── Step 3: Migrate job_applications to job_id-only PK ────────────────────
    app_cols = {r[1] for r in conn.execute("PRAGMA table_info(job_applications)").fetchall()}
    if "profile_id" in app_cols:
        conn.execute("""
            CREATE TABLE job_applications_new (
                job_id       TEXT PRIMARY KEY,
                analysis     TEXT,
                cover_letter TEXT,
                created_at   TEXT
            )
        """)
        # For each job_id keep the row with the most content
        app_rows = conn.execute(
            "SELECT job_id, analysis, cover_letter, created_at FROM job_applications ORDER BY job_id"
        ).fetchall()
        app_by_job: dict[str, dict] = {}
        for r in app_rows:
            jid = r["job_id"]
            content_len = len(r["analysis"] or "") + len(r["cover_letter"] or "")
            if jid not in app_by_job or content_len > app_by_job[jid]["_len"]:
                app_by_job[jid] = {
                    "analysis": r["analysis"],
                    "cover_letter": r["cover_letter"],
                    "created_at": r["created_at"],
                    "_len": content_len,
                }
        for jid, d in app_by_job.items():
            conn.execute(
                "INSERT INTO job_applications_new (job_id, analysis, cover_letter, created_at) VALUES (?, ?, ?, ?)",
                (jid, d["analysis"], d["cover_letter"], d["created_at"]),
            )
        conn.execute("DROP TABLE job_applications")
        conn.execute("ALTER TABLE job_applications_new RENAME TO job_applications")
        print(f"  Migrated {len(app_by_job)} job_applications rows (profile_id dropped)")
    else:
        print("  job_applications already has no profile_id — skipping")

    # ── Step 4: Recreate job_scores without status/notes ──────────────────────
    conn.execute("""
        CREATE TABLE job_scores_new (
            job_id        TEXT NOT NULL,
            profile_id    TEXT NOT NULL,
            score         INTEGER,
            reason        TEXT,
            summary       TEXT,
            work_mode     TEXT,
            geo_zone      TEXT,
            company_size  TEXT,
            contract_type TEXT,
            scored_by     TEXT,
            scored_at     TEXT,
            PRIMARY KEY (job_id, profile_id),
            FOREIGN KEY (job_id)     REFERENCES jobs(id),
            FOREIGN KEY (profile_id) REFERENCES search_profiles(id)
        )
    """)
    conn.execute("""
        INSERT INTO job_scores_new
        SELECT job_id, profile_id, score, reason, summary, work_mode, geo_zone,
               company_size, contract_type, scored_by, scored_at
        FROM job_scores
    """)
    conn.execute("DROP TABLE job_scores")
    conn.execute("ALTER TABLE job_scores_new RENAME TO job_scores")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_profile ON job_scores (profile_id, score DESC)")
    print("  Recreated job_scores without status/notes columns")

    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    run(db_path)
