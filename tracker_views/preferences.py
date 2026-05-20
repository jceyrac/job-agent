"""tracker_views/preferences.py — In-app preference report viewer + generator."""
import glob
import os

import streamlit as st

from preference_report import generate_report
from tracker_views.shared import ensure_db

REPORT_DIR = "outputs/preference_reports"


def _list_reports() -> list[str]:
    if not os.path.isdir(REPORT_DIR):
        return []
    return sorted(glob.glob(os.path.join(REPORT_DIR, "*.md")), reverse=True)


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


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
