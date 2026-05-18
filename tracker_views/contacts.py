"""tracker_views/contacts.py — Contacts list view."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db,
    load_contacts, load_companies, md_link,
    relationship_badge, unverified_badge,
    CONTACT_ROLE_FAMILIES,
)
from tracker_views.forms import add_contact_dialog


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
                company_id = ct.get("company_id")
                if company_id and company_name:
                    link = md_link(company_name, f"/company_detail?id={company_id}")
                    st.markdown(f"{role} @ {link}" if role else link)
                else:
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
                st.markdown(md_link("View", f"/contact_detail?id={ct['id']}"))


def render():
    ensure_db()
    _render_list()


from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
