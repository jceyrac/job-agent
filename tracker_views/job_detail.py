"""tracker_views/job_detail.py — Job detail view (standalone page)."""
import streamlit as st

from profiles import ALL_PROFILES
from tracker_views.shared import (
    ensure_db, get_db, get_detail_id, md_link,
    score_badge, sector_label,
    COUNTRY_FLAG,
)
from tracker_views.job_helpers import (
    _source_label, _source_link,
    _resolve_active_profile, _run_score,
    _render_action_bar,
)
from tracker_views.forms import log_interaction_dialog


def _render_detail(job_id: str):
    db = get_db()

    if st.button("← Back to Jobs"):
        st.switch_page("tracker_views/jobs.py")

    job = db.get_job_for_prepare(job_id)
    if not job:
        st.error(f"Job {job_id} not found.")
        return

    scores = db.get_scores_for_job(job_id)
    app = db.get_application(job_id)

    st.title(job.get("title", ""))
    company = job.get("company", "")
    company_id = job.get("company_id")
    if company_id:
        st.markdown(f"### @ {md_link(company, f'/company_detail?id={company_id}')}")
    else:
        st.markdown(f"### @ {company}")

    # Scores table
    if scores:
        st.subheader("Scores")
        lines = ["| Profile | Score | Reason | Model |",
                  "|---|---|---|---|"]
        for s in scores:
            lines.append(
                f"| {s['profile_id']} | {score_badge(s['score'])} "
                f"| {(s['reason'] or '')[:120]} | {s['scored_by'] or ''} |"
            )
        st.markdown("\n".join(lines))
    else:
        st.info("Not yet scored.")

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
    # Source as link
    url = job.get("url") or ""
    src = job.get("source") or ""
    if src and url:
        chips.append(f"**Source:** [{_source_label(src)}]({url})")
    else:
        chips.append(f"**Source:** {_source_label(src) or src or '?'}")
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

    # ── Unified action bar ──────────────────────────────────────────────────
    st.subheader("Actions")
    _render_action_bar(job, "detail", scores=scores, app=app)

    # ── Score against a different profile (always available) ────────────────
    with st.expander("Score against a different profile", expanded=False):
        chosen = st.selectbox(
            "Profile",
            list(ALL_PROFILES.keys()),
            key=f"score_pick_detail_{job_id}",
        )
        if st.button("Run score", key=f"score_pick_run_detail_{job_id}"):
            if _run_score(job_id, chosen):
                st.cache_data.clear()
                st.rerun()

    # Archive confirmation
    if st.session_state.get(f"pending_detail_archive_{job_id}"):
        st.warning("Please add a note before marking this job as not relevant.")
        archive_note = st.text_area("Note", key=f"detail_archive_note_{job_id}", height=60)
        c1, c2 = st.columns(2)
        if c1.button("Confirm archive", key=f"detail_confirm_archive_{job_id}"):
            if archive_note.strip():
                db.set_status(job_id, "archived", notes=archive_note.strip())
                st.session_state.pop(f"pending_detail_archive_{job_id}")
                st.cache_data.clear()
                st.rerun()
            else:
                st.error("Note is required.")
        if c2.button("Cancel", key=f"detail_cancel_archive_{job_id}"):
            st.session_state.pop(f"pending_detail_archive_{job_id}")
            st.rerun()

    st.divider()

    # Contacts discovered from this ad
    st.subheader("Contacts from this posting")
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
    if not job_id:
        st.error("No job ID specified. Go back to the [Jobs list](/jobs).")
        return
    _render_detail(job_id)


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
