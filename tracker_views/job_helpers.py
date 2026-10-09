"""tracker_views/job_helpers.py — Job-specific constants, helpers, and action bar
shared by the Jobs list page and Job detail page."""
from datetime import date

import streamlit as st

from core.job_actions import extract_one, score_one, prepare_one
from tracker_views.action_sets import ACTION_LABELS, ACTION_SETS
from tracker_views.shared import get_db


# ---------------------------------------------------------------------------
# Helpers — source labels
# ---------------------------------------------------------------------------

SOURCE_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "wellfound": "Wellfound",
    "welcome_to_the_jungle": "Welcome to the Jungle",
    "wttj": "Welcome to the Jungle",
    "remotive": "Remotive",
    "weworkremotely": "We Work Remotely",
    "remoteok": "RemoteOK",
    "cryptojobs.com": "CryptoJobs.com",
    "cryptojobslist": "CryptoJobsList",
    "defi jobs": "DeFi Jobs",
    "greenhouse": "Greenhouse",
    "jobup": "Jobup",
    "web3career": "Web3Career",
}


def _source_label(src: str) -> str:
    if not src:
        return ""
    return SOURCE_LABELS.get(src.lower(), src)


def _source_link(src: str, url: str) -> str:
    """Render source as a markdown link when url exists, plain text otherwise."""
    label = _source_label(src)
    if not label:
        return ""
    if url:
        return f"[{label}]({url})"
    return label


# ---------------------------------------------------------------------------
# Helpers — profile resolution + scoring
# ---------------------------------------------------------------------------

def _resolve_active_profile() -> str:
    """Return the active profile id."""
    from core.profiles import get_active_profile
    return get_active_profile().id


def _run_score(job_id: str, profile_id: str) -> bool:
    """Run score_one and show feedback. Returns True on success."""
    with st.spinner(f"Scoring against {profile_id}…"):
        result = score_one(job_id, profile_id)
    if result is None or result.get("status") == "error":
        st.error(f"Score failed: {(result or {}).get('error', 'unknown')}")
        return False
    st.success(
        f"[{profile_id}] scored {result['score']}/10 "
        f"— {(result['reason'] or '')[:80]}"
    )
    return True


# ---------------------------------------------------------------------------
# Contextual action bar (FR-008/009/010/011)
# ---------------------------------------------------------------------------

_LIFECYCLE_ACTIONS = {"applied", "interviewing", "interview", "offer", "rejected", "withdrawn"}
_LIFECYCLE_STATES = {"applied", "interviewing", "offer", "rejected", "withdrawn"}

# Space-aware action bar: the ⋯ overflow is only used when the actions don't fit
# on one line. Below this count the "rare/parking" actions render inline instead.
_MAX_INLINE_ACTIONS = 5


def _derive_state(job: dict, scores: list[dict] | None,
                  app: dict | None) -> str:
    """Return derived state (FR-010): one of scraped|extracted|scored|queued|
    prepared|applied|interviewing|offer|rejected|withdrawn|archived|expired."""
    tracking = (job.get("status") or "new").strip().lower()
    if tracking in ("archived", "rejected", "expired", "applied",
                    "interviewing", "offer", "withdrawn"):
        return tracking
    if app and app.get("prepared_at"):
        return "prepared"
    if tracking in ("queued", "ready"):
        return "queued"
    # Scores: accept list, or fall back to job.get("score_count") from load_jobs
    if scores:
        return "scored"
    if job.get("score_count"):
        return "scored"
    if job.get("extracted_at"):
        return "extracted"
    return "scraped"


# ---------------------------------------------------------------------------
# Helpers — lifecycle prompts (FR-012/029)
# ---------------------------------------------------------------------------

def _request_lifecycle(job: dict, action: str, scope: str) -> None:
    """Arm the inline confirm prompt for a lifecycle action, then rerun."""
    st.session_state[f"pending_lifecycle_{scope}_{job['id']}"] = action
    st.rerun(scope="app")


def _confirm_lifecycle(job: dict, action: str, scope: str, occurred_at,
                       invite_date, interview_date, notes) -> None:
    """Validate and write the pending lifecycle action (FR-012). Reruns on success."""
    job_id = job["id"]
    db = get_db()
    notes = (notes or "").strip() or None

    try:
        if action == "interviewing":
            if not invite_date and not interview_date:
                st.error("Provide at least one of the invite or interview date.")
                return
            confirmed = interview_date or invite_date
            if invite_date:
                db.record_lifecycle_event(
                    job_id, event="interview_invited",
                    occurred_at=invite_date.isoformat(), new_status="interviewing",
                )
            if interview_date:
                db.record_lifecycle_event(
                    job_id, event="interview",
                    occurred_at=interview_date.isoformat(), new_status="interviewing",
                )
        elif action == "interview":
            db.record_lifecycle_event(
                job_id, event="interview",
                occurred_at=occurred_at.isoformat(), new_status="interviewing",
            )
            confirmed = occurred_at
        elif action == "applied":
            db.record_lifecycle_event(
                job_id, event="application_submitted",
                occurred_at=occurred_at.isoformat(), new_status="applied",
            )
            confirmed = occurred_at
        elif action == "offer":
            db.record_lifecycle_event(
                job_id, event="decision_received",
                occurred_at=occurred_at.isoformat(), new_status="offer",
                outcome="positive",
            )
            confirmed = occurred_at
        elif action == "rejected":
            db.record_lifecycle_event(
                job_id, event="decision_received",
                occurred_at=occurred_at.isoformat(), new_status="rejected",
                outcome="negative", notes=notes,
            )
            confirmed = occurred_at
        elif action == "withdrawn":
            db.record_lifecycle_event(
                job_id, event="withdrawn",
                occurred_at=occurred_at.isoformat(), new_status="withdrawn",
                notes=notes,
            )
            confirmed = occurred_at
        else:
            return
    except ValueError as e:
        st.error(str(e))
        return

    st.session_state["lifecycle_last_date"] = confirmed  # FR-029 sticky pre-fill
    st.session_state.pop(f"pending_lifecycle_{scope}_{job_id}")
    st.cache_data.clear()
    st.rerun(scope="app")


def _render_lifecycle_prompt(job: dict, scope: str) -> None:
    """Render the inline confirm prompt for a pending lifecycle action."""
    job_id = job["id"]
    action = st.session_state.get(f"pending_lifecycle_{scope}_{job_id}")
    if not action:
        return
    db = get_db()
    summary = db.get_lifecycle_summary(job_id)

    default_date = st.session_state.get("lifecycle_last_date", date.today())

    # Context (FR-029): application date + last event date.
    ctx_bits = []
    if summary.get("application_date"):
        ctx_bits.append(f"applied {summary['application_date']}")
    if summary.get("last_event_date"):
        ctx_bits.append(f"last event {summary['last_event_date']}")
    if ctx_bits:
        st.caption(" · ".join(ctx_bits))

    invite_date = None
    interview_date = None
    occurred_at = None
    notes = None

    if action == "interviewing":
        st.markdown("**Move to interviewing**")
        c1, c2 = st.columns(2)
        invite_date = c1.date_input(
            "Invite received on", value=None, format="DD/MM/YYYY",
            key=f"lc_invite_{scope}_{job_id}",
        )
        interview_date = c2.date_input(
            "First interview on", value=default_date, format="DD/MM/YYYY",
            key=f"lc_interview_{scope}_{job_id}",
        )
        st.caption("At least one of the two is required · future dates allowed.")
    elif action == "interview":
        st.markdown("**Log an interview**")
        occurred_at = st.date_input(
            "Interview on", value=default_date, format="DD/MM/YYYY",
            key=f"lc_date_{scope}_{job_id}",
        )
    else:
        title = {
            "applied": "Mark as applied",
            "offer": "Mark as offer",
            "rejected": "Mark as rejected",
            "withdrawn": "Withdraw application",
        }[action]
        st.markdown(f"**{title}**")
        occurred_at = st.date_input(
            "Date", value=default_date, format="DD/MM/YYYY",
            key=f"lc_date_{scope}_{job_id}",
        )

    if action == "rejected":
        notes = st.text_area(
            "Employer's reason", key=f"lc_reason_{scope}_{job_id}", height=60,
        )
    elif action == "withdrawn":
        notes = st.text_area(
            "My reason", key=f"lc_reason_{scope}_{job_id}", height=60,
        )

    c1, c2 = st.columns(2)
    if c1.button("Confirm", key=f"lc_confirm_{scope}_{job_id}"):
        _confirm_lifecycle(job, action, scope, occurred_at, invite_date, interview_date, notes)
    if c2.button("Cancel", key=f"lc_cancel_{scope}_{job_id}"):
        st.session_state.pop(f"pending_lifecycle_{scope}_{job_id}")
        st.rerun(scope="app")


def _request_archive(job: dict, scope: str) -> None:
    """Show inline note prompt; archive on confirm."""
    job_id = job["id"]
    key_prefix = f"{scope}_archive" if scope != "card" else "archive"
    db = get_db()
    if st.session_state.get(f"pending_{key_prefix}_{job_id}"):
        st.warning("Please add a note before marking this job as not relevant.")
        archive_note = st.text_area("Note", key=f"{key_prefix}_note_{job_id}", height=60)
        c1, c2 = st.columns(2)
        if c1.button("Confirm archive", key=f"confirm_{key_prefix}_{job_id}"):
            if archive_note.strip():
                db.set_status(job_id, "archived", notes=archive_note.strip())
                st.session_state.pop(f"pending_{key_prefix}_{job_id}")
                st.cache_data.clear()
                st.rerun(scope="app")
            else:
                st.error("Note is required.")
        if c2.button("Cancel", key=f"cancel_{key_prefix}_{job_id}"):
            st.session_state.pop(f"pending_{key_prefix}_{job_id}")
            st.rerun(scope="app")
    else:
        # Show note prompt (keep existing notes if any)
        unsaved = (st.session_state.get(f"notes_text_{job_id}") or "").strip()
        db_notes = (job.get("notes") or "").strip()
        if unsaved or db_notes:
            try:
                db.set_status(job_id, "archived", notes=unsaved or db_notes)
                st.cache_data.clear()
                st.rerun(scope="app")
            except Exception as e:
                st.error(str(e))
        else:
            st.session_state[f"pending_{key_prefix}_{job_id}"] = True
            st.rerun(scope="app")


def _handle_action(action: str, job: dict, scope: str) -> None:
    """Execute a single action. May trigger st.rerun() internally."""
    if action in _LIFECYCLE_ACTIONS:
        _request_lifecycle(job, action, scope)
        return

    job_id = job["id"]
    db = get_db()
    try:
        if action == "extract":
            with st.spinner("Extracting…"):
                res = extract_one(job_id)
            if res and res.get("status") == "ok":
                st.success(f"Extracted: {(res.get('summary') or '')[:80]}")
            else:
                st.error(f"Extract failed: {(res or {}).get('error', 'unknown')}")
            st.cache_data.clear()
            st.rerun()  # fragment scope
        elif action == "score":
            if _run_score(job_id, _resolve_active_profile()):
                st.cache_data.clear()
                st.rerun()  # fragment scope
        elif action == "prepare":
            with st.spinner("Preparing (4 LLM calls, ~10-30s)…"):
                res = prepare_one(job_id)
            if res and res.get("status") == "ok":
                st.success(f"Prepared by {res.get('prepared_by', '?')}")
            else:
                st.error(f"Prepare failed: {(res or {}).get('error', 'unknown')}")
            st.cache_data.clear()
            st.rerun(scope="app")  # status changes → full-page rerun
        elif action == "queue":
            db.set_status(job_id, "queued")
            st.cache_data.clear()
            st.rerun(scope="app")
        elif action == "expired":
            db.set_status(job_id, "expired")
            st.cache_data.clear()
            st.rerun(scope="app")
        elif action == "not_relevant":
            _request_archive(job, scope)
            return  # _request_archive handles its own rerun
    except Exception as e:
        st.error(str(e))


def _render_state_caption(job: dict, state: str) -> None:
    """State line under the action bar: derived label for lifecycle states (FR-011)."""
    if state in _LIFECYCLE_STATES:
        summary = get_db().get_lifecycle_summary(job["id"])
        label = summary.get("label") or state
        caption = f"State: **{label}**"
        if summary.get("application_date"):
            caption += f" · applied {summary['application_date']}"
        if summary.get("decision_date"):
            caption += f" · decision {summary['decision_date']}"
        if summary.get("reason"):
            caption += f" · {summary['reason']}"
        st.caption(caption)
    else:
        st.caption(f"State: **{state}**")


def _render_action_bar(job: dict, scope: str, scores: list[dict] | None,
                       app: dict | None) -> None:
    """Contextual action bar: [primary] [secondaries] [⋯] (FR-008/009).

    The ⋯ overflow appears only when the actions won't fit on one line; when
    they do, the "rare/parking" actions render inline (space-aware, not always).
    All buttons render uniformly with the same transparent background — no
    ``type="primary"``, per design.
    """
    state = _derive_state(job, scores, app)
    primary, secondaries, more = ACTION_SETS[state]
    job_id = job["id"]

    buttons = []
    if primary:
        buttons.append(primary)
    buttons += list(secondaries)

    overflow = []
    if more:
        if len(buttons) + len(more) <= _MAX_INLINE_ACTIONS:
            buttons += list(more)
        else:
            overflow = list(more)

    n_cols = len(buttons) + (1 if overflow else 0)
    if n_cols:
        cols = st.columns(n_cols)
        i = 0
        for action in buttons:
            label = ACTION_LABELS[action]
            key = f"act_{scope}_{action}_{job_id}"
            if cols[i].button(label, key=key):
                _handle_action(action, job, scope)
            i += 1
        if overflow:
            with cols[i].popover("⋯"):
                for action in overflow:
                    if st.button(ACTION_LABELS[action], key=f"act_{scope}_{action}_{job_id}"):
                        _handle_action(action, job, scope)

    _render_state_caption(job, state)

    # Inline lifecycle confirm prompt (rendered here — single change point, FR-008).
    if st.session_state.get(f"pending_lifecycle_{scope}_{job_id}"):
        _render_lifecycle_prompt(job, scope)
