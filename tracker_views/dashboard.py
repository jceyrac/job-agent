"""tracker_views/dashboard.py — Landing page with at-a-glance widgets."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db, get_detail_id,
    load_dashboard_data, load_jobs, load_companies,
    score_badge, company_status_badge, relationship_badge, unverified_badge,
    COUNTRY_FLAG, sector_label,
    nav_to_entity,
)


def _widget_container(title: str, icon: str):
    """Return a st.container with a styled header."""
    c = st.container(border=True)
    c.markdown(f"### {icon} {title}")
    return c


def render():
    ensure_db()
    db = get_db()

    st.title("📊 Dashboard")

    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    data = load_dashboard_data()

    # ── Row 1: At a glance ──────────────────────────────────────────────────
    st.subheader("At a Glance")
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        with st.container(border=True):
            st.markdown("#### 📋 Follow-ups Due")
            items = data["follow_ups_due_today"]
            if not items:
                st.caption("No follow-ups due. Nice.")
            else:
                st.metric("Due today", len(items))
                for item in items[:5]:
                    st.caption(
                        f"**{item.get('contact_name') or item.get('company_name', '?')}** — "
                        f"{item.get('type','').replace('_',' ').title()} "
                        f"({(item.get('follow_up_due_at') or '')[:10]})"
                    )

    with c2:
        with st.container(border=True):
            st.markdown("#### 📥 Recent Inbound")
            items = data["recent_inbound"]
            if not items:
                st.caption("No replies yet this week.")
            else:
                st.metric("Last 7 days", len(items))
                for item in items[:5]:
                    st.caption(
                        f"**{item.get('contact_name') or item.get('company_name', '?')}** — "
                        f"{item.get('type','').replace('_',' ').title()} "
                        f"({(item.get('occurred_at') or '')[:10]})"
                    )

    with c3:
        with st.container(border=True):
            st.markdown("#### ⚠️ Unverified Contacts")
            count = data["unverified_contacts_count"]
            if count == 0:
                st.caption("All contacts verified.")
            else:
                st.metric("Unverified", count)
                if st.button("➔ Review", key="review_unverified"):
                    st.switch_page("tracker_views/contacts.py")

    with c4:
        with st.container(border=True):
            st.markdown("#### ⏳ Stale Outreach")
            items = data["stale_active_outreach"]
            if not items:
                st.caption("All outreach is fresh.")
            else:
                st.metric("Stale (>14d)", len(items))
                for item in items[:5]:
                    cname = item.get("name", "?")
                    cid = item.get("id")
                    last = (item.get("last_interaction_at") or "never")[:10]
                    if cid:
                        st.markdown(f"➔ [{cname}](/companies?id={cid}) ({last})")
                    else:
                        st.caption(f"➔ {cname} ({last})")

    # ── Row 2: Pipeline stats ───────────────────────────────────────────────
    st.subheader("Pipeline")
    stats = db.get_stats(None) if hasattr(db, 'get_stats') else {}
    by_status = stats.get("by_status", {}) if stats else {}

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    status_order = ["new", "queued", "ready", "applied", "rejected", "archived"]
    status_icons = {"new": "🆕", "queued": "📋", "ready": "✅", "applied": "📤",
                    "rejected": "❌", "archived": "🗄"}
    for col, s in zip([m1, m2, m3, m4, m5, m6], status_order):
        col.metric(f"{status_icons.get(s,'')} {s.title()}", by_status.get(s, 0))

    # ── Row 3: Hot Jobs Feed ────────────────────────────────────────────────
    st.subheader("🔥 Hot Jobs Feed")
    hot_jobs = load_jobs(profile_id=None, exclude_archived=True)
    hot_jobs = [j for j in hot_jobs if (j.get("score") or 0) >= 6][:10]

    if not hot_jobs:
        st.caption("No high-score jobs right now.")
    else:
        for job in hot_jobs:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.markdown(
                        f"**{score_badge(job.get('score'))}**  "
                        f"{job.get('title', '')} @ {job.get('company', '')}"
                    )
                    country = job.get("company_country") or ""
                    meta = []
                    if country and country != "unknown":
                        meta.append(f"{COUNTRY_FLAG.get(country, '🌐')} {country}")
                    sector = job.get("industry_sector") or ""
                    if sector and sector != "other":
                        meta.append(sector_label(sector))
                    st.caption(" · ".join(meta) if meta else "")
                with c2:
                    st.markdown(f"[View](/jobs?id={job['id']})")

render()
