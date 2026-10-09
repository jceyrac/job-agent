"""tracker_views/job_detail.py — Job detail view (standalone page)."""
from datetime import date, datetime

import streamlit as st

from core.profiles import ACTIVE_PROFILE_ID
from tracker_views.shared import (
    ensure_db, get_db, get_detail_id, md_link,
    score_badge, sector_label,
    COUNTRY_FLAG,
)
from tracker_views.job_helpers import (
    _source_label, _source_link,
    _render_action_bar, _request_archive, _request_lifecycle,
)
from tracker_views.forms import log_interaction_dialog


# ── Application timeline helpers (FR-014/015/028/031) ───────────────────────

_LIFECYCLE_STATUSES = {"applied", "interviewing", "offer", "rejected", "withdrawn"}
_STATUS_OPTIONS = ["new", "queued", "ready", "applied", "interviewing",
                   "offer", "rejected", "withdrawn", "expired", "archived"]

_EVENT_TYPE_LABELS = {
    "interview_invited": "Interview invite received",
    "interview": "Interview",
    "decision_received": "Decision",
    "withdrawn": "Withdrawal",
}
_IMPLIED_STATUS = {
    "interview_invited": "interviewing",
    "interview": "interviewing",
    "withdrawn": "withdrawn",
}


def _to_date(ts) -> date:
    try:
        return datetime.strptime(str(ts)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


def _fmt_ddmm(ts) -> str:
    d = str(ts)[:10]
    return f"{d[8:10]}/{d[5:7]}/{d[:4]}" if len(d) == 10 else d


def _event_title(ev: dict, events: list[dict], idx: int) -> str:
    t = ev["type"]
    if t == "application_submitted":
        return "Applied"
    if t == "interview_invited":
        return "Interview invite received"
    if t == "interview":
        rnd = sum(1 for x in events[: idx + 1] if x["type"] == "interview")
        return f"Interview · round {rnd}"
    if t == "decision_received":
        return "Offer" if ev.get("outcome") == "positive" else "Rejected"
    if t == "withdrawn":
        return "Withdrawn"
    return t


def _current_status(db, job_id: str) -> str:
    with db._conn() as conn:
        row = conn.execute(
            "SELECT status FROM job_tracking WHERE job_id = ?", (job_id,)
        ).fetchone()
    return row["status"] if row else "new"


def _render_timeline_event(db, job_id: str, ev: dict, events: list[dict], idx: int):
    """Render one timeline row plus its inline edit/delete prompts (FR-014/015/031)."""
    event_id = ev["id"]
    title = _event_title(ev, events, idx)
    date_str = _fmt_ddmm(ev["occurred_at"])
    note = (ev.get("notes") or "").strip()
    src = ev.get("source") or "manual"

    text = f"**{date_str}** · **{title}**"
    if note:
        text += f" — {note}"
    st.markdown(text)
    if src != "manual":
        st.caption(f"source: {src}")

    c1, c2, _ = st.columns([0.7, 0.8, 4])
    if c1.button("Edit", key=f"edit_ev_{event_id}"):
        st.session_state[f"pending_edit_event_{event_id}"] = True
        st.rerun()
    if c2.button("Delete", key=f"del_ev_{event_id}"):
        st.session_state[f"pending_delete_event_{event_id}"] = True
        st.rerun()

    if st.session_state.get(f"pending_edit_event_{event_id}"):
        _render_edit_event(db, job_id, ev, events, idx)
    if st.session_state.get(f"pending_delete_event_{event_id}"):
        _render_delete_event(db, job_id, ev)


def _render_edit_event(db, job_id: str, ev: dict, events: list[dict], idx: int):
    """Inline edit prompt for an event's date and/or notes (FR-015)."""
    event_id = ev["id"]
    st.markdown(f"**Edit event — {_event_title(ev, events, idx)}**")
    new_date = st.date_input(
        "Date", value=_to_date(ev["occurred_at"]), format="DD/MM/YYYY",
        key=f"edit_date_{event_id}",
    )
    new_notes = st.text_area(
        "Notes", value=(ev.get("notes") or ""), key=f"edit_notes_{event_id}", height=60,
    )
    c1, c2 = st.columns(2)
    if c1.button("Save", key=f"edit_save_{event_id}"):
        try:
            db.update_lifecycle_event(
                event_id,
                occurred_at=new_date.isoformat(),
                notes=new_notes.strip(),
            )
            db.reconcile_lifecycle_status(job_id)
            st.session_state.pop(f"pending_edit_event_{event_id}", None)
            st.cache_data.clear()
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if c2.button("Cancel", key=f"edit_cancel_{event_id}"):
        st.session_state.pop(f"pending_edit_event_{event_id}", None)
        st.rerun()


def _render_delete_event(db, job_id: str, ev: dict):
    """Inline delete confirmation; the status is re-derived after delete (FR-015)."""
    event_id = ev["id"]
    st.warning("Delete this event? The job's status will be re-derived from the remaining events.")
    c1, c2 = st.columns(2)
    if c1.button("Delete", key=f"delete_confirm_{event_id}"):
        try:
            db.delete_lifecycle_event(event_id)
            db.reconcile_lifecycle_status(job_id)
            st.session_state.pop(f"pending_delete_event_{event_id}", None)
            st.cache_data.clear()
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if c2.button("Cancel", key=f"delete_cancel_{event_id}"):
        st.session_state.pop(f"pending_delete_event_{event_id}", None)
        st.rerun()


def _write_past_event(db, job_id: str, event_type: str, occurred: date,
                      outcome: str | None, notes: str | None):
    """Record a back-dated/out-of-order event via the FR-006 entry point (FR-028)."""
    notes = (notes or "").strip() or None
    implied = (
        _IMPLIED_STATUS[event_type] if event_type != "decision_received"
        else ("offer" if outcome == "positive" else "rejected")
    )
    occurred_iso = occurred.isoformat()
    try:
        db.record_lifecycle_event(
            job_id, event=event_type, occurred_at=occurred_iso,
            new_status=implied, outcome=outcome, notes=notes,
        )
    except ValueError as e:
        msg = str(e).lower()
        # Back-dated interview/invite on a terminal job (US7): record without flipping state.
        if event_type in ("interview", "interview_invited") and (
            "transition" in msg or "implies" in msg
        ):
            try:
                db.record_lifecycle_event(
                    job_id, event=event_type, occurred_at=occurred_iso,
                    new_status=_current_status(db, job_id), outcome=outcome, notes=notes,
                )
            except ValueError as e2:
                st.error(str(e2))
                return
        else:
            st.error(str(e))
            return
    st.session_state["lifecycle_last_date"] = occurred  # FR-029 sticky pre-fill
    st.session_state.pop(f"pending_add_past_{job_id}", None)
    st.cache_data.clear()
    st.rerun()


def _render_add_past_event(db, job_id: str):
    """Inline form to add an invite/interview/decision/withdrawal with any valid date (FR-028)."""
    st.markdown("**Add past event**")
    event_type = st.selectbox(
        "Event", ["interview_invited", "interview", "decision_received", "withdrawn"],
        format_func=_EVENT_TYPE_LABELS.get, key=f"ape_type_{job_id}",
    )
    default_date = st.session_state.get("lifecycle_last_date", date.today())
    occurred = st.date_input(
        "Date", value=default_date, format="DD/MM/YYYY", key=f"ape_date_{job_id}",
    )
    outcome = None
    if event_type == "decision_received":
        outcome = st.radio(
            "Outcome", ["positive", "negative"],
            format_func=lambda o: "Offer" if o == "positive" else "Rejected",
            horizontal=True, key=f"ape_outcome_{job_id}",
        )
    notes = None
    if event_type == "decision_received":
        label = "Employer's reason" if outcome == "negative" else "Notes"
        notes = st.text_area(label, key=f"ape_notes_{job_id}", height=60)
    elif event_type == "withdrawn":
        notes = st.text_area("My reason", key=f"ape_notes_{job_id}", height=60)

    c1, c2 = st.columns(2)
    if c1.button("Confirm", key=f"ape_confirm_{job_id}"):
        _write_past_event(db, job_id, event_type, occurred, outcome, notes)
    if c2.button("Cancel", key=f"ape_cancel_{job_id}"):
        st.session_state.pop(f"pending_add_past_{job_id}", None)
        st.rerun()


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

    # Active profile score
    active_score = next(
        (s for s in scores if s["profile_id"] == ACTIVE_PROFILE_ID), None)
    if active_score:
        st.markdown(
            f"**{score_badge(active_score['score'])}** — "
            f"{active_score.get('reason', '')}"
        )
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

    # Status change (FR-013: lifecycle statuses route through record_lifecycle_event)
    status = job.get("status", "new")
    new_status = st.selectbox(
        "Tracking status",
        _STATUS_OPTIONS,
        index=_STATUS_OPTIONS.index(status) if status in _STATUS_OPTIONS else 0,
    )
    if new_status != status:
        if st.button("Update Status"):
            if new_status in _LIFECYCLE_STATUSES:
                # Same date prompt as the action-bar buttons (FR-006/012).
                _request_lifecycle(job, new_status, "detail")
            else:
                db.set_status(job_id, new_status)
                st.cache_data.clear()
                st.rerun()

    # ── Unified action bar ──────────────────────────────────────────────────
    st.subheader("Actions")
    _render_action_bar(job, "detail", scores=scores, app=app)

    # Archive confirmation — delegated to shared helper
    if st.session_state.get(f"pending_detail_archive_{job_id}"):
        _request_archive(job, "detail")

    # ── Application timeline (FR-014/015/028/031) ────────────────────────────
    events = db.get_lifecycle_events(job_id)
    has_application = any(e["type"] == "application_submitted" for e in events)
    if events or status in _LIFECYCLE_STATUSES:
        st.subheader("Application timeline")
        if events:
            for idx, ev in enumerate(events):
                _render_timeline_event(db, job_id, ev, events, idx)
        else:
            st.caption("No application date recorded — select 'applied' above to record it.")

        # "+ Add past event" (FR-028) — only once an application event exists.
        if has_application:
            if st.session_state.get(f"pending_add_past_{job_id}"):
                _render_add_past_event(db, job_id)
            elif st.button("+ Add past event", key=f"add_past_{job_id}"):
                st.session_state[f"pending_add_past_{job_id}"] = True
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
