"""tracker.py — Multi-page Streamlit Job Tracker + CRM."""
import os
import streamlit as st

from paths import DB_PATH

st.set_page_config(page_title="Job Tracker", layout="wide", page_icon="💼")

st.html("""
<style>
[data-testid="stSidebarNav"] a[href*="job_detail"],
[data-testid="stSidebarNav"] a[href*="company_detail"],
[data-testid="stSidebarNav"] a[href*="contact_detail"] {
    display: none;
}
</style>
""")

# ── First-run gating ───────────────────────────────────────────────────────


def _is_onboarded() -> bool:
    """Return True if the user has a usable profile (completed onboarding
    OR a pre-existing profile with a scoring_context)."""
    if not os.path.exists(DB_PATH):
        return False
    from storage import JobStorage
    from profiles import DEFAULT_PROFILE_ID
    db = JobStorage(DB_PATH)
    active_id = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)
    row = db.get_profile(active_id)
    if not row:
        return False
    criteria = row.get("criteria", {})
    if not criteria.get("scoring_context", "").strip():
        return False
    # If the explicit flag is missing but a usable profile exists
    # (pre-Phase-2 user), auto-set it so the wizard doesn't show.
    if db.get_config("onboarding_complete") != "true":
        db.set_config("onboarding_complete", "true")
    return True


if not _is_onboarded():
    pg = st.navigation(
        [st.Page("tracker_views/onboarding.py", title="Welcome", icon="🚀", default=True)],
        position="sidebar",
    )
    pg.run()
else:
    pages = [
        st.Page("tracker_views/dashboard.py",      title="Dashboard",  icon="📊", default=True),
        st.Page("tracker_views/jobs.py",           title="Jobs",       icon="💼"),
        st.Page("tracker_views/companies.py",      title="Companies",  icon="🏢"),
        st.Page("tracker_views/contacts.py",       title="Contacts",   icon="👥"),
        st.Page("tracker_views/settings.py",       title="Settings",    icon="⚙️"),
        st.Page("tracker_views/preferences.py",   title="Preferences",  icon="📈"),
        st.Page("tracker_views/job_detail.py",     title="Job",         icon="🔍", url_path="job_detail"),
        st.Page("tracker_views/company_detail.py", title="Company",    icon="🔍", url_path="company_detail"),
        st.Page("tracker_views/contact_detail.py", title="Contact",    icon="🔍", url_path="contact_detail"),
    ]

    pg = st.navigation(pages, position="sidebar")
    pg.run()
