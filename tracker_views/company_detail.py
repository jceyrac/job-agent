"""tracker_views/company_detail.py — Company detail view (standalone page)."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db, get_detail_id, md_link,
    load_company_by_id, load_jobs_for_company, load_contacts,
    score_badge, company_status_badge, relationship_badge,
    COMPANY_STATUSES, COUNTRY_FLAG, sector_label,
    monitoring_status_badge, monitoring_badge, is_monitoring_source_enabled,
    monitoring_info_line,
)
from tracker_views.forms import add_contact_dialog, log_interaction_dialog

SIZE_OPTIONS = ["startup", "scaleup", "sme", "large", "unknown"]
METHOD_OPTIONS = [
    "greenhouse", "lever", "workable", "ashby", "teamtailor",
    "recruitee", "bamboohr", "smartrecruiters", "myworkdayjobs",
    "jobspy", "custom_html", "manual", "none",
]


def _safe_index(options: list, value) -> int:
    """Return the index of value in options, or 0 if not found."""
    try:
        return options.index(value) if value else 0
    except ValueError:
        return 0


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

    # ── Edit button ───────────────────────────────────────────────────────
    editing = st.session_state.get(f"edit_company_{company_id}", False)

    if not editing:
        if st.button("✏️ Edit", key=f"edit_btn_{company_id}"):
            st.session_state[f"edit_company_{company_id}"] = True
            st.rerun()

    # ── Edit form ─────────────────────────────────────────────────────────
    if editing:
        with st.form(key=f"edit_form_{company_id}"):
            st.subheader("✏️ Edit company")

            st.markdown("**Identity**")
            c_id1, c_id2 = st.columns(2)
            with c_id1:
                name = st.text_input("Name", value=company.get("name") or "")
                website = st.text_input("Website", value=company.get("website") or "")
                location = st.text_input(
                    "Location", value=company.get("company_country") or "")
            with c_id2:
                sector = st.text_input(
                    "Sector / Field", value=company.get("industry_sector") or "")
                size = st.selectbox(
                    "Company size", SIZE_OPTIONS,
                    index=_safe_index(SIZE_OPTIONS, company.get("company_size")))
                x_handle = st.text_input(
                    "X account", value=company.get("x_handle") or "")

            st.markdown("**Monitoring config**")
            c_m1, c_m2 = st.columns(2)
            with c_m1:
                careers_url = st.text_input(
                    "Careers URL", value=company.get("careers_url") or "")
                ats_provider = st.text_input(
                    "ATS provider", value=company.get("ats_provider") or "")
                ats_slug = st.text_input(
                    "ATS board slug", value=company.get("ats_identifier") or "")
            with c_m2:
                ats_board_url = st.text_input(
                    "ATS board URL", value=company.get("ats_board_url") or "")
                scraping_method = st.selectbox(
                    "Scraping method", METHOD_OPTIONS,
                    index=_safe_index(METHOD_OPTIONS, company.get("scraping_method")))
            research_notes = st.text_area(
                "Research notes", value=company.get("research_notes") or "", height=80)

            col_save, col_cancel = st.columns(2)
            with col_save:
                submitted = st.form_submit_button("💾 Save", use_container_width=True)
            with col_cancel:
                cancelled = st.form_submit_button("✖ Cancel", use_container_width=True)

        if submitted:
            db.update_company_fields(company_id, {
                "name": name.strip(),
                "website": website.strip() or None,
                "company_country": location.strip() or None,
                "industry_sector": sector.strip() or None,
                "company_size": size,
                "x_handle": x_handle.strip() or None,
                "careers_url": careers_url.strip() or None,
                "ats_provider": ats_provider.strip() or None,
                "ats_identifier": ats_slug.strip() or None,
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

    # ── Company status ───────────────────────────────────────────────────────
    new_status = st.selectbox(
        "Status", COMPANY_STATUSES,
        index=COMPANY_STATUSES.index(company["status"]) if company["status"] in COMPANY_STATUSES else 0,
        key="co_status_select",
    )
    if new_status != company["status"]:
        if st.button("Update status", key="co_update_status"):
            db.set_company_status(company_id, new_status)
            st.cache_data.clear()
            st.rerun()

    # ── Monitoring section ────────────────────────────────────────────────
    st.divider()
    st.subheader("📡 Monitoring")
    mon_status = company.get("monitoring_status", "unmonitored")

    # ATS / method / confidence info line
    info = monitoring_info_line(company)
    if info:
        st.markdown(info, unsafe_allow_html=True)

    # Monitoring status selectbox — only shows valid transitions
    _MON_TRANSITIONS = {
        "unmonitored":      ["unmonitored", "watch_pending"],
        "watch_pending":    ["watch_pending", "watch_ready", "watch_unsuitable", "unmonitored"],
        "watch_ready":     ["watch_ready", "watching", "watch_pending", "unmonitored"],
        "watching":        ["watching", "watch_ready", "unmonitored"],
        "watch_unsuitable": ["watch_unsuitable", "watch_pending", "unmonitored"],
    }
    _MON_LABELS = {
        "unmonitored":      "⬜ unmonitored",
        "watch_pending":    "🔍 watch_pending",
        "watch_ready":     "⏸ watch_ready",
        "watching":        "✅ watching",
        "watch_unsuitable": "⛔ unsuitable",
    }
    valid_transitions = _MON_TRANSITIONS.get(mon_status, [mon_status])
    mon_options = [_MON_LABELS[s] for s in valid_transitions]
    current_label = _MON_LABELS.get(mon_status, mon_status)
    current_idx = mon_options.index(current_label) if current_label in mon_options else 0

    selected_label = st.selectbox(
        "Monitoring status", mon_options,
        index=current_idx,
        key=f"mon_status_select_{company_id}",
    )
    selected_status = valid_transitions[mon_options.index(selected_label)]
    if selected_status != mon_status:
        if st.button("Apply", key=f"mon_apply_{company_id}"):
            db.set_monitoring_status(company_id, selected_status)
            db.set_company_monitored(company_id, selected_status == "watching")
            st.cache_data.clear()
            st.rerun()

    # Contextual action buttons — only for watch_pending
    if mon_status == "watch_pending":
        if company.get("research_notes"):
            st.caption(f"Research notes: {company['research_notes']}")
        _already_researched = bool(company.get("scraping_method") and company.get("scraping_method") != "none")
        if not _already_researched:
            if st.button("🔍 Research now", use_container_width=True, key=f"research_{company_id}"):
                with st.spinner(f"Researching {company['name']}..."):
                    from company_researcher import research_company, update_company_from_research
                    result = research_company(
                        company["name"],
                        company.get("website") or company.get("careers_url"))
                    update_company_from_research(db, company_id, result)
                    st.cache_data.clear()
                    st.rerun()
        else:
            if st.button("🔍 Re-research", use_container_width=True, key=f"reresearch_{company_id}"):
                with st.spinner(f"Re-researching {company['name']}..."):
                    from company_researcher import research_company, update_company_from_research
                    result = research_company(
                        company["name"],
                        company.get("website") or company.get("careers_url"))
                    update_company_from_research(db, company_id, result)
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
