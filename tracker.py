"""tracker.py — Multi-page Streamlit Job Tracker + CRM."""
import streamlit as st

st.set_page_config(page_title="Job Tracker", layout="wide", page_icon="💼")

pages = [
    st.Page("tracker_views/dashboard.py", title="Dashboard", icon="📊", default=True),
    st.Page("tracker_views/jobs.py",      title="Jobs",      icon="💼"),
    st.Page("tracker_views/companies.py", title="Companies", icon="🏢"),
    st.Page("tracker_views/contacts.py",  title="Contacts",  icon="👥"),
    st.Page("tracker_views/settings.py",  title="Settings",  icon="⚙️"),
]

pg = st.navigation(pages, position="sidebar")
pg.run()
