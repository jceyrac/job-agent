"""tracker_views/preferences.py — In-app preference report viewer + generator + feedback loop."""
import glob
import os
from datetime import date

import streamlit as st

from preference_report import generate_report, generate_action_items
from tracker_views.shared import ensure_db

REPORT_DIR = "outputs/preference_reports"
PROPOSAL_DIR = "outputs/context_proposals"
ACTIONS_DIR = "outputs/action_items"


def _list_reports() -> list[str]:
    if not os.path.isdir(REPORT_DIR):
        return []
    return sorted(glob.glob(os.path.join(REPORT_DIR, "*.md")), reverse=True)


def _latest_file_date(dir_path: str) -> str | None:
    """Return the date string of the most recent .md file in a directory, or None."""
    if not os.path.isdir(dir_path):
        return None
    files = sorted(glob.glob(os.path.join(dir_path, "*.md")), reverse=True)
    if not files:
        return None
    return os.path.basename(files[0]).replace("preference_report_", "").replace("proposal_", "").replace("action_items_", "").replace(".md", "")


def _latest_file_path(dir_path: str) -> str | None:
    """Return the path of the most recent .md file in a directory, or None."""
    if not os.path.isdir(dir_path):
        return None
    files = sorted(glob.glob(os.path.join(dir_path, "*.md")), reverse=True)
    return files[0] if files else None


def render():
    ensure_db()
    st.title("📈 Preferences")

    with st.sidebar:
        st.markdown("### Generate")
        if st.button("Regenerate report"):
            with st.spinner("Analyzing apply/archive behavior…"):
                from profiles import get_active_profile
                path = generate_report(
                    profile_id=get_active_profile().id, output_dir=REPORT_DIR)
            st.success(f"Wrote {os.path.basename(path)}")
            st.cache_data.clear()
            st.rerun()

    reports = _list_reports()
    if not reports:
        st.info("No reports yet. Click Regenerate in the sidebar.")
        return

    st.caption(f"Showing latest report: {os.path.basename(reports[0])}")
    with open(reports[0]) as f:
        st.markdown(f.read())

    if len(reports) > 1:
        with st.expander(f"Older reports ({len(reports) - 1})"):
            for path in reports[1:]:
                label = os.path.basename(path)
                if st.button(f"View {label}", key=f"v_{label}"):
                    st.session_state["pref_selected"] = path
                    st.rerun()

    sel = st.session_state.get("pref_selected")
    if sel and sel != reports[0]:
        st.divider()
        st.caption(f"Viewing: {os.path.basename(sel)}")
        with open(sel) as f:
            st.markdown(f.read())

    # ── Feedback loop expander ──────────────────────────────────────────
    st.divider()
    with st.expander("🔄 Feedback loop", expanded=False):
        last_report = _latest_file_date(REPORT_DIR)
        last_proposal = _latest_file_date(PROPOSAL_DIR)
        last_actions = _latest_file_date(ACTIONS_DIR)

        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown("**Status**")
            st.caption(f"Last report: {last_report or 'never'}")
            st.caption(f"Last proposal: {last_proposal or 'none'}")
            st.caption(f"Last actions: {last_actions or 'none'}")

        with c2:
            st.markdown("**Generate**")
            if st.button("📊 Run full analysis", use_container_width=True):
                with st.spinner("Generating report + action items…"):
                    from profiles import get_active_profile
                    pid = get_active_profile().id
                    report_path = generate_report(profile_id=pid)
                    actions_path = generate_action_items(profile_id=pid)
                st.success(f"Report: {report_path}")
                st.success(f"Action items: {actions_path}")
                st.cache_data.clear()
                st.rerun()

            if st.button("🤖 Suggest context update", use_container_width=True,
                         help="Call the LLM to draft a revised scoring_context. Takes 5-10s."):
                with st.spinner("Calling LLM to draft revised scoring_context…"):
                    from storage import JobStorage
                    from context_tuner import propose_context_update
                    from profiles import get_active_profile
                    pid = get_active_profile().id
                    db = JobStorage("data/jobs.db")
                    try:
                        prop_path = propose_context_update(pid, db)
                        st.success(f"Proposal written to {prop_path}")
                        st.info(
                            "Review the proposal below, then apply from the CLI:\n\n"
                            f"`python preference_report.py --apply-context {prop_path}`"
                        )
                    except Exception as e:
                        st.error(f"LLM call failed: {e}")

        with c3:
            st.markdown("**Review**")
            actions_path = _latest_file_path(ACTIONS_DIR)
            if actions_path and st.button("📋 View action items", use_container_width=True):
                with open(actions_path) as f:
                    st.session_state["feedback_view_actions"] = f.read()

            proposal_path = _latest_file_path(PROPOSAL_DIR)
            if proposal_path and st.button("📝 View context proposal", use_container_width=True):
                with open(proposal_path) as f:
                    st.session_state["feedback_view_proposal"] = f.read()

        # Render viewed content below the columns
        view_actions = st.session_state.get("feedback_view_actions")
        if view_actions:
            with st.expander("Action items", expanded=True):
                st.markdown(view_actions)
                if st.button("✕ Close", key="close_actions"):
                    st.session_state.pop("feedback_view_actions")
                    st.rerun()

        view_proposal = st.session_state.get("feedback_view_proposal")
        if view_proposal:
            with st.expander("Context proposal", expanded=True):
                st.markdown(view_proposal)
                if st.button("✕ Close", key="close_proposal"):
                    st.session_state.pop("feedback_view_proposal")
                    st.rerun()


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
