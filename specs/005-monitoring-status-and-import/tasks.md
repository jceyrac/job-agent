# Tasks 005 — Monitoring Status UI + CSV Import

## Phase 0 — DB migration
- [x] Read `storage.py` — identify migration pattern
- [x] Add `x_handle TEXT` column to `companies`
- [x] Add `import_companies_from_csv(rows)` to `JobStorage`
  - case-insensitive duplicate check on `name`
  - sets `monitoring_status = 'watch_pending'` for all new rows
  - returns `{"imported": int, "skipped": list[str]}`
- [x] Update `get_monitored_companies()` to filter on `monitoring_status = 'watching'`
- [x] Verify migration on existing DB (no data loss)

## Phase 1 — Status badge in companies list
- [x] Read `tracker_views/companies.py`
- [x] Add "Monitoring" column to company list table
- [x] Render badge per status: ⬜ / 🔍 / ⏸ / ✅ with color via st.markdown

## Phase 2 — Status controls in company detail
- [x] Read `tracker_views/company_detail.py`
- [x] Add "Monitoring" section (expander or tab)
- [x] Render current status + correct action buttons per status:
  - `unmonitored` → "👁 Watch this company"
  - `watch_pending` → "🔍 Research now" + "✖ Stop watching"
  - `watch_ready` → "▶ Activate" + "✖ Stop watching" + show ATS/method fields
  - `watching` → "⏸ Pause" + "🔍 Re-research" + show careers_url/last scraped
- [x] "Research now" button: call `research_company()`, show `st.spinner`, display result
- [x] All status buttons call `db.set_monitoring_status()` + `st.rerun()`

## Phase 3 — CSV import in Settings
- [x] Read Settings view — understand existing structure
- [x] Add "Company Monitoring" section
- [x] `st.file_uploader` accepting CSV files
- [x] Parse with `csv.DictReader` (stdlib only)
- [x] Map CSV columns to `companies` fields (per spec)
- [x] Call `db.import_companies_from_csv(rows)` on submit
- [x] Show summary: "✅ N imported, ⚠️ M skipped: [names]"
- [x] "Research all pending" button with `st.progress` + result table

## Phase 4 — Cron filter update
- [x] Read `scrape.py` `_run_monitored_only()`
- [x] Update company fetch to use `monitoring_status = 'watching'`
- [x] Test `python scrape.py --monitored-only` — only `watching` companies scraped

## Phase 5 — End-to-end test
- [x] Import Google Sheet CSV via Settings → companies land at `watch_pending`
- [x] "Research all pending" → statuses transition correctly
- [x] Activate one company → `watching`
- [x] `python scrape.py --monitored-only` → only active company scraped
- [x] Jobs in tracker have `monitored_company_id` set
- [x] No regressions on existing Companies view, Settings, or broad scrape
