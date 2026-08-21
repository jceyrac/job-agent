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

/* Tighten metric label size */
[data-testid="stMetricLabel"] { font-size: 12px !important; }

/* Make job card containers slightly more compact */
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {
    padding: 0.6rem 0.8rem !important;
}

/* Sidebar filter section headers */
.sidebar-section-header {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    opacity: 0.5;
    margin: 1rem 0 0.25rem;
}
</style>
""")

# ── First-run gating ───────────────────────────────────────────────────────


def _is_onboarded() -> bool:
    """Return True if the user has a usable profile (completed onboarding
    OR a pre-existing profile with a scoring_context).

    Checks onboarding_complete first. If not set, queries the DB directly
    — does NOT use load_active_profile() here because it seeds a default
    profile into an empty DB, which would falsely pass the gate mid-onboarding
    and redirect the user away from the wizard before they can save."""
    if not os.path.exists(DB_PATH):
        return False
    from storage import JobStorage
    from profiles import DEFAULT_PROFILE_ID, load_active_profile
    db = JobStorage(DB_PATH)

    # If the explicit onboarding flag is set, the user completed the wizard.
    # Use load_active_profile for backfill safety (pre-v3 scoring_context).
    if db.get_config("onboarding_complete") == "true":
        profile = load_active_profile(db)
        return bool(profile and profile.scoring_context.strip())

    # No flag yet — query the DB directly to avoid seeding.
    # load_active_profile() would insert the code-defined default profile
    # into an empty DB, which makes the gate pass prematurely during a
    # first-time onboarding session (the CV step creates the DB file, and
    # the next rerun would find the seeded profile and redirect to the
    # dashboard before the user reaches Generate/Review/Save).
    active_id = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)
    row = db.get_profile(active_id)
    if row:
        criteria = row.get("criteria", {})
        if isinstance(criteria, dict) and criteria.get("scoring_context", "").strip():
            # Pre-Phase-2 user: has a real saved profile but no flag — auto-set
            db.set_config("onboarding_complete", "true")
            return True

    return False


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
        st.Page("tracker_views/reports.py",       title="Reports",      icon="📤"),
        st.Page("tracker_views/job_detail.py",     title="Job",         icon="🔍", url_path="job_detail"),
        st.Page("tracker_views/company_detail.py", title="Company",    icon="🔍", url_path="company_detail"),
        st.Page("tracker_views/contact_detail.py", title="Contact",    icon="🔍", url_path="contact_detail"),
    ]

    pg = st.navigation(pages, position="sidebar")
    pg.run()
