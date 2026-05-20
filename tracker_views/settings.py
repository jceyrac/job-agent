"""tracker_views/settings.py — Profile management, stats, and actions."""
import subprocess
import sys

import streamlit as st

from tracker_views.shared import ensure_db, get_db


def render():
    ensure_db()
    db = get_db()

    st.title("⚙️ Settings")
    _render_stats_actions(db)


def _render_stats_actions(db):
    st.subheader("Database Stats")

    # Total counts
    with db._conn() as conn:
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        total_scored = conn.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0]
        total_jobs_no_score = total_jobs - conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM job_scores"
        ).fetchone()[0]
        total_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        unverified = conn.execute("SELECT COUNT(*) FROM contacts WHERE is_unverified = 1").fetchone()[0]
        total_interactions = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Jobs", total_jobs, f"{total_scored} scored")
    c2.metric("Companies", total_companies)
    c3.metric("Contacts", total_contacts, f"{unverified} unverified")
    c4.metric("Interactions", total_interactions)

    # Contacts by role_family
    with db._conn() as conn:
        by_role = conn.execute(
            "SELECT role_family, COUNT(*) FROM contacts GROUP BY role_family ORDER BY COUNT(*) DESC"
        ).fetchall()
    if by_role:
        st.caption("Contacts by role: " + " · ".join(
            f"**{r[0] or 'unknown'}**: {r[1]}" for r in by_role
        ))

    st.divider()

    # Actions
    st.subheader("Actions")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 Clear Cache", use_container_width=True):
            st.cache_data.clear()
            st.success("Cache cleared.")
            st.rerun()

    with c2:
        if st.button("🔍 Re-extract Job Fields", use_container_width=True):
            with st.spinner("Running score.py --extract ..."):
                result = subprocess.run(
                    [sys.executable, "score.py", "--extract"],
                    capture_output=True, text=True, timeout=600,
                )
                st.text_area("Output", result.stdout + "\n" + result.stderr, height=200)
                st.cache_data.clear()

from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
