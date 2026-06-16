# Spec 005b — Research Run Output & Summary + Companies Filter Update

## Context

After spec 005, two issues were identified during testing:

1. **The "Research all pending" button produces no visible output** — the user has
   no way to know what happened per company, and in particular which companies
   failed or remained unresolved.

2. **`watch_pending` companies are invisible in the Companies view** — the
   "Monitored only" filter only shows `watch_ready` and `watching` companies,
   so companies imported via CSV (which land at `watch_pending`) disappear from
   view until research is complete. The user needs to be able to see and manage
   them.

---

## Part 1 — Research run live output + summary (settings.py)

### Current implementation (to replace)

The research button block in `_render_company_monitoring()` runs silently with
only a progress bar and a final success count.

### New implementation

Replace the silent loop with a `st.status` expanded block for live per-company
output, followed by a structured summary table.

#### Live output block

```python
with st.status("🔍 Researching companies...", expanded=True) as status_block:
    progress = st.progress(0)
    for i, company in enumerate(pending):
        st.write(f"**{company['name']}** — researching...")
        result = research_company(
            company["name"],
            company.get("website") or company.get("careers_url"),
        )
        update_company_from_research(db, company["id"], result)
        results.append((company, result))

        icon = _research_outcome_icon(result)
        st.write(
            f"{icon} **{company['name']}** → "
            f"`{result.ats_provider or 'none'}` / "
            f"`{result.scraping_method}` ({result.confidence})"
            + (f" — {result.notes}" if result.notes else "")
        )
        progress.progress((i + 1) / total)
        time.sleep(0.5)

    status_block.update(label="✅ Research complete", state="complete")

_render_research_summary(results)
st.cache_data.clear()
```

#### Helper: `_research_outcome_icon(result) -> str`

```python
def _research_outcome_icon(result) -> str:
    if result.scraping_method in ("greenhouse", "lever", "workable", "ashby"):
        return "✅"
    if result.scraping_method == "custom_html":
        return "🟡"
    if result.scraping_method == "jobspy":
        return "🔵"
    if result.scraping_method == "none":
        return "❌"
    return "⚠️"  # manual / unknown
```

#### Helper: `_render_research_summary(results)`

Renders a `st.dataframe` with columns: Company, ATS, Method, Confidence, Notes.
Followed by an aggregate count line:

```
✅ 3 resolved  🟡 1 custom HTML  🔵 0 JobSpy  ❌ 2 no page  ⚠️ 1 manual
```

---

## Part 2 — Companies view filter update (companies.py or equivalent)

### Current filter options (from screenshot)

```
Monitoring status
○ All
● Monitored only        ← currently: watch_ready + watching
○ Monitorable (not yet monitored)
```

### Required change

Add `watch_pending` to the "Monitored only" option, OR add a dedicated filter
option. The cleanest approach given the new status model is to replace the
existing options with the four explicit statuses:

```
Monitoring status
○ All
○ 🔍 To research        (watch_pending)
○ ⏸ Ready              (watch_ready)
○ ✅ Watching           (watching)
○ 📡 Any monitored      (watch_pending + watch_ready + watching)
```

"Any monitored" replaces the current "Monitored only" as the default selection
so the user sees everything in the pipeline at a glance.

"Monitorable (not yet monitored)" can be removed — it was a legacy concept from
before the explicit status model. `unmonitored` companies with an ATS are now
surfaced differently (via the researcher).

Read the Companies view carefully to understand how the filter is currently
implemented before modifying it. The filter is session-state only (no DB query
change needed if the existing approach filters a loaded list in Python).

---

## Files to modify

- **`tracker_views/settings.py`** — live output + summary in research button block
- **`tracker_views/companies.py`** (or equivalent) — update monitoring status filter

No DB changes. No changes to `company_researcher.py` or any scraper.

---

## Non-goals

- No persistent log of research runs in the DB
- No re-run of individual failed companies from the UI (manual CLI for now)
- No change to the company list columns or card layout
