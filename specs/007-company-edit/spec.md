# Spec 007 — Company Edit Form

## Context

Company metadata (website, careers URL, sector, location, ATS fields, etc.)
currently cannot be edited from the Streamlit UI. Errors discovered during
CSV export (e.g. wrong website URL) require direct DB manipulation.

This spec adds an "Edit" mode to `company_detail.py` — a toggle that turns the
read-only header section into an editable form, saves on submit, and returns
to read mode.

---

## Goal

Add an inline edit form to the company detail page that allows the user to
correct any company metadata field without leaving the UI. Changes are written
to the DB immediately and reflected in `data/companies.json` via `export_seed.py`
at the next deploy.

---

## Editable fields

All fields are on the `companies` table. Group them into two sections for clarity:

### Section 1 — Identity
| Field | Widget | DB column |
|---|---|---|
| Name | `text_input` | `name` |
| Website | `text_input` | `website` |
| Location | `text_input` | `hq_location` (or `company_country`) |
| Sector / Field | `text_input` | `industry_sector` |
| Company size | `selectbox` | `company_size` — options: startup, scaleup, sme, large, unknown |
| X account | `text_input` | `x_handle` |

### Section 2 — Monitoring config
| Field | Widget | DB column |
|---|---|---|
| Careers URL | `text_input` | `careers_url` |
| ATS provider | `text_input` | `ats_provider` |
| ATS board slug | `text_input` | `ats_board_slug` |
| ATS board URL | `text_input` | `ats_board_url` |
| Scraping method | `selectbox` | `scraping_method` — options: greenhouse, lever, workable, ashby, teamtailor, jobspy, custom_html, manual, none |
| Research notes | `text_area` | `research_notes` |

Notes field (already in tab 4) is NOT duplicated here — keep it in its tab.

---

## UI pattern — inline toggle edit mode

Use a session state flag `edit_company_{id}` to toggle between read and edit mode.

### Read mode (default)

Current layout is unchanged. Add a small "✏️ Edit" button in the header area,
next to the company name or below the meta chips:

```python
if st.button("✏️ Edit", key=f"edit_btn_{company_id}"):
    st.session_state[f"edit_company_{company_id}"] = True
    st.rerun()
```

### Edit mode

Replace the header section (name, meta chips, website link) with a `st.form`:

```python
with st.form(key=f"edit_form_{company_id}"):
    st.subheader("✏️ Edit company")

    st.markdown("**Identity**")
    name = st.text_input("Name", value=company["name"])
    website = st.text_input("Website", value=company.get("website") or "")
    location = st.text_input("Location", value=company.get("hq_location") or "")
    sector = st.text_input("Sector / Field", value=company.get("industry_sector") or "")
    size = st.selectbox("Company size", SIZE_OPTIONS,
                        index=SIZE_OPTIONS.index(company.get("company_size") or "unknown"))
    x_handle = st.text_input("X account", value=company.get("x_handle") or "")

    st.markdown("**Monitoring config**")
    careers_url = st.text_input("Careers URL", value=company.get("careers_url") or "")
    ats_provider = st.text_input("ATS provider", value=company.get("ats_provider") or "")
    ats_slug = st.text_input("ATS board slug", value=company.get("ats_board_slug") or "")
    ats_board_url = st.text_input("ATS board URL", value=company.get("ats_board_url") or "")
    scraping_method = st.selectbox("Scraping method", METHOD_OPTIONS,
                                   index=METHOD_OPTIONS.index(
                                       company.get("scraping_method") or "none"))
    research_notes = st.text_area("Research notes",
                                  value=company.get("research_notes") or "", height=80)

    col_save, col_cancel = st.columns(2)
    with col_save:
        submitted = st.form_submit_button("💾 Save", use_container_width=True)
    with col_cancel:
        cancelled = st.form_submit_button("✖ Cancel", use_container_width=True)

if submitted:
    db.update_company_fields(company_id, {
        "name": name.strip(),
        "website": website.strip() or None,
        "hq_location": location.strip() or None,
        "industry_sector": sector.strip() or None,
        "company_size": size,
        "x_handle": x_handle.strip() or None,
        "careers_url": careers_url.strip() or None,
        "ats_provider": ats_provider.strip() or None,
        "ats_board_slug": ats_slug.strip() or None,
        "ats_board_url": ats_board_url.strip() or None,
        "scraping_method": scraping_method,
        "research_notes": research_notes.strip() or None,
    })
    st.session_state[f"edit_company_{company_id}"] = False
    st.cache_data.clear()
    st.success("Company updated.")
    st.rerun()

if cancelled:
    st.session_state[f"edit_company_{company_id}"] = False
    st.rerun()
```

The rest of the page (Monitoring section, tabs) renders below the form in both
read and edit mode — no change needed there.

---

## Changes to `storage.py`

Add one method:

```python
def update_company_fields(self, company_id: int, fields: dict) -> None:
    """
    Update arbitrary company fields by column name.
    Only columns present in `fields` are updated — others untouched.
    Raises ValueError if an unknown column name is passed.
    """
    ALLOWED = {
        "name", "website", "hq_location", "industry_sector", "company_size",
        "x_handle", "careers_url", "ats_provider", "ats_board_slug",
        "ats_board_url", "scraping_method", "research_notes",
    }
    invalid = set(fields) - ALLOWED
    if invalid:
        raise ValueError(f"Unknown company fields: {invalid}")
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [company_id]
    with self._conn() as conn:
        conn.execute(f"UPDATE companies SET {cols} WHERE id = ?", vals)
```

---

## Files to modify

- **`tracker_views/company_detail.py`** — add edit toggle + form in `_render_detail()`
- **`storage.py`** — add `update_company_fields()` method

No other files. No new pages. No changes to shared.py, forms.py, or any scraper.

---

## Non-goals

- No inline field editing (click-to-edit per cell) — full form only
- No history/audit log of changes
- No validation beyond stripping whitespace (URLs not validated)
- No automatic `export_seed.py` call after save (user runs manually before deploy)
- Do not add edit capability to the Companies list view
