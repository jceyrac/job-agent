# Plan 005b — Research Run Output & Summary + Companies Filter Update

## Phase 1 — settings.py (research run output)

1. Read `tracker_views/settings.py` — locate `_render_company_monitoring()` and
   the research button block.
2. Add `_research_outcome_icon(result) -> str` helper.
3. Add `_render_research_summary(results)` helper — builds list of dicts,
   renders `st.dataframe` + aggregate count line.
4. Replace silent loop with `st.status` expanded block (live per-company output).
5. Call `_render_research_summary(results)` after the block closes.
6. Quick test: run on 2-3 companies, verify live lines and summary table appear.

## Phase 2 — companies.py (filter update)

1. Read `tracker_views/companies.py` (or wherever the monitoring filter is
   implemented) — understand current filter options and how they map to DB queries
   or in-memory list filtering.
2. Replace current filter options with:
   - All
   - 🔍 To research (`watch_pending`)
   - ⏸ Ready (`watch_ready`)
   - ✅ Watching (`watching`)
   - 📡 Any monitored (`watch_pending + watch_ready + watching`) — set as default
3. Remove "Monitorable (not yet monitored)" option.
4. Update the filter logic to match companies against the selected status value(s).
5. Test: import CSV → companies appear under "🔍 To research" and "📡 Any monitored".
   Verify "✅ Watching" only shows active companies.
