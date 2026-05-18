"""tracker_views/contact_detail.py — Contact detail view (standalone page)."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db, get_detail_id, md_link,
    load_companies,
    relationship_badge, unverified_badge,
    email_link, linkedin_link, x_link, telegram_link, github_link, phone_link,
)
from tracker_views.forms import log_interaction_dialog


def _render_detail(contact_id: int):
    db = get_db()

    if st.button("← Back to Contacts"):
        st.switch_page("tracker_views/contacts.py")

    ct = db.get_contact(contact_id)
    if not ct:
        st.error(f"Contact {contact_id} not found.")
        return

    # Header
    name = ct.get("full_name") or f"{ct.get('first_name','')} {ct.get('last_name','')}".strip()
    st.title(name or ct.get("email") or f"Contact #{contact_id}")

    company_id = ct.get("company_id")
    companies = load_companies(exclude_blacklisted=False)
    company = next((c for c in companies if c["id"] == company_id), None)
    company_name = company["name"] if company else "Unknown"

    if ct.get("role_title"):
        if company_id:
            st.markdown(f"### {ct['role_title']} @ {md_link(company_name, f'/company_detail?id={company_id}')}")
        else:
            st.markdown(f"### {ct['role_title']} @ {company_name}")
    else:
        if company_id:
            st.markdown(f"### @ {md_link(company_name, f'/company_detail?id={company_id}')}")
        else:
            st.markdown(f"### @ {company_name}")

    # Relationship status
    rel_status = db.get_contact_relationship_status(contact_id)
    st.markdown(f"**{relationship_badge(rel_status)}**")
    if ct.get("is_unverified"):
        st.caption(unverified_badge())

    # Channels
    st.subheader("Contact Channels")
    channels = []
    if ct.get("email"):
        channels.append(email_link(ct["email"]))
    if ct.get("linkedin_url"):
        channels.append(linkedin_link(ct["linkedin_url"]))
    if ct.get("x_handle"):
        channels.append(x_link(ct["x_handle"]))
    if ct.get("telegram_handle"):
        channels.append(telegram_link(ct["telegram_handle"]))
    if ct.get("github_handle"):
        channels.append(github_link(ct["github_handle"]))
    if ct.get("phone"):
        channels.append(phone_link(ct["phone"]))

    if channels:
        st.markdown(" · ".join(channels))
    else:
        st.caption("No contact channels available.")

    # Toggle buttons
    c1, c2 = st.columns(2)
    with c1:
        if ct.get("is_unverified"):
            if st.button("✅ Mark Verified", use_container_width=True):
                now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
                with db._conn() as conn:
                    conn.execute(
                        "UPDATE contacts SET is_unverified = 0, last_verified_at = ? WHERE id = ?",
                        (now, contact_id),
                    )
                st.cache_data.clear()
                st.rerun()
        else:
            if st.button("⚠️ Mark Unverified", use_container_width=True):
                with db._conn() as conn:
                    conn.execute(
                        "UPDATE contacts SET is_unverified = 1 WHERE id = ?",
                        (contact_id,),
                    )
                st.cache_data.clear()
                st.rerun()

    with c2:
        if ct.get("is_current"):
            if st.button("🚪 No longer at company", use_container_width=True):
                with db._conn() as conn:
                    conn.execute(
                        "UPDATE contacts SET is_current = 0 WHERE id = ?",
                        (contact_id,),
                    )
                st.cache_data.clear()
                st.rerun()

    st.divider()

    # Interactions timeline
    st.subheader("Interactions")
    if st.button("📝 Log Interaction", key="ct_log_int"):
        log_interaction_dialog(company_id=company_id, contact_id=contact_id)

    interactions = db.get_contact_interactions(contact_id, limit=50)
    if not interactions:
        st.caption("No interactions logged for this contact.")
    else:
        for ix in interactions:
            with st.container(border=True):
                st.caption(
                    f"**{ix['type'].replace('_',' ').title()}** | "
                    f"{ix.get('direction','none')} | "
                    f"{(ix.get('occurred_at') or '')[:16]}"
                )
                if ix.get("subject"):
                    st.caption(f"_{ix['subject']}_")
                if ix.get("body_excerpt"):
                    st.text((ix.get("body_excerpt") or "")[:300])

    # Notes
    st.subheader("Notes")
    current_notes = ct.get("notes") or ""
    new_notes = st.text_area("Notes", value=current_notes, height=100, key="ct_detail_notes")
    if new_notes != current_notes:
        if st.button("Save Notes", key="ct_save_notes"):
            with db._conn() as conn:
                conn.execute(
                    "UPDATE contacts SET notes = ? WHERE id = ?",
                    (new_notes, contact_id),
                )
            st.cache_data.clear()
            st.rerun()


def render():
    ensure_db()
    cid_str = get_detail_id()
    if not cid_str:
        st.error("No contact ID specified. Go back to the [Contacts list](/contacts).")
        return
    try:
        _render_detail(int(cid_str))
    except (ValueError, TypeError):
        st.error(f"Invalid contact ID: {cid_str}")


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
