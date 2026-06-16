# Plan 005a — Settings Monitoring Section Consolidation

## Single phase — surgical edit to settings.py

This is a pure restructuring — no logic changes, no DB changes.

1. Read `tracker_views/settings.py` in full before touching anything.
2. In `render()`: remove the `_render_monitored_companies(db)` call and the
   `st.divider()` that precedes `_render_company_monitoring(db)`.
3. Rewrite `_render_company_monitoring()`:
   - Add `st.markdown("#### Monitored companies")` at the top
   - Copy the pipeline toggle block from `_render_monitored_companies()` verbatim
   - Copy the company list block from `_render_monitored_companies()` verbatim
   - **Do NOT copy** the `➕ Add a company to monitor` expander
   - Add `st.divider()` between the two subsections
   - Add `st.markdown("#### Import companies to watch")`
   - Keep the existing CSV + research block unchanged
4. Delete `_render_monitored_companies()` entirely.
5. Verify: launch Streamlit locally, confirm single section with two subsections,
   confirm pipeline toggle still works, confirm company toggles still work,
   confirm CSV import still works.
