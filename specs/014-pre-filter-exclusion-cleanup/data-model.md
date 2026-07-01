# Data Model: Pre-filter exclusion cleanup

**Feature**: Spec 014
**Date**: 2026-07-01

## Schema Impact

**No changes.** This feature filters jobs before they reach `db.save_unscored()`. It does not:

- Add, remove, or modify any table or column
- Change any `JobStorage` method signature
- Introduce new statuses or state transitions

## Affected Data Flow

```
Before (current):
  scraper.fetch() → JobFilterEngine.apply() → dedup → db.save_unscored() → DB

After (spec 014):
  scraper.fetch() → JobFilterEngine.apply() → dedup → title gate → db.save_unscored() → DB
                                                         ↓
                                                   skipped (no DB write)
```

## Entities

No new entities. The gate operates on `JobPosting.title` (a string field in `models.py`), which already exists and is unchanged.
