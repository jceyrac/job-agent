"""tracker_views/companies.py — Companies list + detail view."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db,
    load_companies, load_contacts, load_jobs,
    score_badge, company_status_badge, relationship_badge,
    COMPANY_STATUSES, COUNTRY_FLAG, sector_label,
    nav_to_entity, clear_detail, get_detail_id,
)
from tracker_views.forms import add_company_dialog, add_contact_dialog, log_interaction_dialog


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
                if st.button("View", key=f"co_view_{c['id']}"):
                    nav_to_entity(c["id"])
                    st.rerun()


def _render_detail(company_id: int):
    db = get_db()

    if st.button("← Back to Companies"):
        clear_detail()
        st.rerun()

    # Fetch company data
    companies = db.get_companies(status=None, exclude_blacklisted=False)
    company = next((c for c in companies if c["id"] == company_id), None)
    if not company:
        st.error(f"Company {company_id} not found.")
        return

    st.title(company["name"])
    st.markdown(f"### {company_status_badge(company['status'])}")

    # Meta chips
    meta = []
    country = company.get("company_country") or ""
    if country and country != "unknown":
        meta.append(f"{COUNTRY_FLAG.get(country, '🌐')} {country}")
    sector = company.get("industry_sector") or ""
    if sector and sector != "other":
        meta.append(sector_label(sector))
    size = company.get("company_size") or ""
    if size and size != "unknown":
        meta.append(size)
    if company.get("website"):
        meta.append(f"[🌐 {company['website']}]({company['website']})")
    st.caption(" · ".join(meta) if meta else "")

    # Status change
    new_status = st.selectbox(
        "Status", COMPANY_STATUSES,
        index=COMPANY_STATUSES.index(company["status"]) if company["status"] in COMPANY_STATUSES else 0,
    )
    if new_status != company["status"]:
        if st.button("Update Status"):
            db.set_company_status(company_id, new_status)
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # 4 tabs
    t1, t2, t3, t4 = st.tabs(["💼 Jobs", "👥 Contacts", "📅 Interactions", "📝 Notes"])

    with t1:
        jobs = load_jobs(profile_id=None, exclude_archived=False)
        company_jobs = [j for j in jobs if j.get("company_id") == company_id]
        if not company_jobs:
            st.caption("No jobs at this company.")
        else:
            for job in company_jobs[:20]:
                st.markdown(
                    f"{score_badge(job.get('score'))} **{job.get('title','')}** "
                    f"({job.get('status','new')})"
                )
                st.markdown(f"[View Job](/jobs?id={job['id']})")

    with t2:
        if st.button("➕ Add Contact", key="co_add_ct"):
            add_contact_dialog(company_id=company_id)
        contacts = load_contacts(company_id=company_id)
        if not contacts:
            st.caption("No contacts at this company.")
        else:
            for ct in contacts:
                with st.container(border=True):
                    name = ct.get("full_name") or f"{ct.get('first_name','')} {ct.get('last_name','')}".strip()
                    st.markdown(
                        f"**{name}** — {ct.get('role_title') or 'No role'} "
                        f"{'⚠️' if ct.get('is_unverified') else ''}"
                    )
                    st.caption(
                        f"{relationship_badge(db.get_contact_relationship_status(ct['id']))} | "
                        f"{ct.get('email') or 'no email'}"
                    )
                    st.markdown(f"[View Contact](/contacts?id={ct['id']})")

    with t3:
        if st.button("📝 Log Interaction", key="co_log_int"):
            log_interaction_dialog(company_id=company_id)
        interactions = db.get_company_interactions(company_id, limit=50)
        if not interactions:
            st.caption("No interactions logged yet.")
        else:
            for ix in interactions:
                st.caption(
                    f"**{ix['type'].replace('_',' ').title()}** — "
                    f"{ix.get('direction','none')} | {(ix.get('occurred_at') or '')[:16]}"
                )
                if ix.get("subject"):
                    st.caption(f"  _{ix['subject']}_")
                if ix.get("body_excerpt"):
                    st.caption(f"  {(ix['body_excerpt'] or '')[:200]}")

    with t4:
        current_notes = company.get("notes") or ""
        new_notes = st.text_area("Notes", value=current_notes, height=150, key="co_notes")
        if new_notes != current_notes:
            if st.button("Save Notes"):
                # Notes are on the companies table — update directly
                with db._conn() as conn:
                    conn.execute(
                        "UPDATE companies SET notes = ? WHERE id = ?",
                        (new_notes, company_id),
                    )
                st.cache_data.clear()
                st.rerun()


def render():
    ensure_db()
    cid_str = get_detail_id()
    if cid_str:
        try:
            _render_detail(int(cid_str))
        except (ValueError, TypeError):
            st.error(f"Invalid company ID: {cid_str}")
            _render_list()
    else:
        _render_list()

render()
