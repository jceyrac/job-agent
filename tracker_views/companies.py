"""tracker_views/companies.py — Companies list view."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db,
    load_companies, md_link,
    company_status_badge,
    COMPANY_STATUSES, COUNTRY_FLAG, sector_label,
)
from tracker_views.forms import add_company_dialog


def _render_list():
    st.title("🏢 Companies")

    # Sidebar
    with st.sidebar:
        st.markdown("### 🔍 Filters")
        exclude_bl = st.checkbox("Exclude blacklisted", value=True, key="co_exclude_bl")
        status_filter = st.multiselect(
            "Status", COMPANY_STATUSES, key="co_status",
            default=[s for s in COMPANY_STATUSES if s != "blacklisted"],
        )
        search = st.text_input("Search by name", key="co_search")

    # Load
    companies = load_companies(
        status=status_filter if status_filter else None,
        search=search if search else None,
        exclude_blacklisted=exclude_bl,
    )

    # Header
    c1, c2 = st.columns([3, 1])
    with c1:
        st.caption(f"{len(companies)} companies")
    with c2:
        if st.button("➕ Add Company", use_container_width=True):
            add_company_dialog()

    if not companies:
        st.info("No companies match the current filters.")
        return

    # Bulk action
    with st.expander("Bulk status change", expanded=False):
        selected_names = st.multiselect(
            "Select companies", [c["name"] for c in companies], key="co_bulk"
        )
        new_status = st.selectbox("Change status to", COMPANY_STATUSES, key="co_bulk_status")
        if st.button("Apply bulk status change") and selected_names:
            db = get_db()
            count = 0
            for c in companies:
                if c["name"] in selected_names and c["status"] != new_status:
                    db.set_company_status(c["id"], new_status)
                    count += 1
            st.success(f"Updated {count} companies to '{new_status}'.")
            st.cache_data.clear()
            st.rerun()

    # Table
    for c in companies:
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 3, 1])
            with c1:
                st.markdown(f"**{c['name']}**")
                st.caption(
                    f"{company_status_badge(c['status'])} | "
                    f"💼 {c['job_count']} jobs | 👥 {c['contact_count']} contacts"
                )
            with c2:
                meta = []
                country = c.get("company_country") or ""
                if country and country != "unknown":
                    meta.append(f"{COUNTRY_FLAG.get(country, '🌐')} {country}")
                sector = c.get("industry_sector") or ""
                if sector and sector != "other":
                    meta.append(sector_label(sector))
                size = c.get("company_size") or ""
                if size and size != "unknown":
                    meta.append(size)
                st.caption(" · ".join(meta) if meta else "")
                last = (c.get("last_interaction_at") or "")[:10]
                if last:
                    st.caption(f"Last interaction: {last}")
            with c3:
                st.markdown(md_link("View", f"/company_detail?id={c['id']}"))


def render():
    ensure_db()
    _render_list()


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
