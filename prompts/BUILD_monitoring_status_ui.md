# BUILD — Company Monitoring Status UI

> Spec for Claude Code. Read `storage.py` (companies section + `_migrate_monitored_companies`),
> `scrapers/base.py`, and `tracker_views/{shared,companies,company_detail,settings}.py`
> before starting.
> Surgical edits only — no new dependencies, no schema changes (all required columns
> already exist: `companies.ats_provider`, `ats_identifier`, `scraper_id`, `monitored`).

---

## Background / bug being fixed

`tracker_views/settings.py::_render_monitored_companies()` currently iterates
`db.get_monitored_companies()`, which only returns rows where `monitored = TRUE`.
The "⏸ Pause / ▶ Resume" button toggles `monitored`, but as soon as a company is
paused (`monitored` → 0), it drops out of the list on the next rerun — so the
"▶ Resume" button is never reachable. From the user's perspective, pausing a
company makes it vanish.

This spec fixes that AND adds monitoring-status visibility/filtering to the
Companies list and detail pages, per the three-state model below.

---

## Three-state monitoring model

For any company, derive a monitoring state from `ats_provider` / `scraper_id`
(the "monitoring source") and whether that source's scraper is currently enabled
(`BaseScraper.is_enabled()` / config key `scraper.<slug>.enabled`):

1. **Not monitorable** — `ats_provider IS NULL AND scraper_id IS NULL`.
   → No toggle shown at all. No badge, or an optional neutral "—" if a badge slot
   is always rendered for layout consistency (prefer: omit entirely).

2. **Monitorable but scraper disabled** — `ats_provider` or `scraper_id` is set,
   but the corresponding scraper is disabled via
   `db.get_config(f"scraper.{slug}.enabled")` (where `slug` is the lowercased
   provider/scraper name, `.` and `:` replaced with `_`, matching
   `BaseScraper._config_prefix()`).
   → Toggle shown but **disabled** (`st.toggle(..., disabled=True)`), with a
   caption/tooltip: `"Enable the {Provider} scraper in Settings to monitor this company."`
   → Badge: ⚪ `Monitorable · {provider}` (greyed, e.g. via caption styling).

3. **Monitorable and scraper enabled** — same source check, but scraper enabled.
   → Toggle shown, interactive, bound to `companies.monitored`.
   → Badge: 🟢 `Monitored · {provider}` if `monitored=1`, or ⚪ `Monitorable · {provider}`
     if `monitored=0`.

Provider display name: title-case `ats_provider` (e.g. `greenhouse` → `Greenhouse`).
If only `scraper_id` is set (no `ats_provider`), use `scraper_id` similarly.

---

## 1. `storage.py` — new methods

Add near the existing monitored-companies section (after `get_monitored_companies`).
Do not modify existing methods.

```python
def get_all_monitorable_companies(self) -> list[dict]:
    """Return all companies that have a monitoring source (ats_provider or
    scraper_id set), regardless of current monitored state. Used by Settings
    so paused companies remain visible (fixes pause/resume disappearing bug)."""
    with self._conn() as conn:
        rows = conn.execute(
            """SELECT id, name, ats_provider, ats_identifier, scraper_id,
                      careers_url, monitored, detected_at
               FROM companies
               WHERE ats_provider IS NOT NULL OR scraper_id IS NOT NULL
               ORDER BY name"""
        ).fetchall()
    return [dict(r) for r in rows]
```

Extend `get_companies()` signature with two new optional kwargs, applied as
additional WHERE clauses (follow the existing pattern of appending to `clauses`/`params`):

```python
monitored: bool | None = None,
monitorable: bool | None = None,
```

- `monitored=True` → `clauses.append("c.monitored = 1")`
- `monitored=False` → `clauses.append("(c.monitored IS NULL OR c.monitored = 0)")`
- `monitorable=True` → `clauses.append("(c.ats_provider IS NOT NULL OR c.scraper_id IS NOT NULL)")`
- `monitorable=False` → `clauses.append("(c.ats_provider IS NULL AND c.scraper_id IS NULL)")`

Both `None` by default → no-op, preserving existing callers.

---

## 2. `scrapers/base.py` — no code change, but note the contract

`BaseScraper.is_enabled()` requires a `storage` instance and reads
`get_config(self.enabled_config_key())`. For the UI we don't have a scraper
instance per company — we only have a provider/scraper_id string. Add a small
helper to `tracker_views/shared.py` (UI-only, avoids touching scraper modules)
that replicates the slug logic without instantiating a scraper:

```python
# tracker_views/shared.py

def _scraper_slug(name: str) -> str:
    """Mirror BaseScraper._config_prefix() slug logic for a provider/scraper name."""
    return name.lower().replace(" ", "_").replace(":", "_")


def is_monitoring_source_enabled(db, company: dict) -> bool:
    """True if the ATS provider or dedicated scraper for this company is enabled.

    Defaults to True if no config override exists (matches BaseScraper.is_enabled
    default-to-ENABLED behavior) — we assume enabled unless explicitly disabled.
    """
    source = company.get("ats_provider") or company.get("scraper_id")
    if not source:
        return False
    slug = _scraper_slug(source)
    val = db.get_config(f"scraper.{slug}.enabled")
    if val is None:
        return True
    return val.lower() == "true"
```

---

## 3. `tracker_views/shared.py` — badge + loader

Add alongside other badge helpers:

```python
def monitoring_badge(company: dict) -> str:
    """Return a short status string for a company's monitoring state, or '' if
    the company is not monitorable at all."""
    source = company.get("ats_provider") or company.get("scraper_id")
    if not source:
        return ""
    label = source.title()
    if company.get("monitored"):
        return f"🟢 Monitored · {label}"
    return f"⚪ Monitorable · {label}"
```

Add cached loader:

```python
@st.cache_data(ttl=60, show_spinner=False)
def load_all_monitorable_companies() -> list[dict]:
    return get_db().get_all_monitorable_companies()
```

---

## 4. `tracker_views/companies.py` — list view

### Sidebar filters

Add a radio under the existing "Activity" section (or a new "Monitoring"
section). Prefer a radio over two checkboxes — avoids a both-checked edge case:

```python
st.markdown("---")
st.markdown("**Monitoring**")
monitoring_choice = st.radio(
    "Monitoring status",
    ["All", "Monitored only", "Monitorable (not yet monitored)"],
    key="co_monitoring_filter",
    label_visibility="collapsed",
)
```

Map to `get_companies()` kwargs:

```python
if monitoring_choice == "Monitored only":
    monitored_kw, monitorable_kw = True, True
elif monitoring_choice == "Monitorable (not yet monitored)":
    monitored_kw, monitorable_kw = False, True
else:
    monitored_kw, monitorable_kw = None, None
```

Pass `monitored=monitored_kw, monitorable=monitorable_kw` to `load_companies()`.

### Card display

In the per-company card loop, add the monitoring badge to the caption line:

```python
badge = monitoring_badge(c)
caption_parts = [
    company_status_badge(c['status']),
    f"💼 {c['job_count']} jobs",
    f"👥 {c['contact_count']} contacts",
]
if badge:
    caption_parts.append(badge)
st.caption(" | ".join(caption_parts))
```

`monitoring_badge` needs `ats_provider`, `scraper_id`, `monitored` in the row —
confirm `get_companies()`'s SELECT already includes these three columns; if not,
add them to the SELECT in `get_companies()` following the existing
`COALESCE(c.xxx, 'unknown') AS xxx` pattern, but these three don't need COALESCE
since NULL is meaningful.

---

## 5. `tracker_views/company_detail.py` — detail view

After the meta chips block (`st.caption(" · ".join(meta) ...)`), add a monitoring
status line + toggle:

```python
from tracker_views.shared import monitoring_badge, is_monitoring_source_enabled

# ── Monitoring status ──
mon_badge = monitoring_badge(company)
if mon_badge:
    source_enabled = is_monitoring_source_enabled(db, company)
    if source_enabled:
        new_monitored = st.toggle(
            mon_badge, value=bool(company.get("monitored")),
            key=f"detail_mon_toggle_{company_id}",
        )
        if new_monitored != bool(company.get("monitored")):
            db.set_company_monitored(company_id, new_monitored)
            st.cache_data.clear()
            st.rerun()
    else:
        st.toggle(
            mon_badge, value=bool(company.get("monitored")),
            key=f"detail_mon_toggle_{company_id}", disabled=True,
        )
        provider_label = (company.get("ats_provider") or company.get("scraper_id") or "").title()
        st.caption(f"Enable the {provider_label} scraper in Settings to monitor this company.")
# else: not monitorable — render nothing
```

Note: `load_company_by_id()` → `get_company_by_id()` query must include
`ats_provider`, `ats_identifier`, `scraper_id`, `monitored` in its SELECT. Check
current SELECT list in `get_company_by_id()`; add these columns (no COALESCE needed)
if missing.

---

## 6. `tracker_views/settings.py` — fix pause/resume

Replace `_render_monitored_companies()` body:

- Iterate `db.get_all_monitorable_companies()` (via a new cached loader
  `load_all_monitorable_companies()`) instead of `db.get_monitored_companies()`.
  This keeps paused companies visible.
- Replace the Pause/Resume button with `st.toggle`:

```python
for company in load_all_monitorable_companies():
    cid = company["id"]
    name = company["name"]
    provider = company.get("ats_provider") or company.get("scraper_id") or "—"
    identifier = company.get("ats_identifier", "")
    source_enabled = is_monitoring_source_enabled(db, company)

    cols = st.columns([4, 2, 1])
    with cols[0]:
        st.markdown(f"**{name}**  \n`{provider}` → `{identifier}`")
    with cols[1]:
        if source_enabled:
            st.caption("Active" if company.get("monitored") else "Paused")
        else:
            st.caption("⚪ Scraper disabled")
    with cols[2]:
        new_val = st.toggle(
            "Monitor", value=bool(company.get("monitored")),
            key=f"mon_toggle_{cid}", disabled=not source_enabled,
            label_visibility="collapsed",
        )
        if source_enabled and new_val != bool(company.get("monitored")):
            db.set_company_monitored(cid, new_val)
            st.cache_data.clear()
            st.rerun()
```

Keep the existing "➕ Add a company to monitor" expander unchanged.

Update `st.caption(f"{len(monitored)} monitored companies")` → reflects the new
list:
```python
active_count = sum(1 for c in all_monitorable if c["monitored"])
st.caption(f"{len(all_monitorable)} monitorable companies ({active_count} active)")
```

If `all_monitorable` is empty, keep the existing
`st.info(...)` but rename to `"No monitorable companies yet. Add one above!"`.

Import `load_all_monitorable_companies` and `is_monitoring_source_enabled` from
`tracker_views.shared` at the top of `settings.py`.

---

## Explicit non-goals

- No changes to `scrape.py`, `_run_monitored_only`, or the Greenhouse scraper —
  the monitoring *execution* path is already correct and out of scope.
- No new DB columns or migrations — all required fields exist.
- `scraper_id`-based companies are included in the filter/badge logic for
  forward-compatibility, even though `scraper_id` is currently always NULL
  (0 rows as of this spec) — harmless no-op until populated.
- Do not change `COMPANY_STATUSES` or `company_status_badge` — monitoring status
  is orthogonal to company relationship status.

---

## Acceptance criteria

- [ ] A company with no `ats_provider`/`scraper_id` shows no monitoring badge and
      no toggle anywhere (list, detail, settings).
- [ ] A company with `ats_provider='greenhouse'` and `scraper.greenhouse.enabled`
      unset or `"true"`: badge shows 🟢/⚪ correctly, toggle is interactive in
      detail view and Settings.
- [ ] Setting `scraper.greenhouse.enabled = "false"` via existing scraper toggles
      → all Greenhouse companies show a disabled toggle + "Enable the Greenhouse
      scraper..." caption, in both detail view and Settings.
- [ ] In Settings, toggling a company from Active → Paused keeps it visible in
      the list with the toggle now off (no more disappearing row).
- [ ] Toggling Paused → Active works from the same list (regression test for the
      original bug).
- [ ] Companies list sidebar: "All" / "Monitored only" / "Monitorable (not yet
      monitored)" radio filters the list correctly; badge appears on cards.
- [ ] `get_companies()` existing callers (no `monitored`/`monitorable` args) behave
      identically to before.
