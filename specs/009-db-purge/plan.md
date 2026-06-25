# Implementation Plan: Automatic DB Purge (stale jobs cleanup)

**Branch**: `009-db-purge` | **Date**: 2026-06-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/009-db-purge/spec.md`

## Summary

Add automatic stale job cleanup at pipeline start. Purge jobs >N days old (default 30) with status `new` (or no tracking row) and no notes. Add a Settings widget to preview and configure retention. Three files touched: `storage.py` (2 new methods), `main.py` (1 call), and `tracker_views/settings.py` (1 widget).

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: None (stdlib sqlite3)
**Storage**: SQLite via `JobStorage` methods — read/write, transactional
**Testing**: Manual verification on dev DB; storage unit tests for the new methods
**Target Platform**: macOS (dev) + Ubuntu server (prod)
**Project Type**: Backend pipeline + Streamlit UI

**Performance Goals**: <1s on 3000+ job DB (single SQL DELETE with subquery)

**Constraints**: No new files; no new dependencies; no changes to `scorer.py`, `profiles.py`, `models.py`, scrapers

**Scale/Scope**: 2 new methods in `storage.py` (~40 lines), 1 block in `main.py` (~5 lines), 1 widget in `settings.py` (~30 lines)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Filet large, point de filtrage unique | ✅ N/A | Data cleanup, not filtering |
| II. Deux chemins d'amélioration | ✅ Pass | Code change → git push → deploy |
| III. Profil unifié unique | ✅ Pass | Profile-independent purge |
| IV. Structure déterministe, prose LLM uniquement | ✅ Pass | Deterministic SQL logic |
| V. Modification chirurgicale | ✅ Pass | Three files, minimal diffs |
| VI. Validation empirique avant livraison | ✅ Pass | Will verify against live DB metrics |
| VII. Sécurité d'abord | ✅ Pass | Read/write DB only, no network |
| VIII. Scrapers organisés par modèle d'acquisition | ✅ N/A | Not a scraper |
| IX. Le scoring est une couche optionnelle | ✅ Pass | Works without scores |

**Verdict**: No violations.

## Project Structure

### Source Code (repository root)

```text
storage.py                # +purge_stale_jobs(), +count_purgeable_jobs()
main.py                   # +purge call at pipeline start
tracker_views/settings.py # +retention widget
```

**Structure Decision**: No new files. Two new methods in existing `JobStorage` class, one call in `main.py`, one widget in the settings page.

## Phase 0 — Research

### FK cascade verification ✅

Confirmed via schema inspection (2026-06-25): no FK has `ON DELETE CASCADE`.

| Table | FK | CASCADE? |
|-------|-----|----------|
| `job_scores` | `job_id → jobs(id)` | No |
| `job_tracking` | `job_id → jobs(id)` | No |
| `status_history` | `job_id → jobs(id)` | No |
| `job_applications` | None (TEXT PK) | N/A |
| `interactions` | `job_id → jobs(id)` | No |

**Decision**: Manual DELETE order within a transaction. Skip `job_applications` and `interactions` — purgeable jobs (untouched, status=new) can't have rows in those tables by definition.

### Config key convention ✅

The `config` table already uses keys like `scraper.greenhouse.enabled`. New key: `purge_retention_days` — consistent with existing flat key convention.

## Phase 1 — Design

### Data Model

No new tables or columns. New config key:

```
key:   "purge_retention_days"
value: "30"  (string, parsed to int)
```

### SQL Query (from spec)

```sql
DELETE FROM jobs
WHERE id IN (
    SELECT j.id FROM jobs j
    LEFT JOIN job_tracking jt ON j.id = jt.job_id
    WHERE date(j.first_seen) < date('now', '-' || ? || ' days')
      AND (
          jt.job_id IS NULL
          OR (jt.status = 'new' AND (jt.notes IS NULL OR trim(jt.notes) = ''))
      )
)
```

### Transaction Order

```python
with self._conn() as conn:
    conn.execute("BEGIN")
    conn.execute("DELETE FROM status_history WHERE job_id IN (...)")
    conn.execute("DELETE FROM job_scores WHERE job_id IN (...)")
    conn.execute("DELETE FROM job_tracking WHERE job_id IN (...)")
    count = conn.execute("DELETE FROM jobs WHERE id IN (...)").rowcount
    conn.execute("COMMIT")
    return count
```

### `main.py` integration

At the very beginning of `main()`, before scraping:

```python
retention_days = int(db.get_config("purge_retention_days", default="30"))
purged = db.purge_stale_jobs(retention_days)
if purged > 0:
    print(f"[purge] {purged} stale jobs removed (>{retention_days}d, untouched)")
else:
    print(f"[purge] No stale jobs to remove")
```

### Settings Widget

In `tracker_views/settings.py`, in the Pipeline/Scraper section:

```python
# Retention
retention = st.number_input("Retention period (days)", min_value=7, max_value=180,
                            value=int(db.get_config("purge_retention_days", default="30")))
purgeable = db.count_purgeable_jobs(retention)
st.caption(f"ℹ️ With this setting, {purgeable} jobs would be purged on next run (stale, untouched).")
if st.button("Save retention", key="save_retention"):
    db.set_config("purge_retention_days", str(retention))
    st.success("Saved")
```

### Data Flow

```
main.py: get_config("purge_retention_days") → purge_stale_jobs(N) → log count
tracker: number_input → count_purgeable_jobs(N) → live preview → save to config
```

## Complexity Tracking

No violations. Table intentionally left empty.

## Implementation Steps

### Step 1 — `storage.py`

Add two methods to `JobStorage`:
- `purge_stale_jobs(retention_days: int = 30) -> int` — transactional DELETE across 4 tables
- `count_purgeable_jobs(retention_days: int = 30) -> int` — SELECT COUNT(*) only

### Step 2 — `main.py`

Add purge call at pipeline start (after `db = JobStorage(DB_PATH)`, before scrape).

### Step 3 — `tracker_views/settings.py`

Add retention widget in the Pipeline/Scraper section.

### Step 4 — Tests

Add unit tests for both new methods (in-memory DB):
- `test_purge_stale_jobs_removes_untouched_old_jobs`
- `test_purge_stale_jobs_preserves_jobs_with_notes`
- `test_purge_stale_jobs_preserves_saved_applied_rejected`
- `test_count_purgeable_jobs_matches_purge_count`
