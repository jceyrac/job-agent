"""tracker_views/company_detail.py — Company detail view (standalone page)."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db, get_detail_id, md_link,
    load_company_by_id, load_jobs_for_company, load_contacts,
    score_badge, company_status_badge, relationship_badge,
    COMPANY_STATUSES, COUNTRY_FLAG, sector_label,
    monitoring_status_badge,
)
from tracker_views.forms import add_contact_dialog, log_interaction_dialog


def _render_detail(company_id: int):
    db = get_db()

    if st.button("← Back to Companies"):
        st.switch_page("tracker_views/companies.py")

    # Fetch company data — targeted by PK
    company = load_company_by_id(company_id)
    if not company:
        st.error(f"Company {company_id} not found.")
        return

    # Header
    st.title(company["name"])
    st.markdown(f"### {company_status_badge(company['status'])}")

    if company.get("summary"):
        st.markdown(f"_{company['summary']}_")

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

    # ── Monitoring status section ─────────────────────────────────────────
    st.divider()
    st.subheader("📡 Monitoring")
    mon_status = company.get("monitoring_status", "unmonitored")
    st.markdown(monitoring_status_badge(company), unsafe_allow_html=True)

    if mon_status == "unmonitored":
        if st.button("👁 Watch this company", use_container_width=True):
            db.set_monitoring_status(company_id, "watch_pending")
            st.cache_data.clear()
            st.rerun()

    elif mon_status == "watch_pending":
        if company.get("research_notes"):
            st.caption(f"Notes: {company['research_notes']}")
        c_r1, c_r2 = st.columns(2)
        with c_r1:
            if st.button("🔍 Research now", use_container_width=True):
                with st.spinner(f"Researching {company['name']}..."):
                    from company_researcher import research_company, update_company_from_research
                    result = research_company(
                        company["name"],
                        company.get("website") or company.get("careers_url"))
                    update_company_from_research(db, company_id, result)
                    st.cache_data.clear()
                    st.rerun()
        with c_r2:
            if st.button("✖ Stop watching", use_container_width=True):
                db.set_monitoring_status(company_id, "unmonitored")
                st.cache_data.clear()
                st.rerun()

    elif mon_status == "watch_ready":
        ats = company.get("ats_provider", "—")
        method = company.get("scraping_method", "—")
        confidence = company.get("research_confidence", "—")
        board = company.get("ats_identifier", "—")
        st.caption(f"ATS: **{ats}** | Method: **{method}** | "
                   f"Board: `{board}` | Confidence: **{confidence}**")
        c_r1, c_r2 = st.columns(2)
        with c_r1:
            if st.button("▶ Activate monitoring", use_container_width=True):
                db.set_monitoring_status(company_id, "watching")
                db.set_company_monitored(company_id, True)
                st.cache_data.clear()
                st.rerun()
        with c_r2:
            if st.button("✖ Stop watching", use_container_width=True):
                db.set_monitoring_status(company_id, "unmonitored")
                st.cache_data.clear()
                st.rerun()

    elif mon_status == "watching":
        ats = company.get("ats_provider", "—")
        careers = company.get("careers_url", "—")
        st.caption(f"ATS: **{ats}** | Careers: {careers}")
        c_r1, c_r2 = st.columns(2)
        with c_r1:
            if st.button("⏸ Pause monitoring", use_container_width=True):
                db.set_monitoring_status(company_id, "watch_ready")
                st.cache_data.clear()
                st.rerun()
        with c_r2:
            if st.button("🔍 Re-research", use_container_width=True):
                with st.spinner(f"Re-researching {company['name']}..."):
                    from company_researcher import research_company, update_company_from_research
                    result = research_company(
                        company["name"],
                        company.get("website") or company.get("careers_url"))
                    update_company_from_research(db, company_id, result)
                    st.cache_data.clear()
                    st.rerun()

    # ── Monitoring badge + toggle (Phase 8a three-state model) ──
    from tracker_views.shared import monitoring_badge, is_monitoring_source_enabled
    badge_html = monitoring_badge(company)
    if badge_html:
        st.html(badge_html)
        source_enabled = is_monitoring_source_enabled(db, company)
        if source_enabled:
            new_mon = st.toggle(
                "Monitor this company",
                value=bool(company.get("monitored")),
                key=f"mon_toggle_detail_{company_id}",
            )
            if new_mon != bool(company.get("monitored")):
                db.set_company_monitored(company_id, new_mon)
                st.cache_data.clear()
                st.rerun()
        else:
            provider = company.get("ats_provider") or company.get("scraper_id") or "?"
            st.toggle(
                "Monitor this company",
                value=False, disabled=True,
                key=f"mon_toggle_detail_dis_{company_id}",
                help=f"Enable the {provider} scraper in Settings to monitor this company.",
            )

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

    # Tabs
    t1, t2, t3, t4 = st.tabs(["💼 Jobs", "👥 Contacts", "📅 Interactions", "📝 Notes"])

    with t1:
        company_jobs = load_jobs_for_company(company_id)
        if not company_jobs:
            st.caption("No jobs at this company.")
        else:
            for job in company_jobs[:20]:
                st.markdown(
                    f"{score_badge(job.get('score'))} **{job.get('title','')}** "
                    f"({job.get('status','new')})"
                )
                st.markdown(f"[View Job](/job_detail?id={job['id']})")

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
                    st.markdown(f"[View Contact](/contact_detail?id={ct['id']})")

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
        new_notes = st.text_area("Notes", value=current_notes, height=150, key="co_detail_notes")
        if new_notes != current_notes:
            if st.button("Save Notes", key="co_save_notes"):
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
    if not cid_str:
        st.error("No company ID specified. Go back to the [Companies list](/companies).")
        return
    try:
        _render_detail(int(cid_str))
    except (ValueError, TypeError):
        st.error(f"Invalid company ID: {cid_str}")


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
