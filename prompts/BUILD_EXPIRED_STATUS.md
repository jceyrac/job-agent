# BUILD — Add `expired` job status + DB migration

> Spec for Claude Code.
> Read `models.py`, `storage.py`, `tracker.py`, and `tracker_views/` before starting.
> Do not modify `main.py`, `scrape.py`, `score.py`, or any scraper.

---

## Context

A new terminal job status `expired` is needed to distinguish jobs the user **wanted to apply to but could no longer**, because the posting had closed. This is semantically different from `archived` (not interested) and `rejected` (dismissed).

Current terminal statuses: `new`, `saved`, `applied`, `rejected`, `archived`  
New status to add: **`expired`**

---

## Status semantics (for reference)

| Status | Meaning | Terminal? |
|--------|---------|-----------|
| `new` | Unreviewed | No |
| `saved` | Interesting, to apply | No |
| `applied` | Application sent | Yes |
| `rejected` | Not relevant / dismissed | Yes |
| `expired` | Wanted to apply, posting closed | Yes ← **new** |
| `archived` | Hidden from view | Yes |

---

## Changes required

### 1. `models.py`

Add `"expired"` to the `JobStatus` literal type or enum (wherever the other statuses are defined).

---

### 2. `tracker.py` and/or `tracker_views/`

Three places to update in the Streamlit UI:

**a) Status filter multiselect (sidebar)**  
Add `"expired"` to the list of options. Default: unchecked (same as `archived` — it's a terminal state).

**b) Status action buttons on each job card**  
Add a `[⏰ Expired]` button alongside the existing status buttons (`Save`, `Applied`, `Reject`, `Archive`).  
Clicking it calls `db.set_status(job_id, profile_id, "expired")` and triggers `st.rerun()`.  
Only show the button if the current status is not already `expired`.

**c) Stats bar**  
No change needed — `expired` is a terminal state like `applied` and does not need to appear in the main counters. Optionally add `Expired: N` next to `Applied: N` if the stats bar has room, but do not break the existing layout.

---

### 3. One-time DB migration

After making the code changes, run this migration to reclassify existing `archived` rows whose notes indicate the posting had closed.

The migration targets **41 rows** identified by pattern-matching on the `notes` field in `job_tracking`. These are jobs archived solely because the posting was no longer available (confirmed by manual review of the data).

```python
# migrate_expired_status.py  ← create this file at project root
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "jobs.db")

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
```

**The 5 excluded rows** (`NOT IN` list) are mixed-reason cases where the posting was also closed, but the primary disqualifier was geography or industry — they stay as `archived`.

Run after code changes:
```bash
python migrate_expired_status.py
```

Expected output: `Migration complete: 41 rows updated to status='expired'`

---

## Acceptance criteria

- [ ] `"expired"` is a valid value in the `JobStatus` type
- [ ] `[⏰ Expired]` button appears on job cards and correctly updates status
- [ ] `expired` appears as a filter option in the sidebar (default: unchecked)
- [ ] `python migrate_expired_status.py` runs without error and reports 41 rows updated
- [ ] Verify in DB: `sqlite3 data/jobs.db "SELECT COUNT(*) FROM job_tracking WHERE status='expired'"` → 41
