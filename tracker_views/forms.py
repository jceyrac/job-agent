"""tracker_views/forms.py — @st.dialog modal forms for adding/logging."""

import time
from datetime import date

import streamlit as st

from tracker_views.shared import (
    get_db, load_companies, load_contacts, load_jobs,
    COMPANY_STATUSES, COUNTRY_OPTIONS, SECTOR_LABELS,
    CONTACT_ROLE_FAMILIES, CONTACT_SENIORITIES,
    INTERACTION_TYPES, INTERACTION_DIRECTIONS, INTERACTION_OUTCOMES,
)


@st.dialog("Add Company", width="large")
def add_company_dialog():
    name = st.text_input("Company name *", placeholder="e.g., Acme Corp")
    website = st.text_input("Website", placeholder="https://...")

    col1, col2 = st.columns(2)
    with col1:
        country = st.selectbox("Country", ["(unknown)"] + COUNTRY_OPTIONS)
    with col2:
        sector = st.selectbox("Industry sector", ["(unknown)"] + list(SECTOR_LABELS.keys()))

    col1, col2 = st.columns(2)
    with col1:
        size = st.selectbox("Company size", ["(unknown)", "startup", "scaleup", "sme", "large"])
    with col2:
        status = st.selectbox("Initial status", COMPANY_STATUSES,
                               index=COMPANY_STATUSES.index("watching"))

    notes = st.text_area("Notes", height=80)

    if st.button("Add Company", type="primary"):
        if not name.strip():
            st.error("Company name is required.")
            return
        db = get_db()
        try:
            cid = db.upsert_company(name.strip(), website=website or None)
            if country != "(unknown)":
                db.update_company_enrichment(cid, {"company_country": country})
            if sector != "(unknown)":
                db.update_company_enrichment(cid, {"industry_sector": SECTOR_LABELS[sector]})
            if size != "(unknown)":
                db.update_company_enrichment(cid, {"company_size": size})
            if status != "prospect":
                db.set_company_status(cid, status, note="Initial status set at creation")
            if notes.strip():
                with db._conn() as conn:
                    conn.execute(
                        "UPDATE companies SET notes = ? WHERE id = ?",
                        (notes.strip(), cid),
                    )
            st.success(f"Company '{name.strip()}' added.")
            st.cache_data.clear()
            time.sleep(0.5)
            st.rerun()
        except Exception as e:
            st.error(str(e))


@st.dialog("Add Contact", width="large")
def add_contact_dialog(company_id: int | None = None):
    companies = load_companies(status=None, exclude_blacklisted=False)
    company_options = {c["id"]: c["name"] for c in companies}

    selected_company = st.selectbox(
        "Company *",
        options=list(company_options.keys()),
        format_func=lambda cid: company_options[cid],
        index=list(company_options.keys()).index(company_id) if company_id and company_id in company_options else 0,
    )

    col1, col2 = st.columns(2)
    with col1:
        first_name = st.text_input("First name")
        role_title = st.text_input("Role title")
        seniority = st.selectbox("Seniority", ["(unknown)"] + sorted(CONTACT_SENIORITIES))
    with col2:
        last_name = st.text_input("Last name")
        role_family = st.selectbox("Role family", [""] + sorted(CONTACT_ROLE_FAMILIES))

    email = st.text_input("Email")
    linkedin_url = st.text_input("LinkedIn URL", placeholder="https://linkedin.com/in/...")
    x_handle = st.text_input("X handle", placeholder="@handle")
    telegram_handle = st.text_input("Telegram handle", placeholder="@handle")
    github_handle = st.text_input("GitHub handle")
    phone = st.text_input("Phone")

    notes = st.text_area("Notes", height=80)

    if st.button("Add Contact", type="primary"):
        if not selected_company:
            st.error("Company is required.")
            return
        db = get_db()
        try:
            contact_id = db.upsert_contact(
                company_id=selected_company,
                first_name=first_name or None,
                last_name=last_name or None,
                role_title=role_title or None,
                role_family=role_family or None,
                seniority=seniority if seniority != "(unknown)" else None,
                email=email or None,
                linkedin_url=linkedin_url or None,
                x_handle=x_handle or None,
                telegram_handle=telegram_handle or None,
                github_handle=github_handle or None,
                phone=phone or None,
                notes=notes or None,
                is_unverified=False,
            )
            st.success(f"Contact added (ID: {contact_id}).")
            st.cache_data.clear()
            time.sleep(0.5)
            st.rerun()
        except Exception as e:
            st.error(str(e))


@st.dialog("Log Interaction", width="large")
def log_interaction_dialog(
    company_id: int | None = None,
    contact_id: int | None = None,
    job_id: str | None = None,
):
    # Company selector
    companies = load_companies(status=None, exclude_blacklisted=False)
    company_options = {c["id"]: c["name"] for c in companies}
    co_id = st.selectbox(
        "Company *",
        options=list(company_options.keys()),
        format_func=lambda cid: company_options[cid],
        index=list(company_options.keys()).index(company_id) if company_id and company_id in company_options else 0,
    )

    # Contact selector (filtered by company)
    contacts = load_contacts(company_id=co_id) if co_id else []
    contact_options = {0: "(none)"}
    contact_options.update({
        c["id"]: c.get("full_name") or c.get("email") or f"ID {c['id']}"
        for c in contacts
    })
    ct_id = st.selectbox(
        "Contact",
        options=list(contact_options.keys()),
        format_func=lambda cid: contact_options[cid],
        index=list(contact_options.keys()).index(contact_id) if contact_id and contact_id in contact_options else 0,
    )
    ct_id = ct_id if ct_id != 0 else None

    # Job selector
    if co_id:
        jobs_raw = load_jobs(profile_id=None, exclude_archived=False)
        company_jobs = [j for j in jobs_raw if j.get("company_id") == co_id]
        job_options = {"": "(none)"}
        job_options.update({j["id"]: f"{j.get('title','')} @ {j.get('company','')}" for j in company_jobs})
    else:
        job_options = {"": "(none)"}
    j_id = st.selectbox(
        "Job",
        options=list(job_options.keys()),
        format_func=lambda jid: job_options[jid],
        index=list(job_options.keys()).index(job_id) if job_id and job_id in job_options else 0,
    )
    j_id = j_id or None

    # Type + Direction
    col1, col2 = st.columns(2)
    with col1:
        int_type = st.selectbox("Type *", sorted(INTERACTION_TYPES))
    with col2:
        direction = st.selectbox("Direction", sorted(INTERACTION_DIRECTIONS))

    # Outcome (only when type is decision_received)
    outcome = None
    if int_type == "decision_received":
        outcome = st.selectbox("Outcome", ["(none)"] + sorted(INTERACTION_OUTCOMES))
        outcome = outcome if outcome != "(none)" else None

    subject = st.text_input("Subject")
    body_excerpt = st.text_area("Notes / Excerpt", height=100)
    occurred_at = st.date_input("Date", value=date.today()).isoformat()
    follow_up_due = st.date_input("Follow-up due", value=None)
    follow_up_due_str = follow_up_due.isoformat() if follow_up_due else None

    if st.button("Log Interaction", type="primary"):
        db = get_db()
        try:
            iid = db.log_interaction(
                company_id=co_id,
                type=int_type,
                contact_id=ct_id,
                job_id=j_id,
                direction=direction,
                outcome=outcome,
                subject=subject or None,
                body_excerpt=body_excerpt or None,
                occurred_at=occurred_at,
                follow_up_due_at=follow_up_due_str,
            )
            st.success(f"Interaction logged (ID: {iid}).")
            st.cache_data.clear()
            time.sleep(0.5)
            st.rerun()
        except Exception as e:
            st.error(str(e))
