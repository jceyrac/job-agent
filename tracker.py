"""tracker.py — Multi-page Streamlit Job Tracker + CRM."""
import streamlit as st

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
