# Plan 007 — Company Edit Form

## Single phase — two files only

This is a small, focused change. No DB migration (all columns already exist),
no new pages, no new shared helpers.

### Step 1 — storage.py

Add `update_company_fields(company_id, fields)` method as specified.
Read `storage.py` to find the right place to insert it (near other company
update methods like `set_company_status`).
Verify the ALLOWED set matches actual column names in the companies table schema.

### Step 2 — company_detail.py

Read `company_detail.py` in full before editing.

1. Add `SIZE_OPTIONS` and `METHOD_OPTIONS` constants at module level.
2. Add "✏️ Edit" button below meta chips in `_render_detail()`, guarded by
   `st.session_state.get(f"edit_company_{company_id}", False)`.
3. If edit mode active: render `st.form` with all fields pre-populated from
   `company` dict. On submit: call `db.update_company_fields()`, clear cache,
   exit edit mode, rerun. On cancel: exit edit mode, rerun.
4. If read mode: render existing header as-is (no change to current layout).
5. The Monitoring section, divider, and tabs render identically in both modes.

### Step 3 — Test

- Open a company detail page → "✏️ Edit" button visible
- Click Edit → form appears with pre-populated values
- Change website URL → Save → read mode shows updated URL
- Click Edit → Cancel → no changes, back to read mode
- Verify DB updated: `sqlite3 data/jobs.db "SELECT website FROM companies WHERE id = N"`
- Verify other fields (monitoring section, tabs) unaffected in both modes
