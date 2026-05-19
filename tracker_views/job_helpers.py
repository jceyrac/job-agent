"""tracker_views/job_helpers.py — Job-specific constants, helpers, and action bar
shared by the Jobs list page and Job detail page."""
import streamlit as st

from job_actions import extract_one, score_one, prepare_one
from profiles import ALL_PROFILES
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

def _resolve_active_profile() -> str | None:
    """Return the active profile id, or None if unset/'all'."""
    pid = (get_db().get_config("active_profile_id") or "").strip()
    if pid and pid in ALL_PROFILES:
        return pid
    return None


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
# Helpers — unified action bar
# ---------------------------------------------------------------------------

ACTIONS = [
    "extract", "score", "queue", "prepare",
    "applied", "rejected", "not_relevant",
]

ACTION_LABELS = {
    "extract":      "🔍 Extract",
    "score":        "🎯 Score",
    "queue":        "🚀 Queue",
    "prepare":      "📝 Prepare",
    "applied":      "✅ Applied",
    "rejected":     "❌ Rejected",
    "not_relevant": "🚫 Not relevant",
}

ENABLED = {
    "scraped":   {"extract", "applied", "not_relevant"},
    "extracted": {"score", "queue", "prepare", "applied", "not_relevant"},
    "scored":    {"queue", "prepare", "applied", "not_relevant"},
    "queued":    {"prepare", "applied", "not_relevant"},
    "prepared":  {"applied", "not_relevant"},
    "applied":   {"rejected", "not_relevant"},
    "rejected":  {"not_relevant"},
    "archived":  set(),
}


def _derive_state(job: dict, scores: list[dict] | None,
                  app: dict | None) -> str:
    """Return derived state: scraped|extracted|scored|queued|prepared|applied|rejected|archived."""
    tracking = (job.get("status") or "new").strip().lower()
    if tracking == "archived":
        return "archived"
    if tracking == "rejected":
        return "rejected"
    if tracking == "applied":
        return "applied"
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
            active = _resolve_active_profile()
            if active is None:
                st.error(
                    "Set a default profile in Settings before scoring "
                    "individual jobs (or use the advanced picker below)."
                )
                return
            if _run_score(job_id, active):
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
        elif action == "applied":
            db.set_status(job_id, "applied")
            st.cache_data.clear()
            st.rerun(scope="app")
        elif action == "rejected":
            db.set_status(job_id, "rejected")
            st.cache_data.clear()
            st.rerun(scope="app")
        elif action == "not_relevant":
            _request_archive(job, scope)
            return  # _request_archive handles its own rerun
    except Exception as e:
        st.error(str(e))


def _render_action_bar(job: dict, scope: str, scores: list[dict] | None,
                       app: dict | None) -> None:
    """Fixed 7-button action bar with disabled state."""
    state = _derive_state(job, scores, app)
    enabled = ENABLED[state]
    cols = st.columns(len(ACTIONS))
    for i, action in enumerate(ACTIONS):
        label = ACTION_LABELS[action]
        disabled = action not in enabled
        key = f"act_{scope}_{action}_{job['id']}"
        if cols[i].button(label, key=key, disabled=disabled):
            _handle_action(action, job, scope)
    st.caption(f"State: **{state}**")
