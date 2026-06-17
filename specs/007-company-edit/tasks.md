# Tasks 007 — Company Edit Form

## storage.py
- [x] Read `storage.py` — locate company update methods (near `set_company_status`)
- [x] Verify ALLOWED column names match actual `companies` table schema
- [x] Add `update_company_fields(company_id: int, fields: dict) -> None`:
  - [x] ALLOWED set validation — raise ValueError on unknown column
  - [x] Dynamic `UPDATE companies SET col = ? WHERE id = ?`
  - [x] Handle empty fields dict (no-op)

## company_detail.py
- [x] Read `company_detail.py` in full
- [x] Add module-level constants:
  - [x] `SIZE_OPTIONS = ["startup", "scaleup", "sme", "large", "unknown"]`
  - [x] `METHOD_OPTIONS = ["greenhouse", "lever", "workable", "ashby", "teamtailor", "jobspy", "custom_html", "manual", "none"]`
- [x] Add "✏️ Edit" button below meta chips in `_render_detail()`:
  - [x] Guarded by `not st.session_state.get(f"edit_company_{company_id}", False)`
  - [x] On click: set session state flag + `st.rerun()`
- [x] Add edit form block (when session state flag is True):
  - [x] `st.form(key=f"edit_form_{company_id}")`
  - [x] Section 1 — Identity: name, website, location, sector, size, x_handle
  - [x] Section 2 — Monitoring config: careers_url, ats_provider, ats_board_slug,
        ats_board_url, scraping_method, research_notes
  - [x] Two submit buttons: "💾 Save" and "✖ Cancel" in columns
  - [x] On Save: call `db.update_company_fields()`, clear session state + cache, rerun
  - [x] On Cancel: clear session state flag, rerun
- [x] Verify: rest of page (Monitoring section, tabs) unchanged in both modes

## Tests
- [x] "✏️ Edit" button visible on company detail page in read mode
- [x] Click Edit → form renders with correct pre-populated values
- [x] Edit website URL → Save → read mode shows new URL ✅
- [x] Edit → Cancel → values unchanged ✅
- [x] DB check: `sqlite3 data/jobs.db "SELECT website, careers_url FROM companies WHERE id = N"` ✅
- [x] Monitoring section (watch_pending / watch_ready / watching controls) unaffected ✅
- [x] Jobs, Contacts, Interactions, Notes tabs unaffected ✅
- [x] No crash on company with NULL fields (all inputs handle None → empty string) ✅
