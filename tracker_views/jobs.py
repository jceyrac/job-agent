"""tracker_views/jobs.py — Jobs list + detail view."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db,
    load_jobs, load_contacts, load_profiles,
    score_badge, company_status_badge, relationship_badge, sector_label,
    COUNTRY_FLAG, COUNTRY_OPTIONS, SECTOR_LABELS,
    apply_filters, nav_to_entity, clear_detail, get_detail_id,
    email_link, linkedin_link,
)
from tracker_views.forms import log_interaction_dialog


def _render_list():
    st.title("💼 Jobs")

    # ── Sidebar filters ─────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🔍 Filters")

        profiles = load_profiles()
        profile_options = ["-- All profiles --"] + [p["id"] for p in profiles]

        # Pre-select active profile (stored in DB config)
        active_id = get_db().get_config("active_profile_id") or ""
        index = 0
        for i, opt in enumerate(profile_options):
            if opt == active_id:
                index = i
                break

        profile_id = st.selectbox("Profile", profile_options, index=index, key="jobs_profile")
        if profile_id == "-- All profiles --":
            profile_id = None

        view = st.radio("View", ["Active jobs", "Non relevant jobs"], key="jobs_view")

        min_score = st.slider("Min score", 0, 10, 0, key="jobs_min_score")

        if view == "Active jobs":
            status_filter = st.multiselect(
                "Status", ["new", "queued", "ready", "applied", "rejected", "unscored"],
                default=["new"], key="jobs_status_filter")
        else:
            status_filter = None

        date_filter = st.selectbox("Posted within", ["Any", "1 day", "3 days", "1 week", "2 weeks", "3 weeks", "1 month"], key="jobs_date_filter")
        scraped_filter = st.selectbox("Scraped within", ["Any", "1 day", "3 days", "1 week", "2 weeks", "3 weeks", "1 month"], key="jobs_scraped_filter")

        show_stale = st.checkbox("Show stale jobs (>30 days)", key="jobs_show_stale")
        show_archived = view == "Non relevant jobs"

    # Load and filter
    jobs_raw = load_jobs(profile_id, exclude_archived=False)

    # Build filter option lists from raw data
    all_locations = sorted({j.get("location", "") for j in jobs_raw if j.get("location")})
    all_work_modes = sorted({j.get("work_mode", "unknown") for j in jobs_raw})
    all_geo_zones = sorted({j.get("geo_zone", "unknown") for j in jobs_raw})
    all_sizes = sorted({j.get("company_size", "unknown") for j in jobs_raw})
    all_sectors = sorted({j.get("industry_sector", "other") for j in jobs_raw})
    all_languages = sorted({j.get("language_required", "unknown") for j in jobs_raw})
    all_sources = sorted({j.get("source", "") for j in jobs_raw if j.get("source")})

    with st.sidebar:
        location_filter = st.multiselect("Location", all_locations, key="jobs_location")
        work_mode_filter = st.multiselect("Work mode", all_work_modes, key="jobs_wm")
        geo_zone_filter = st.multiselect("Geo zone", all_geo_zones, key="jobs_gz")
        company_size_filter = st.multiselect("Company size", all_sizes, key="jobs_cs")
        sector_filter = st.multiselect("Sector", all_sectors, key="jobs_sector",
                                        format_func=lambda c: sector_label(c))
        language_filter = st.multiselect("Language", all_languages, key="jobs_lang")
        source_filter = st.multiselect("Source", all_sources, key="jobs_source")

    jobs = apply_filters(
        jobs_raw,
        min_score=min_score,
        show_stale=show_stale,
        date_filter=date_filter,
        scraped_filter=scraped_filter,
        location_filter=location_filter,
        work_mode_filter=work_mode_filter,
        geo_zone_filter=geo_zone_filter,
        company_size_filter=company_size_filter,
        sector_filter=sector_filter,
        language_filter=language_filter,
        source_filter=source_filter,
        status_filter=status_filter,
        show_archived_view=show_archived,
    )

    # Stats row
    total = len(jobs_raw)
    scored = sum(1 for j in jobs_raw if j.get("score") is not None)
    st.caption(f"{len(jobs)} of {total} jobs shown ({scored} scored)")

    if not jobs:
        st.info("No jobs match the current filters.")
        return

    # Score distribution
    if profile_id:
        hot = sum(1 for j in jobs if (j.get("score") or 0) >= 9)
        solid = sum(1 for j in jobs if 7 <= (j.get("score") or 0) <= 8)
        maybe = sum(1 for j in jobs if 5 <= (j.get("score") or 0) <= 6)
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("🔥 Hot (9-10)", hot)
        mc2.metric("⭐ Solid (7-8)", solid)
        mc3.metric("👀 Maybe (5-6)", maybe)
        mc4.metric("Total", len(jobs))

    for job in jobs:
        _render_card(job, profile_id)


def _render_card(job: dict, profile_id: str | None):
    """Render a compact job card with actions."""
    db = get_db()
    job_id = job["id"]
    status = job.get("status", "new")
    score = job.get("score") or 0
    url = job.get("url") or ""

    with st.container(border=True):
        col_badge, col_main, col_meta = st.columns([1, 6, 2])

        with col_badge:
            st.markdown(f"### {score_badge(score)}")

        with col_main:
            company = job.get("company", "")
            company_id = job.get("company_id")
            if company_id:
                company_text = f"[{company}](/companies?id={company_id})"
            else:
                company_text = company
            st.markdown(f"**{job.get('title', '')}** @ {company_text}")
            parts = [f"📍 {job.get('location', '')}" if job.get("location") else "",
                     job.get("work_mode", ""), job.get("company_size", "")]
            st.caption("  ".join(p for p in parts if p))

            meta = []
            country = job.get("company_country") or ""
            if country and country != "unknown":
                meta.append(f"{COUNTRY_FLAG.get(country, '🌐')} {country}")
            sector = job.get("industry_sector") or ""
            if sector and sector != "other":
                meta.append(sector_label(sector))
            lang = job.get("language_required") or ""
            if lang and lang not in ("english", "unknown"):
                meta.append(f"🗣 {lang}")
            if meta:
                st.caption(" · ".join(meta))

            summary = (job.get("summary") or "").strip()
            if summary:
                st.text(summary[:250] + "…" if len(summary) > 250 else summary)

        with col_meta:
            st.caption(job.get("source") or "")
            posted = (job.get("posted_date") or "")[:10]
            if posted:
                st.caption(f"📅 {posted}")
            st.caption(f"Status: **{status}**")
            st.caption(f"ID: `{job_id}`")

        # Action buttons
        btn_defs = []
        if url:
            btn_defs.append(("🔗 Open", "link", url))
        if st.button("📋 Details", key=f"detail_{job_id}"):
            nav_to_entity(job_id)
            st.rerun()
        if status == "new":
            btn_defs.append(("🚀 Queue", "action", "queued"))
        for label, new_status in [
            ("✅ Applied", "applied"),
            ("❌ Rejected", "rejected"),
            ("🚫 Not relevant", "archived"),
        ]:
            if status != new_status:
                btn_defs.append((label, "action", new_status))

        if btn_defs:
            btn_cols = st.columns(len(btn_defs))
            for i, (label, kind, value) in enumerate(btn_defs):
                if kind == "link":
                    btn_cols[i].link_button(label, value)
                else:
                    if btn_cols[i].button(label, key=f"btn_{value}_{job_id}"):
                        unsaved = (st.session_state.get(f"notes_text_{job_id}") or "").strip()
                        db_notes = (job.get("notes") or "").strip()
                        if value == "archived":
                            if unsaved or db_notes:
                                try:
                                    db.set_status(job_id, "archived", notes=unsaved or db_notes)
                                    st.cache_data.clear()
                                    st.rerun()
                                except Exception as e:
                                    st.error(str(e))
                            else:
                                st.session_state[f"pending_archive_{job_id}"] = True
                        else:
                            try:
                                db.set_status(job_id, value, notes=unsaved or None)
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))

        # Archive confirmation
        if st.session_state.get(f"pending_archive_{job_id}"):
            st.warning("Please add a note before marking this job as not relevant.")
            archive_note = st.text_area("Note", key=f"archive_note_{job_id}", height=60)
            c1, c2 = st.columns(2)
            if c1.button("Confirm archive", key=f"confirm_archive_{job_id}"):
                if archive_note.strip():
                    db.set_status(job_id, "archived", notes=archive_note.strip())
                    st.session_state.pop(f"pending_archive_{job_id}")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Note is required.")
            if c2.button("Cancel", key=f"cancel_archive_{job_id}"):
                st.session_state.pop(f"pending_archive_{job_id}")
                st.rerun()

        # Notes
        with st.expander("Notes", expanded=False):
            current_notes = (job.get("notes") or "").strip()
            st.text_area("Notes", value=current_notes, key=f"notes_text_{job_id}", height=80)

        # Application preview
        if status == "ready":
            with st.expander("Application", expanded=False):
                app = db.get_application(job_id)
                if app:
                    if app.get("analysis"):
                        st.markdown("**Analysis**")
                        st.text(app["analysis"][:500])
                    if app.get("cover_letter"):
                        st.markdown("**Cover Letter**")
                        st.text(app["cover_letter"][:500])
                else:
                    st.caption("No application content saved yet.")


def _render_detail(job_id: str):
    db = get_db()

    if st.button("← Back to Jobs"):
        clear_detail()
        st.rerun()

    job = db.get_job_for_prepare(job_id)
    if not job:
        st.error(f"Job {job_id} not found.")
        return

    st.title(job.get("title", ""))
    company = job.get("company", "")
    company_id = job.get("company_id")
    if company_id:
        st.markdown(f"### @ [{company}](/companies?id={company_id})")
    else:
        st.markdown(f"### @ {company}")

    # Score badge
    score = job.get("score")
    st.markdown(f"**{score_badge(score)}** — {job.get('reason', '')}")

    # Info chips
    chips = []
    for key, label in [
        ("work_mode", "Work mode"), ("contract_type", "Contract"),
        ("geo_zone", "Geo zone"), ("language_required", "Language"),
    ]:
        val = job.get(key)
        if val and val != "unknown":
            chips.append(f"**{label}:** {val}")
    posted = (job.get("posted_date") or "")[:10]
    if posted:
        chips.append(f"**Posted:** {posted}")
    chips.append(f"**Source:** {job.get('source', '?')}")
    st.caption(" | ".join(chips))

    # Country and sector
    country = job.get("company_country") or ""
    sector = job.get("industry_sector") or ""
    meta = []
    if country and country != "unknown":
        meta.append(f"{COUNTRY_FLAG.get(country, '🌐')} {country}")
    if sector and sector != "other":
        meta.append(sector_label(sector))
    st.caption(" · ".join(meta) if meta else "")

    # Description
    with st.expander("Description", expanded=True):
        st.markdown(job.get("description") or "No description available.")

    # Status change
    status = job.get("status", "new")
    new_status = st.selectbox(
        "Tracking status",
        ["new", "queued", "ready", "applied", "rejected", "archived"],
        index=["new", "queued", "ready", "applied", "rejected", "archived"].index(status)
        if status in ["new", "queued", "ready", "applied", "rejected", "archived"] else 0,
    )
    if new_status != status:
        if st.button("Update Status"):
            db.set_status(job_id, new_status)
            st.cache_data.clear()
            st.rerun()

    # URL
    url = job.get("url") or ""

    # Quick action buttons
    st.subheader("Actions")
    btn_cols = st.columns(6)
    col_idx = 0
    if url:
        btn_cols[col_idx].link_button("🔗 Open", url)
        col_idx += 1
    if status == "new":
        if btn_cols[col_idx].button("🚀 Queue", key=f"detail_queue_{job_id}"):
            db.set_status(job_id, "queued")
            st.cache_data.clear()
            st.rerun()
        col_idx += 1
    for label, new_status in [
        ("✅ Applied", "applied"),
        ("❌ Rejected", "rejected"),
        ("🚫 Not relevant", "archived"),
    ]:
        if status != new_status:
            if btn_cols[col_idx].button(label, key=f"detail_{new_status}_{job_id}"):
                if new_status == "archived":
                    st.session_state[f"detail_pending_archive_{job_id}"] = True
                else:
                    db.set_status(job_id, new_status)
                    st.cache_data.clear()
                    st.rerun()
            col_idx += 1

    # Archive confirmation
    if st.session_state.get(f"detail_pending_archive_{job_id}"):
        st.warning("Please add a note before marking this job as not relevant.")
        archive_note = st.text_area("Note", key=f"detail_archive_note_{job_id}", height=60)
        c1, c2 = st.columns(2)
        if c1.button("Confirm archive", key=f"detail_confirm_archive_{job_id}"):
            if archive_note.strip():
                db.set_status(job_id, "archived", notes=archive_note.strip())
                st.session_state.pop(f"detail_pending_archive_{job_id}")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("Note is required.")
        if c2.button("Cancel", key=f"detail_cancel_archive_{job_id}"):
            st.session_state.pop(f"detail_pending_archive_{job_id}")
            st.rerun()

    st.divider()

    # Contacts discovered from this ad
    st.subheader("Contacts from this posting")
    company_id = job.get("company_id")
    if company_id:
        # Find contacts linked via discovered_on_posting interactions for this job
        with db._conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT ct.* FROM contacts ct
                   JOIN interactions i ON i.contact_id = ct.id
                   WHERE i.job_id = ? AND i.type = 'discovered_on_posting'
                   ORDER BY ct.last_seen_at DESC""",
                (job_id,),
            ).fetchall()
        contacts = [dict(r) for r in rows]
        if contacts:
            for ct in contacts:
                st.caption(
                    f"**{ct.get('full_name') or ct.get('email') or 'Unknown'}** — "
                    f"{ct.get('role_title') or 'No role'} "
                    f"({'⚠️ unverified' if ct.get('is_unverified') else '✅ verified'})"
                )
        else:
            st.caption("No contacts discovered from this posting.")
    else:
        st.caption("No company linked to this job.")

    # Log interaction
    if company_id:
        if st.button("📝 Log Interaction", key="log_int_detail"):
            log_interaction_dialog(company_id=company_id, job_id=job_id)

    # Application
    app = db.get_application(job_id)
    if app:
        st.subheader("Prepared Application")
        if app.get("analysis"):
            with st.expander("Analysis"):
                st.text(app["analysis"])
        if app.get("cover_letter"):
            with st.expander("Cover Letter"):
                st.text(app["cover_letter"])


def render():
    ensure_db()
    job_id = get_detail_id()
    if job_id:
        _render_detail(job_id)
    else:
        _render_list()

render()
