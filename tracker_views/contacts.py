"""tracker_views/contacts.py — Contacts list + detail view."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db,
    load_contacts, load_companies,
    score_badge, company_status_badge, relationship_badge, unverified_badge,
    CONTACT_ROLE_FAMILIES,
    nav_to_entity, clear_detail, get_detail_id,
    email_link, linkedin_link, x_link, telegram_link, github_link, phone_link,
)
from tracker_views.forms import add_contact_dialog, log_interaction_dialog


def _render_list():
    st.title("👥 Contacts")

    # Sidebar filters
    with st.sidebar:
        st.markdown("### 🔍 Filters")

        companies = load_companies(exclude_blacklisted=False)
        company_options = {c["id"]: c["name"] for c in companies}
        selected_companies = st.multiselect(
            "Company",
            options=list(company_options.keys()),
            format_func=lambda cid: company_options[cid],
            key="ct_co_filter",
        )

        role_family = st.selectbox(
            "Role family",
            ["All"] + sorted(CONTACT_ROLE_FAMILIES),
            key="ct_role_filter",
        )

        unverified_only = st.checkbox("Unverified only", key="ct_unverified")

        search = st.text_input("Search", key="ct_search")

        exclude_bl = st.checkbox("Exclude blacklisted companies", value=True, key="ct_exclude_bl")

    # Build query
    co_id = selected_companies[0] if len(selected_companies) == 1 else None
    contacts = load_contacts(
        company_id=co_id,
        search=search if search else None,
        is_unverified=True if unverified_only else None,
        role_family=role_family if role_family != "All" else None,
        exclude_blacklisted=exclude_bl,
    )

    # Filter by multiple companies in Python if needed
    if len(selected_companies) > 1:
        allowed = set(selected_companies)
        contacts = [c for c in contacts if c["company_id"] in allowed]

    # Header
    c1, c2 = st.columns([3, 1])
    with c1:
        st.caption(f"{len(contacts)} contacts")
    with c2:
        if st.button("➕ Add Contact", use_container_width=True):
            add_contact_dialog()

    if not contacts:
        st.info("No contacts match the current filters.")
        return

    # Bulk verify
    if unverified_only and contacts:
        with st.expander("Bulk actions", expanded=False):
            selected_for_verify = [
                c for c in contacts
                if st.checkbox(
                    c.get("full_name") or c.get("email") or f"ID {c['id']}",
                    key=f"ct_bulk_{c['id']}",
                )
            ]
            if selected_for_verify and st.button("✅ Mark selected as verified"):
                db = get_db()
                now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
                with db._conn() as conn:
                    for ct in selected_for_verify:
                        conn.execute(
                            "UPDATE contacts SET is_unverified = 0, last_verified_at = ? WHERE id = ?",
                            (now, ct["id"]),
                        )
                    st.cache_data.clear()
                st.rerun()

    # Contact cards
    for ct in contacts:
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 2, 1])
            with c1:
                name = ct.get("full_name") or f"{ct.get('first_name','')} {ct.get('last_name','')}".strip()
                st.markdown(f"**{name or ct.get('email') or 'Unknown'}**")
                role = ct.get("role_title") or ""
                company_name = ct.get("company_name") or ""
                st.caption(f"{role} @ {company_name}" if role else company_name)
            with c2:
                db = get_db()
                rel_status = db.get_contact_relationship_status(ct["id"])
                st.caption(relationship_badge(rel_status))
                if ct.get("is_unverified"):
                    st.caption(unverified_badge())
                last = (ct.get("last_interaction_at") or "")[:10]
                if last:
                    st.caption(f"Last seen: {last}")
            with c3:
                if st.button("View", key=f"ct_view_{ct['id']}"):
                    nav_to_entity(ct["id"])
                    st.rerun()


def _render_detail(contact_id: int):
    db = get_db()

    if st.button("← Back to Contacts"):
        clear_detail()
        st.rerun()

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
        st.markdown(f"### {ct['role_title']} @ {company_name}")
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
    new_notes = st.text_area("Notes", value=current_notes, height=100, key="ct_notes")
    if new_notes != current_notes:
        if st.button("Save Notes"):
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
    if cid_str:
        try:
            _render_detail(int(cid_str))
        except (ValueError, TypeError):
            st.error(f"Invalid contact ID: {cid_str}")
            _render_list()
    else:
        _render_list()

render()
