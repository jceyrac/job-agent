# Tasks 005 — Monitoring Status UI + CSV Import

## Phase 0 — DB migration
- [ ] Read `storage.py` — identify migration pattern
- [ ] Add `x_handle TEXT` column to `companies`
- [ ] Add `import_companies_from_csv(rows)` to `JobStorage`
  - case-insensitive duplicate check on `name`
  - sets `monitoring_status = 'watch_pending'` for all new rows
  - returns `{"imported": int, "skipped": list[str]}`
- [ ] Update `get_monitored_companies()` to filter on `monitoring_status = 'watching'`
- [ ] Verify migration on existing DB (no data loss)

## Phase 1 — Status badge in companies list
- [ ] Read `tracker_views/companies.py`
- [ ] Add "Monitoring" column to company list table
- [ ] Render badge per status: ⬜ / 🔍 / ⏸ / ✅ with color via st.markdown

## Phase 2 — Status controls in company detail
- [ ] Read `tracker_views/company_detail.py`
- [ ] Add "Monitoring" section (expander or tab)
- [ ] Render current status + correct action buttons per status:
  - `unmonitored` → "👁 Watch this company"
  - `watch_pending` → "🔍 Research now" + "✖ Stop watching"
  - `watch_ready` → "▶ Activate" + "✖ Stop watching" + show ATS/method fields
  - `watching` → "⏸ Pause" + "🔍 Re-research" + show careers_url/last scraped
- [ ] "Research now" button: call `research_company()`, show `st.spinner`, display result
- [ ] All status buttons call `db.set_monitoring_status()` + `st.rerun()`

## Phase 3 — CSV import in Settings
- [ ] Read Settings view — understand existing structure
- [ ] Add "Company Monitoring" section
- [ ] `st.file_uploader` accepting CSV files
- [ ] Parse with `csv.DictReader` (stdlib only)
- [ ] Map CSV columns to `companies` fields (per spec)
- [ ] Call `db.import_companies_from_csv(rows)` on submit
- [ ] Show summary: "✅ N imported, ⚠️ M skipped: [names]"
- [ ] "Research all pending" button with `st.progress` + result table

## Phase 4 — Cron filter update
- [ ] Read `scrape.py` `_run_monitored_only()`
- [ ] Update company fetch to use `monitoring_status = 'watching'`
- [ ] Test `python scrape.py --monitored-only` — only `watching` companies scraped

## Phase 5 — End-to-end test
- [ ] Import Google Sheet CSV via Settings → companies land at `watch_pending`
- [ ] "Research all pending" → statuses transition correctly
- [ ] Activate one company → `watching`
- [ ] `python scrape.py --monitored-only` → only active company scraped
- [ ] Jobs in tracker have `monitored_company_id` set
- [ ] No regressions on existing Companies view, Settings, or broad scrape
