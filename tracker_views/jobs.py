"""tracker_views/jobs.py — Jobs list view."""
import streamlit as st

from profiles import ACTIVE_PROFILE_ID
from tracker_views.shared import (
    ensure_db, get_db,
    load_jobs, load_applications_index,
    score_badge, sector_label, md_link,
    COUNTRY_FLAG, SECTOR_LABELS,
    apply_filters,
)
from tracker_views.job_helpers import (
    _source_label, _derive_state, _run_score, _render_action_bar,
)


# ---------------------------------------------------------------------------
# List view
# ---------------------------------------------------------------------------

def _render_list():
    st.title("💼 Jobs")

    # ── Sidebar filters ─────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("### 🔍 Filters")

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
    jobs_raw = load_jobs(exclude_archived=False)

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

        per_page = st.selectbox(
            "Per page", [25, 50, 100, 250, "All"],
            index=1,
            key="jobs_per_page",
        )

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

    # Reset page when filter set or per_page changes
    sig = (
        min_score, show_stale, date_filter, scraped_filter,
        tuple(location_filter or ()), tuple(work_mode_filter or ()),
        tuple(geo_zone_filter or ()), tuple(company_size_filter or ()),
        tuple(sector_filter or ()), tuple(language_filter or ()),
        tuple(source_filter or ()), tuple(status_filter or ()),
        show_archived, per_page,
    )
    if st.session_state.get("jobs_filter_sig") != sig:
        st.session_state["jobs_page"] = 1
        st.session_state["jobs_filter_sig"] = sig

    total = len(jobs)
    if per_page == "All":
        page_jobs = jobs
        n_pages = 1
        current_page = 1
    else:
        n_pages = max(1, (total + per_page - 1) // per_page)
        current_page = st.session_state.get("jobs_page", 1)
        current_page = max(1, min(current_page, n_pages))
        start = (current_page - 1) * per_page
        end = start + per_page
        page_jobs = jobs[start:end]

    # Stats row
    total_raw = len(jobs_raw)
    scored = sum(1 for j in jobs_raw if j.get("score") is not None)
    st.caption(f"{total} of {total_raw} jobs shown ({scored} scored)")

    if not jobs:
        st.info("No jobs match the current filters.")
        return

    # Score distribution (full filtered set, not just visible page)
    hot = sum(1 for j in jobs if (j.get("score") or 0) >= 9)
    solid = sum(1 for j in jobs if 7 <= (j.get("score") or 0) <= 8)
    maybe = sum(1 for j in jobs if 5 <= (j.get("score") or 0) <= 6)
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("🔥 Hot (9-10)", hot)
    mc2.metric("⭐ Solid (7-8)", solid)
    mc3.metric("👀 Maybe (5-6)", maybe)
    mc4.metric("Total", total)

    # Pagination controls
    if per_page != "All" and n_pages > 1:
        pcols = st.columns([1, 2, 1, 1])
        if pcols[0].button("◀ Prev", disabled=current_page <= 1):
            st.session_state["jobs_page"] = current_page - 1
            st.rerun()
        pcols[1].caption(
            f"Page {current_page} of {n_pages} · "
            f"showing {len(page_jobs)} of {total} jobs"
        )
        if pcols[2].button("Next ▶", disabled=current_page >= n_pages):
            st.session_state["jobs_page"] = current_page + 1
            st.rerun()
        jump = pcols[3].number_input(
            "Go to", min_value=1, max_value=n_pages,
            value=current_page, key="jobs_page_input",
            label_visibility="collapsed",
        )
        if jump != current_page:
            st.session_state["jobs_page"] = int(jump)
            st.rerun()

    apps_index = load_applications_index()

    for job in page_jobs:
        _render_card(job, apps_index=apps_index)


@st.fragment
def _render_card(job: dict, apps_index: dict[str, dict]):
    """Render a compact job card with unified action bar."""
    db = get_db()
    job_id = job["id"]
    status = job.get("status", "new")
    score = job.get("score") or 0
    url = job.get("url") or ""
    src = job.get("source") or ""

    with st.container(border=True):
        col_badge, col_main, col_meta = st.columns([1, 6, 2])

        with col_badge:
            st.markdown(f"### {score_badge(score)}")

        with col_main:
            company = job.get("company", "")
            company_id = job.get("company_id")
            if company_id:
                company_text = md_link(company, f"/company_detail?id={company_id}")
            else:
                company_text = company
            title_md = md_link(f"**{job.get('title', '')}**", f"/job_detail?id={job_id}")
            st.markdown(f"{title_md}  @ {company_text}")
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
            # Source as link
            if src and url:
                st.markdown(f"[{_source_label(src)}]({url})")
            else:
                st.caption(_source_label(src) or src)
            posted = (job.get("posted_date") or "")[:10]
            if posted:
                st.caption(f"📅 {posted}")
            derived = _derive_state(job, scores=None, app=apps_index.get(job_id))
            st.caption(f"State: **{derived}**")
            st.caption(f"ID: `{job_id}`")

        # ── Unified action bar ──────────────────────────────────────────────
        app = apps_index.get(job_id)
        _render_action_bar(job, "card", scores=None, app=app)

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
                app_data = apps_index.get(job_id)
                if app_data:
                    if app_data.get("analysis"):
                        st.markdown("**Analysis**")
                        st.text(app_data["analysis"][:500])
                    if app_data.get("cover_letter"):
                        st.markdown("**Cover Letter**")
                        st.text(app_data["cover_letter"][:500])
                else:
                    st.caption("No application content saved yet.")


def render():
    ensure_db()
    _render_list()


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
