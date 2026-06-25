"""tracker_views/companies.py — Companies list view."""
from datetime import datetime

import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db,
    load_companies, md_link,
    company_status_badge, monitoring_badge, monitoring_status_badge,
    COMPANY_STATUSES, COUNTRY_OPTIONS, COUNTRY_FLAG, SECTOR_LABELS, sector_label,
)
from tracker_views.forms import add_company_dialog

_LAST_IX_DAYS = {"Within 1 week": 7, "Within 1 month": 30, "Within 3 months": 90}

_SORTERS = {
    "Name (A-Z)": lambda c: (c.get("name") or "").lower(),
    "Job count (desc)": lambda c: -(c.get("job_count") or 0),
    "Contact count (desc)": lambda c: -(c.get("contact_count") or 0),
    "Last interaction (recent first)": lambda c: -(_parse_dt(c.get("last_interaction_at"))),
    "Status": lambda c: c.get("status") or "",
}


def _parse_dt(val) -> int:
    """Return epoch seconds, or 0 if None/unparseable."""
    if not val:
        return 0
    try:
        return int(datetime.fromisoformat(str(val)).timestamp())
    except (ValueError, TypeError):
        return 0


def _render_list():
    st.title("🏢 Companies")

    # ── Sidebar filters ──────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🔍 Filters")
        exclude_bl = st.checkbox("Exclude blacklisted", value=True, key="co_exclude_bl")
        status_filter = st.multiselect(
            "Status", COMPANY_STATUSES, key="co_status",
            default=[s for s in COMPANY_STATUSES if s != "blacklisted"],
        )
        search = st.text_input("Search by name", key="co_search")

        st.markdown("---")
        st.markdown("**Geography & industry**")
        country_filter = st.multiselect(
            "Country", COUNTRY_OPTIONS + ["unknown"], key="co_country",
            format_func=lambda c: f"{COUNTRY_FLAG.get(c, '🌐')} {c}",
        )
        sector_filter = st.multiselect(
            "Sector", list(SECTOR_LABELS.values()), key="co_sector",
            format_func=lambda c: sector_label(c),
        )
        size_filter = st.multiselect(
            "Size", ["startup", "sme", "scaleup", "large", "unknown"],
            key="co_size",
        )

        st.markdown("---")
        st.markdown("**Activity**")
        min_jobs = st.number_input(
            "Min jobs at this company", min_value=0, max_value=50,
            value=0, step=1, key="co_min_jobs",
        )
        last_ix_choice = st.selectbox(
            "Last interaction",
            ["Any", "Within 1 week", "Within 1 month",
             "Within 3 months", "Never interacted"],
            key="co_last_ix",
        )
        sort_by = st.selectbox(
            "Sort by",
            ["Name (A-Z)", "Job count (desc)", "Contact count (desc)",
             "Last interaction (recent first)", "Status"],
            key="co_sort",
        )

    st.markdown("---")
    st.markdown("**Monitoring**")
    mon_status = st.radio(
        "Monitoring status",
        ["📡 Any monitored", "All", "🔍 To research", "⏸ Ready", "✅ Watching"],
        key="co_mon_status",
    )

    last_ix_days = _LAST_IX_DAYS.get(last_ix_choice)
    only_never = (last_ix_choice == "Never interacted")

    companies = load_companies(
        status=status_filter if status_filter else None,
        search=search if search else None,
        exclude_blacklisted=exclude_bl,
        countries=tuple(country_filter),
        sectors=tuple(sector_filter),
        sizes=tuple(size_filter),
        min_job_count=int(min_jobs) if min_jobs else None,
        last_interaction_within_days=last_ix_days,
    )

    # Filter by monitoring_status in-memory
    mon_status_map = {
        "🔍 To research": "watch_pending",
        "⏸ Ready":      "watch_ready",
        "✅ Watching":   "watching",
    }
    if mon_status in mon_status_map:
        companies = [c for c in companies
                     if c.get("monitoring_status") == mon_status_map[mon_status]]
    elif mon_status == "📡 Any monitored":
        companies = [c for c in companies
                     if c.get("monitoring_status") in ("watch_pending", "watch_ready", "watching")]

    if only_never:
        companies = [c for c in companies if not c.get("last_interaction_at")]

    companies.sort(key=_SORTERS[sort_by])

    # Header
    c1, c2 = st.columns([3, 1])
    with c1:
        total_jobs = sum(c.get("job_count", 0) for c in companies)
        st.caption(f"{len(companies)} companies · {total_jobs} open jobs across them")
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
            c1, c2 = st.columns([5, 3])
            with c1:
                st.markdown(md_link(f"**{c['name']}**",
                                    f"/company_detail?id={c['id']}"))
                badge = monitoring_badge(c)
                if badge:
                    st.html(badge)
                mon_badge = monitoring_status_badge(c)
                st.markdown(mon_badge, unsafe_allow_html=True)
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
                summary = (c.get("summary") or "").strip()
                if summary:
                    st.caption(summary[:160] + "…" if len(summary) > 160 else summary)
                last = (c.get("last_interaction_at") or "")[:10]
                if last:
                    st.caption(f"Last interaction: {last}")


def render():
    ensure_db()
    _render_list()


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
