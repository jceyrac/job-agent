# Tasks 005b — Research Run Output & Summary + Companies Filter Update

## Phase 1 — settings.py
- [x] Read `tracker_views/settings.py` — locate research button block
- [x] Add `_research_outcome_icon(result) -> str`:
  - ✅ greenhouse / lever / workable / ashby
  - 🟡 custom_html
  - 🔵 jobspy
  - ❌ none
  - ⚠️ manual / unknown
- [x] Add `_render_research_summary(results)`:
  - Build list of dicts from results
  - `st.dataframe` with Company, ATS, Method, Confidence, Notes columns
  - Aggregate count line below
- [x] Replace research button block with `st.status` expanded block
- [x] Call `_render_research_summary(results)` after block closes
- [x] Test: live lines visible, summary table renders, no crash on None fields

## Phase 2 — companies.py
- [x] Read companies view — locate monitoring status filter implementation
- [x] Replace filter options with 5 new options:
  - [x] All
  - [x] 🔍 To research → `watch_pending`
  - [x] ⏸ Ready → `watch_ready`
  - [x] ✅ Watching → `watching`
  - [x] 📡 Any monitored → `watch_pending + watch_ready + watching` (default)
- [x] Remove "Monitorable (not yet monitored)" option
- [x] Update filter logic to match new status values
- [x] Test: CSV-imported companies visible under "🔍 To research" and "📡 Any monitored"
- [x] Test: "✅ Watching" shows only active companies
- [x] Test: "All" shows everything including `unmonitored`
