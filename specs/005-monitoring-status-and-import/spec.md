# Spec 005 — Monitoring Status UI + CSV Import

## Goal

Two related additions that build on spec 004's `monitoring_status` column:

1. **Monitoring status controls in the UI** — Companies view and company detail page
   expose the status lifecycle (`unmonitored` → `watch_pending` → `watch_ready` →
   `watching`) with appropriate actions at each stage.

2. **CSV import in Settings** — a new "Monitoring" section in Settings lets the user
   upload a CSV of companies to watch. Imported companies land at `watch_pending`
   automatically. Duplicate detection prevents double-imports.

This spec depends on spec 004 being fully implemented (DB columns + researcher logic
must exist before building the UI on top).

---

## Prerequisites (from spec 004)

- `companies.monitoring_status` column exists with enum values:
  `unmonitored | watch_pending | watch_ready | watching`
- `storage.set_monitoring_status(company_id, status)` exists
- `storage.get_watch_pending_companies()` exists
- `company_researcher.research_company()` callable from Python

---

## Part 1 — Monitoring status in Companies view

### Status badge

In the company list table, add a "Monitoring" column showing a colored badge:

| Status | Badge | Color |
|---|---|---|
| `unmonitored` | ⬜ Not watched | grey |
| `watch_pending` | 🔍 To research | blue |
| `watch_ready` | ⏸ Ready | orange |
| `watching` | ✅ Active | green |

### Actions per status (in company detail page)

**`unmonitored`**
- Button: "👁 Watch this company" → sets status to `watch_pending`, reruns

**`watch_pending`**
- Button: "🔍 Research now" → calls `research_company()` inline, shows spinner,
  displays result, transitions to `watch_ready` if ATS found
- Shows `research_notes` if already researched but nothing found
- Button: "✖ Stop watching" → sets status back to `unmonitored`

**`watch_ready`**
- Shows: ATS provider, board slug, scraping method, research confidence
- Button: "▶ Activate monitoring" → sets status to `watching`
- Button: "✖ Stop watching" → sets status to `unmonitored`

**`watching`**
- Shows: ATS provider, careers URL, last scraped (from `run_logs` or `last_seen`)
- Button: "⏸ Pause monitoring" → sets status to `watch_ready`
- Button: "🔍 Re-research" → re-runs researcher, updates fields

### Cron filter (scrape.py)

The `--monitored-only` flag already filters on `monitored = true` in the DB.
After spec 004, this must be updated to filter on `monitoring_status = 'watching'`
instead. Read `scrape.py` `_run_monitored_only()` and update the
`db.get_monitored_companies()` query accordingly.

---

## Part 2 — CSV import in Settings

### Location in UI

Settings page → new tab or section: **"Company Monitoring"**

(Keep existing Settings sections intact — add a new expander or tab.)

### CSV import widget

```
st.file_uploader("Import companies to watch (CSV)", type=["csv"])
```

Expected CSV columns (must match the Google Sheet export format):
```
Name, Location, Field, Website, Career website, ATS, Monitored?, Contacts, X account, Comment
```

Only `Name` is required. All other columns are optional and mapped to `companies` fields
if present:

| CSV column | companies field |
|---|---|
| Name | name |
| Location | hq_location (or notes) |
| Field | sector (free text) |
| Website | website |
| Career website | careers_url |
| ATS | ats_provider (free text, not yet validated) |
| X account | x_handle (new column — see below) |
| Comment | notes |

`monitoring_status` is set to `watch_pending` for all imported rows.

### Duplicate detection

Before inserting, check if a company with the same `name` (case-insensitive) already
exists in `companies`. Skip duplicates silently and report a count:

```
✅ 18 companies imported
⚠️  6 already existed — skipped (Dfinity, 21Shares, Sygnum Bank, ...)
```

### DB changes required by Part 2

New column on `companies`:
```sql
ALTER TABLE companies ADD COLUMN x_handle TEXT;
```

New method on `JobStorage`:
```python
def import_companies_from_csv(self, rows: list[dict]) -> dict:
    """
    rows: list of dicts parsed from CSV (keys = CSV column names).
    Returns {"imported": int, "skipped": list[str]}.
    Sets monitoring_status = 'watch_pending' for all new rows.
    """
```

### Post-import UX

After import, show a summary and a button:
```
✅ 18 companies imported with status "To research"
[🔍 Research all pending companies]
```

Clicking the button calls `research_all_pending(db)` with a progress bar
(`st.progress` + `st.status`). On completion, shows a summary table of results.

---

## Files to create/modify

- **Modify** `tracker_views/companies.py` — add status badge column + status actions
- **Modify** `tracker_views/company_detail.py` — add status section with action buttons
- **Modify** `tracker_views/settings.py` (or equivalent) — add "Company Monitoring" section
- **Modify** `storage.py` — add `x_handle` column, `import_companies_from_csv()` method,
  update `get_monitored_companies()` to filter on `monitoring_status = 'watching'`
- **Modify** `scrape.py` — update `_run_monitored_only()` to use `monitoring_status = 'watching'`
- Do NOT create new tracker_views files
- Do NOT modify `company_researcher.py` (read-only from this spec)
- Do NOT modify any scraper

---

## Non-goals

- No bulk status change UI (change one company at a time)
- No CSV export of companies from the UI
- No validation of ATS provider values from CSV (stored as-is, researcher validates later)
- No scheduling of the research run (manual trigger only for now)
