# Plan 005 — Monitoring Status UI + CSV Import

## Phase 0 — DB migration (storage.py)

Add `x_handle` column to `companies`. Follow existing migration pattern.
Add `import_companies_from_csv(rows)` method.
Update `get_monitored_companies()` to filter `monitoring_status = 'watching'`
instead of `monitored = true` (or whatever the current filter is — read first).

## Phase 1 — Status badge in companies list

Read `tracker_views/companies.py`. Add a "Monitoring" column to the company table.
Render badge as colored `st.markdown` inline HTML or emoji prefix.
No interaction in the list — status changes happen in detail view only.

## Phase 2 — Status controls in company detail

Read `tracker_views/company_detail.py`. Add a "Monitoring" section (expander or
dedicated tab). Render current status + appropriate action buttons per status.
Wire "Research now" button to call `research_company()` with `st.spinner`.
Wire status transition buttons to `db.set_monitoring_status()` + `st.rerun()`.

## Phase 3 — CSV import in Settings

Read the Settings view to understand existing tab/section structure.
Add "Company Monitoring" section with `st.file_uploader`.
Parse CSV with `csv.DictReader` (stdlib, no pandas).
Call `db.import_companies_from_csv(rows)` on submit.
Show import summary (imported count + skipped names).
Add "Research all pending" button that calls `research_all_pending(db)` with
`st.progress` and result table.

## Phase 4 — Cron filter update (scrape.py)

Read `_run_monitored_only()` in `scrape.py`.
Update the company fetch to use `monitoring_status = 'watching'`.
Verify `--monitored-only` still works correctly end-to-end.

## Phase 5 — End-to-end test

1. Import the Google Sheet CSV via Settings → 18+ companies land at `watch_pending`
2. Click "Research all pending" → researcher runs, statuses transition
3. Activate one company manually → `watching`
4. Run `python scrape.py --monitored-only` → only `watching` company scraped
5. Verify jobs appear in tracker with `monitored_company_id` set
