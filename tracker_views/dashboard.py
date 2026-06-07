"""tracker_views/dashboard.py — Landing page with at-a-glance widgets."""
import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db,
    load_dashboard_data, load_jobs, load_companies,
    score_badge, company_status_badge, relationship_badge, unverified_badge,
    COUNTRY_FLAG, sector_label,
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

    # ── Sidebar ────────────────────────────────────────────────────────────
    with st.sidebar:
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
                        st.markdown(f"➔ [{cname}](/company_detail?id={cid}) ({last})")
                    else:
                        st.caption(f"➔ {cname} ({last})")

    st.divider()

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

    st.divider()

    # ── Row 2b: DB stats ───────────────────────────────────────────────────
    st.subheader("Database")
    with db._conn() as conn:
        total_jobs      = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        total_scored    = conn.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0]
        unscored        = total_jobs - conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM job_scores").fetchone()[0]
        total_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        total_contacts  = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        unverified      = conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE is_unverified = 1").fetchone()[0]

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Jobs", total_jobs, f"{total_scored} scored")
    d2.metric("Unscored", unscored)
    d3.metric("Companies", total_companies)
    d4.metric("Contacts", total_contacts, f"{unverified} unverified")

    st.divider()

    # ── Row 3: Hot Jobs Feed ────────────────────────────────────────────────
    st.subheader("🔥 Hot Jobs Feed")
    hot_jobs = [
        j for j in load_jobs(exclude_archived=False)
        if (j.get("score") or 0) >= 6
        and j.get("status") in ("new", "ready", "queued")
    ][:10]

    if not hot_jobs:
        st.caption("No high-score jobs right now.")
    else:
        for job in hot_jobs:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                with c1:
                    score_str = score_badge(job.get('score'))
                    st.html(
                        f'<span style="font-size:18px;font-weight:700;margin-right:6px">{score_str}</span>'
                        f'{job.get("title", "")} @ {job.get("company", "")}'
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
                    st.markdown(f"[View](/job_detail?id={job['id']})")

from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
