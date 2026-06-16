# Spec 005a — Settings Monitoring Section Consolidation

## Context

After implementing spec 005, the Settings page has two overlapping monitoring
sections rendered in `tracker_views/settings.py`:

1. `_render_monitored_companies()` → **"🎯 Monitored Companies"**
   - Pipeline toggle (include monitoring in full pipeline run)
   - `➕ Add a company to monitor` expander (ATS detection form)
   - List of monitorable companies with active/paused toggles

2. `_render_company_monitoring()` → **"📡 Company Monitoring"**
   - CSV import widget
   - "Research all pending" button

These two sections are confusing and redundant. This amendment merges them into
a single **"📡 Company Monitoring"** section with two clearly labelled subsections.

---

## Goal

Replace `_render_monitored_companies()` and `_render_company_monitoring()` with a
single `_render_company_monitoring()` function containing two subsections.

### Subsection 1 — "Monitored companies"
Preserves everything from `_render_monitored_companies()` **except**:
- Remove the `➕ Add a company to monitor` expander entirely.
  Adding a company is already covered by `add_company_dialog()` in the Companies
  view — a third entry point in Settings is redundant and confusing.
- Keep the pipeline toggle (include monitoring in full pipeline run).
- Keep the company list with active/paused toggles.

### Subsection 2 — "Import companies to watch"
Preserves everything from `_render_company_monitoring()` as-is:
- CSV file uploader
- Import button with duplicate detection and summary
- "Research all pending" button with progress bar

---

## Changes to `settings.py`

### 1. `render()` — remove one call

```python
# Before (two separate calls with a divider)
_render_monitored_companies(db)
st.divider()
_render_company_monitoring(db)

# After (single call)
_render_company_monitoring(db)
```

### 2. Delete `_render_monitored_companies()`

Remove the entire function body. Its content is migrated into the new
`_render_company_monitoring()` below.

### 3. Rewrite `_render_company_monitoring()`

New structure — logic is unchanged, only the layout changes:

```python
def _render_company_monitoring(db):
    import csv, io

    st.subheader("📡 Company Monitoring")

    # ── Subsection 1: Monitored companies ─────────────────────────────────
    st.markdown("#### Monitored companies")

    # Pipeline toggle — verbatim from _render_monitored_companies
    # (the checkbox + db.set_config block, no change)
    ...

    # Company list — verbatim from _render_monitored_companies
    # (load_all_monitorable_companies, caption, per-company toggle rows)
    # NOTE: the ➕ Add a company to monitor expander is NOT included here
    ...

    st.divider()

    # ── Subsection 2: Import companies to watch ────────────────────────────
    st.markdown("#### Import companies to watch")

    # CSV uploader — verbatim from current _render_company_monitoring
    ...

    # Research all pending — verbatim from current _render_company_monitoring
    ...
```

---

## Files to modify

- **`tracker_views/settings.py`** only — no other files

---

## Non-goals

- No logic changes — pure UI restructuring
- No DB changes
- No changes to Companies view, company_detail, or any scraper
- Do not change the pipeline toggle behavior or company list behavior
