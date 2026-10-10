# Removed files (roadmap step 1d, spec 035)

Dead files removed from the repository root on 2026-10-10. Each is still retrievable
from git history with the command in the last column.

| File | Purpose | Last commit | Retrieve |
|---|---|---|---|
| `migrate_expired_status.py` | One-shot migration, already executed (reclassify `archived` → `expired`) | `ba728df` | `git show ba728df:migrate_expired_status.py` |
| `migrate_single_status.py` | One-shot migration, already executed (`application_status` → `status`) | `ba728df` | `git show ba728df:migrate_single_status.py` |
| `migrate_profile_independent_tracking.py` | One-shot migration, already executed (status/notes → `job_tracking`) | `ba728df` | `git show ba728df:migrate_profile_independent_tracking.py` |
| `tracker_legacy.py` | Original single-page Streamlit tracker, superseded by `tracker.py` | `ba728df` | `git show ba728df:tracker_legacy.py` |
| `test_wellfound.py` | Ad-hoc live-scrape test at the root (never collected by pytest) | `8715e10` | `git show 8715e10:test_wellfound.py` |
| `CONTEXT.md` | Stale project reference doc, superseded by `CLAUDE.md` + constitution + `docs/roadmap-api.md` | `33871df` | `git show 33871df:CONTEXT.md` |
