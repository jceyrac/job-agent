"""migrate_single_status.py — Merge application_status into status; move analysis/cover_letter to job_applications."""

import sqlite3
import sys

DB_PATH = "data/jobs.db"


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

    cols = {row[1] for row in conn.execute("PRAGMA table_info(job_scores)").fetchall()}
    if "application_status" not in cols:
        print("Already migrated — job_scores has no application_status column.")
        conn.close()
        return

    print("Starting single-status migration...")

    # Step 1: Create job_applications table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_applications (
            job_id       TEXT NOT NULL,
            profile_id   TEXT NOT NULL,
            analysis     TEXT,
            cover_letter TEXT,
            created_at   TEXT,
            PRIMARY KEY (job_id, profile_id)
        )
    """)

    # Step 2: Migrate analysis/cover_letter data before dropping columns
    r = conn.execute("""
        INSERT OR IGNORE INTO job_applications (job_id, profile_id, analysis, cover_letter, created_at)
        SELECT job_id, profile_id, analysis, cover_letter, scored_at
        FROM job_scores
        WHERE analysis IS NOT NULL OR cover_letter IS NOT NULL
    """)
    print(f"  Migrated {r.rowcount} application rows → job_applications")

    # Step 3: Promote application_status into status
    r1 = conn.execute("""
        UPDATE job_scores
        SET status = 'queued'
        WHERE application_status = 'queued' AND status = 'new'
    """)
    print(f"  Set {r1.rowcount} rows to status='queued'")

    r2 = conn.execute("""
        UPDATE job_scores
        SET status = 'ready'
        WHERE application_status = 'ready' AND status IN ('new', 'saved')
    """)
    print(f"  Set {r2.rowcount} rows to status='ready'")

    # Step 4: Recreate job_scores without application_status, analysis, cover_letter
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
            status        TEXT NOT NULL DEFAULT 'new',
            notes         TEXT,
            PRIMARY KEY (job_id, profile_id),
            FOREIGN KEY (job_id)     REFERENCES jobs(id),
            FOREIGN KEY (profile_id) REFERENCES search_profiles(id)
        )
    """)

    conn.execute("""
        INSERT INTO job_scores_new
        SELECT job_id, profile_id, score, reason, summary, work_mode, geo_zone,
               company_size, contract_type, scored_by, scored_at, status, notes
        FROM job_scores
    """)

    conn.execute("DROP TABLE job_scores")
    conn.execute("ALTER TABLE job_scores_new RENAME TO job_scores")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_profile ON job_scores (profile_id, score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_status  ON job_scores (profile_id, status)")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    run(db_path)
