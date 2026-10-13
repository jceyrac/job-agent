# Quickstart — validate spec 039 (shared catalogue vs user space)

Run these on the dev Mac (`.venv/`), against a **copy** of a real DB, never the
live `data/jobs.db`. The point is to prove the migration and the seam behave
exactly as before for the single user, and correctly isolate two users.

## Prerequisites

```bash
cd /Users/jeanclaudevd/AI-Suite/job_agent
source .venv/bin/activate
cp data/jobs.db /tmp/spec039.db            # work on a copy
```

## 1. Migration is one-shot, idempotent, lossless

```bash
python -m scripts.migration_report --db /tmp/spec039.db          # BEFORE
```

Record the per-table row counts. Then open it once (any `JobStorage` open runs
the migration) and re-report:

```bash
python -c "from core.storage import JobStorage; s = JobStorage('/tmp/spec039.db'); print('users:', s.get_config('default_user_id'))"
python -m scripts.migration_report --db /tmp/spec039.db          # AFTER
```

Expected: row counts per table **unchanged**; "rows without `user_id`" = 0 for
`search_profiles`, `job_tracking`, `job_applications`, `status_history`,
`contacts`, `interactions`, `company_status_history`; `user_companies` count =
companies with a non-default relationship (`status != 'prospect'` OR non-empty
`notes` OR `monitored = 1`); `users` has exactly 1 row. Open it a second time —
no further changes (idempotent, recorded in `migrations`).

## 2. Single-user behaviour is byte-identical

```bash
python -m scripts.fingerprint --db /tmp/spec039.db --as-of <date> --meta-candidate
# compare against the pre-migration fingerprint of the same DB
```

Expected: **identical** (parity, Constitution XI).

## 3. Two-user isolation (the seam)

```bash
python -m pytest tests/test_isolation.py -v
```

Expected: green. It creates users A and B in an in-memory DB, drives every
public user-space method as A, and asserts every public user-space read as B
returns none of A's data (and vice versa). The suite also asserts its
`USER_SPACE_METHODS` list covers the full set of public user-space methods.

## 4. Full unit suite + guard

```bash
python -m pytest tests/ -v
```

Expected: all green, including `tests/test_storage.py` (updated for the new
schema, never reduced) and `tests/test_guard_db_access.py` (no raw `sqlite3`
outside `core/storage.py` in runtime code).

## 5. Pipeline smoke (single user)

```bash
python -m core.score --extract                 # profile-independent, catalogue
python -m core.scrape --monitored-only --no-score   # same companies as before
```

Expected: `--monitored-only` targets the same companies as before the split
(`user_companies.monitored = 1 AND companies.monitoring_status = 'watch_ready'`
== old `monitoring_status = 'watching'` set).

## 6. Tracker smoke (no visible change)

```bash
streamlit run tracker.py
```

Feed, status change, application view, contact edit, interaction add/delete,
company status change (incl. blacklist), monitored toggle, dashboard, settings
— all behave exactly as today. No mockup needed (non-goal: no UI change).
