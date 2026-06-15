Context: job-agent project at ~/AI-Suite/job_agent/
Read storage.py, main.py, and SPEC_tracker_streamlit.md before starting.

Task: Add pipeline run logging to storage.py, main.py, and tracker.py.

---

## 1. storage.py — add `runs` table + 2 methods

Add to the SCHEMA string (alongside existing tables):

```sql
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    profile_id TEXT,
    jobs_scraped INTEGER,
    jobs_scored INTEGER,
    jobs_above_threshold INTEGER,
    status TEXT,
    error_msg TEXT
);
```

Add method `log_run`:
```python
def log_run(
    self,
    profile_id: str,
    jobs_scraped: int,
    jobs_scored: int,
    jobs_above_threshold: int,
    status: str,          # 'success', 'partial', 'error'
    error_msg: str = None
) -> None:
    with self._conn() as conn:
        conn.execute(
            """INSERT INTO runs
               (ran_at, profile_id, jobs_scraped, jobs_scored, jobs_above_threshold, status, error_msg)
               VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)""",
            (profile_id, jobs_scraped, jobs_scored, jobs_above_threshold, status, error_msg)
        )
```

Add method `get_last_run`:
```python
def get_last_run(self, profile_id: str = None) -> dict | None:
    with self._conn() as conn:
        if profile_id:
            row = conn.execute(
                "SELECT * FROM runs WHERE profile_id = ? ORDER BY ran_at DESC LIMIT 1",
                (profile_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM runs ORDER BY ran_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
```

Do not modify any existing storage.py methods.

---

## 2. main.py — wrap pipeline in try/except and call log_run at the end

Locate where the pipeline ends (after scoring and notification). Wrap the core pipeline body in a try/except block that catches all exceptions. At the end of the try block, call:

```python
db.log_run(
    profile_id=profile.id,
    jobs_scraped=len(all_jobs),        # total fetched before filtering
    jobs_scored=len(scored_jobs),      # total passed to scorer
    jobs_above_threshold=len(digest_jobs),  # jobs with score >= threshold
    status="success"
)
```

In the except block:
```python
db.log_run(
    profile_id=profile.id if profile else "unknown",
    jobs_scraped=0,
    jobs_scored=0,
    jobs_above_threshold=0,
    status="error",
    error_msg=str(e)
)
raise  # re-raise so cron logs still capture the error
```

Adapt variable names to match what main.py actually uses — read the file first. Do not restructure main.py beyond adding this logging wrapper.

---

## 3. tracker.py — add Last Run banner above the stats bar in Tab 1

At the top of the Jobs tab, before the stats bar, add a last run banner using `db.get_last_run(profile_id)`.

Display logic:
- If no run found: `st.info("No pipeline run recorded yet.")`
- If status == 'success':