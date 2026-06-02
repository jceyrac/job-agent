import sqlite3
import os

from paths import DB_PATH

MIGRATION_SQL = """
UPDATE job_tracking
SET status = 'expired'
WHERE status = 'archived'
AND (
    LOWER(notes) LIKE '%no longer accepting%'
    OR LOWER(notes) LIKE '%no longer receiving%'
    OR LOWER(notes) LIKE '%no longer applications%'
    OR LOWER(notes) LIKE '%not available anymore%'
    OR LOWER(notes) LIKE '%not online anymore%'
    OR LOWER(notes) LIKE '%the job is not available%'
    OR LOWER(notes) LIKE '%receives no more applications%'
    OR LOWER(notes) LIKE '%unavailable%'
    OR LOWER(notes) LIKE '%job expired%'
    OR LOWER(notes) LIKE '%seems removed%'
    OR LOWER(notes) LIKE '%page not found%'
    OR notes LIKE '%Vielen Dank%'
    OR notes IN ('Expired', 'Closed', 'Not available')
)
AND notes NOT IN (
    'In the US and no longer accepting applications',
    'Hybrid spain, no longer accepting applications',
    'Healthcare and no longer accepting applications',
    'No longer accepting applications - hybrid NL',
    'latin ametica and not available'
);
"""

if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(MIGRATION_SQL)
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"Migration complete: {affected} rows updated to status='expired'")
