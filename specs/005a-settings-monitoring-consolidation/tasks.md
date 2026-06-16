# Tasks 005a — Settings Monitoring Section Consolidation

## Single phase
- [x] Read `tracker_views/settings.py` in full
- [x] In `render()`: remove `_render_monitored_companies(db)` call + preceding `st.divider()`
- [x] Rewrite `_render_company_monitoring()`:
  - [x] Add `st.markdown("#### Monitored companies")` at top
  - [x] Copy pipeline toggle from `_render_monitored_companies()` verbatim
  - [x] Copy company list (load + caption + per-company rows) verbatim
  - [x] Do NOT include the `➕ Add a company to monitor` expander
  - [x] Add `st.divider()` between subsections
  - [x] Add `st.markdown("#### Import companies to watch")`
  - [x] Keep CSV uploader + research block unchanged
- [x] Delete `_render_monitored_companies()` entirely
- [x] Launch Streamlit locally — verify:
  - [x] Single "📡 Company Monitoring" section visible
  - [x] No "🎯 Monitored Companies" section
  - [x] No "➕ Add a company to monitor" expander
  - [x] Pipeline toggle works
  - [x] Company active/paused toggles work
  - [x] CSV import works
  - [x] "Research all pending" button works
