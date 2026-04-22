"""tracker.py — Streamlit Job Tracker + Settings

Run:
    streamlit run tracker.py
"""

import json
import os
from datetime import date, timedelta

import streamlit as st

from profiles import ALL_PROFILES, DEFAULT_PROFILE_ID, SearchProfile
from storage import JobStorage

DB_PATH = "data/jobs.db"

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Job Tracker", layout="wide", page_icon="💼")

# ─── DB check ─────────────────────────────────────────────────────────────────
if not os.path.exists(DB_PATH):
    st.warning("No data yet. Run scrape.py first.")
    st.stop()

db = JobStorage(DB_PATH)
profiles = db.get_all_profiles()
profile_ids = [p["id"] for p in profiles]
profile_map = {p["id"]: p for p in profiles}

# ─── Session state init ────────────────────────────────────────────────────────
_active_id = db.get_config("active_profile_id", default=DEFAULT_PROFILE_ID)
if "profile_id" not in st.session_state:
    _initial = _active_id if _active_id in profile_map else (profile_ids[0] if profile_ids else DEFAULT_PROFILE_ID)
    st.session_state.profile_id = _initial

# Reset per-profile filters when profile changes
_cur_pid = st.session_state.get("profile_id")
if st.session_state.get("_prev_profile_id") != _cur_pid:
    st.session_state.pop("source_filter", None)
    st.session_state.pop("geo_zone_filter", None)
    st.session_state.pop("location_filter", None)
    st.session_state.pop("work_mode_filter", None)
    st.session_state.pop("company_size_filter", None)
    st.session_state["_prev_profile_id"] = _cur_pid

# ─── Sidebar: profile selectbox ───────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    if profile_ids:
        st.selectbox(
            "Profile",
            options=[None] + profile_ids,
            format_func=lambda pid: "— All profiles —" if pid is None else profile_map.get(pid, {}).get("name", pid),
            key="profile_id",
        )
    else:
        st.info("No profiles in DB. Run scrape.py first.")

active_profile_id = st.session_state.get("profile_id")  # None = all profiles

# Load jobs for current view
if active_profile_id is None:
    jobs_raw = db.get_all_jobs_best_score()
else:
    jobs_raw = db.get_all_for_tracker(active_profile_id)
all_sources = sorted({j["source"] for j in jobs_raw if j.get("source")})
all_geo_zones = sorted({j.get("geo_zone") or "unknown" for j in jobs_raw})
all_locations = sorted({j["location"] for j in jobs_raw if j.get("location")})

# ─── Sidebar: remaining filters ────────────────────────────────────────────────
with st.sidebar:
    min_score = st.slider("Min score", 1, 10, value=5, key="min_score")
    status_filter = st.multiselect(
        "Status",
        ["new", "queued", "ready", "applied", "rejected", "archived", "unscored"],
        default=["new"],
        key="status_filter",
    )
    st.selectbox(
        "Posted within",
        ["Any", "1 day", "3 days", "1 week", "2 weeks", "3 weeks", "1 month"],
        key="date_filter",
    )
    st.multiselect(
        "Location",
        options=all_locations,
        default=[],
        key="location_filter",
        placeholder="All locations",
    )
    work_mode_filter = st.multiselect(
        "Work mode",
        ["remote", "hybrid", "on-site", "unknown"],
        default=["remote", "hybrid", "on-site", "unknown"],
        key="work_mode_filter",
    )
    geo_zone_filter = st.multiselect(
        "Geo zone (classifier)",
        all_geo_zones,
        default=all_geo_zones,
        key="geo_zone_filter",
    )
    company_size_filter = st.multiselect(
        "Company size",
        ["startup", "scaleup", "sme", "large", "unknown"],
        default=["startup", "scaleup", "sme", "large", "unknown"],
        key="company_size_filter",
    )
    source_filter = st.multiselect(
        "Source",
        options=all_sources,
        default=all_sources,
        key="source_filter",
    )

# ─── Apply filters (Python-side, no SQL round-trip) ───────────────────────────
_DATE_FILTER_DAYS = {
    "1 day": 1, "3 days": 3, "1 week": 7,
    "2 weeks": 14, "3 weeks": 21, "1 month": 30,
}


def apply_filters(jobs: list[dict]) -> list[dict]:
    result = [j for j in jobs if
              j.get("status") == "unscored" or (j.get("score") or 0) >= st.session_state.min_score]
    date_filter = st.session_state.get("date_filter", "Any")
    if date_filter and date_filter != "Any":
        max_days = _DATE_FILTER_DAYS.get(date_filter)
        if max_days:
            cutoff = date.today() - timedelta(days=max_days)
            filtered = []
            for j in result:
                raw = j.get("posted_date") or ""
                try:
                    if date.fromisoformat(str(raw)[:10]) >= cutoff:
                        filtered.append(j)
                except (ValueError, TypeError):
                    pass
            result = filtered
    location_filter = st.session_state.get("location_filter") or []
    if location_filter:
        result = [j for j in result if j.get("location") in location_filter]
    if st.session_state.work_mode_filter:
        result = [j for j in result if (j.get("work_mode") or "unknown") in st.session_state.work_mode_filter]
    if st.session_state.geo_zone_filter:
        result = [j for j in result if (j.get("geo_zone") or "unknown") in st.session_state.geo_zone_filter]
    if st.session_state.company_size_filter:
        result = [j for j in result if (j.get("company_size") or "unknown") in st.session_state.company_size_filter]
    if st.session_state.get("source_filter"):
        result = [j for j in result if j.get("source") in st.session_state.source_filter]
    if st.session_state.status_filter:
        result = [j for j in result if j.get("status") in st.session_state.status_filter]
    return result


jobs = apply_filters(jobs_raw)

# ─── Helpers ───────────────────────────────────────────────────────────────────
def score_badge(score) -> str:
    if score is None:
        return "❓ —/10"
    if score >= 9:
        return f"🔥 {score}/10"
    elif score >= 7:
        return f"⭐ {score}/10"
    return f"👀 {score}/10"


def render_job_card(job: dict, profile_id: str, show_profile_tag: bool = False) -> None:
    job_id = job["id"]
    status = job.get("status", "new")
    score = job.get("score") or 0
    url = job.get("url") or ""

    with st.container(border=True):
        col_badge, col_main, col_meta = st.columns([1, 7, 2])

        with col_badge:
            st.markdown(f"### {score_badge(score)}")

        with col_main:
            st.markdown(f"**{job.get('title', '')}** @ {job.get('company', '')}")
            parts = [
                f"📍 {job.get('location', '')}" if job.get("location") else "",
                f"🌍 {job.get('geo_zone', '')}" if job.get("geo_zone") else "",
                job.get("work_mode", ""),
                job.get("company_size", ""),
            ]
            st.caption("  ".join(p for p in parts if p))
            summary = (job.get("summary") or "").strip()
            if summary:
                st.text(summary[:250] + "…" if len(summary) > 250 else summary)

        with col_meta:
            st.caption(job.get("source") or "")
            posted = (job.get("posted_date") or "")[:10]
            if posted:
                st.caption(f"📅 {posted}")
            if show_profile_tag and job.get("best_profile_id"):
                st.caption(f"🏷 {job['best_profile_id']}")
            st.caption(f"Status: **{status}**")
            st.caption(f"ID: `{job_id}`")

        # Action buttons
        btn_defs = []
        if url:
            btn_defs.append(("🔗 Open", "link", url))
        # Queue button — shown when status is new (not yet in pipeline)
        if status == "new":
            btn_defs.append(("🚀 Queue", "action", "queued"))
        for label, new_status in [
            ("✅ Applied", "applied"),
            ("❌ Rejected", "rejected"),
            ("🚫 Not relevant", "archived"),
        ]:
            if status != new_status:
                btn_defs.append((label, "action", new_status))

        if btn_defs:
            btn_cols = st.columns(len(btn_defs))
            for i, (label, kind, value) in enumerate(btn_defs):
                if kind == "link":
                    btn_cols[i].link_button(label, value)
                else:
                    if btn_cols[i].button(label, key=f"btn_{value}_{job_id}"):
                        try:
                            db.set_status(job_id, value)
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

        # Notes (collapsed by default)
        with st.expander("📝 Notes"):
            current_notes = (job.get("notes") or "").strip()
            notes_val = st.text_area(
                "notes",
                value=current_notes,
                key=f"notes_text_{job_id}",
                label_visibility="collapsed",
                height=80,
            )
            if st.button("Save notes", key=f"notes_save_{job_id}"):
                try:
                    db.set_status(job_id, status, notes=notes_val)
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

        # Application content — shown for jobs with status 'ready'
        with st.expander("📋 Application"):
            if status == "ready":
                app = db.get_application(job_id)
                if app:
                    analysis = (app.get("analysis") or "").strip()
                    cover_letter = (app.get("cover_letter") or "").strip()
                    if analysis:
                        st.markdown("**Analysis**")
                        st.markdown(analysis)
                    if cover_letter:
                        st.markdown("**Cover Letter**")
                        st.markdown(cover_letter)
                else:
                    st.info("No application content saved yet.")
            else:
                st.info("No application prepared yet.")


# ─── Tabs ──────────────────────────────────────────────────────────────────────
tab_jobs, tab_settings = st.tabs(["💼 Jobs", "⚙️ Settings"])

# ══ Tab 1: Jobs ════════════════════════════════════════════════════════════════
with tab_jobs:
    by_status: dict[str, int] = {}
    for j in jobs_raw:
        s = j.get("status") or "new"
        by_status[s] = by_status.get(s, 0) + 1

    # Section 1 — Score distribution (specific profile only)
    if active_profile_id is not None:
        hot_count = sum(1 for j in jobs if (j.get("score") or 0) >= 9)
        solid_count = sum(1 for j in jobs if 7 <= (j.get("score") or 0) <= 8)
        maybe_count = sum(1 for j in jobs if 5 <= (j.get("score") or 0) <= 6)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total (filtered)", len(jobs))
        col2.metric("🔥 Hot (9-10)", hot_count)
        col3.metric("⭐ Solid (7-8)", solid_count)
        col4.metric("👀 Maybe (5-6)", maybe_count)
    else:
        st.metric("Total (filtered)", len(jobs))

    # Section 2 — Status counts (always shown)
    s1, s2, s3, s4, s5, s6, s7 = st.columns(7)
    s1.metric("New", by_status.get("new", 0))
    s2.metric("Queued", by_status.get("queued", 0))
    s3.metric("Ready", by_status.get("ready", 0))
    s4.metric("Applied", by_status.get("applied", 0))
    s5.metric("Rejected", by_status.get("rejected", 0))
    s6.metric("Archived", by_status.get("archived", 0))
    s7.metric("❓ Unscored", by_status.get("unscored", 0))

    st.divider()

    if not jobs:
        st.info("No jobs found for this profile and filters.")
    else:
        if active_profile_id is None:
            st.caption(f"Showing **{len(jobs)}** jobs — all profiles (best score per job)")
        else:
            profile_name = profile_map.get(active_profile_id, {}).get("name", active_profile_id)
            st.caption(f"Showing **{len(jobs)}** jobs — profile: **{profile_name}**")
        for job in jobs:
            card_profile_id = active_profile_id or job.get("best_profile_id") or (profile_ids[0] if profile_ids else "")
            render_job_card(job, card_profile_id, show_profile_tag=active_profile_id is None)


# ══ Tab 2: Settings ════════════════════════════════════════════════════════════
with tab_settings:
    col_left, col_right = st.columns([3, 7])

    with col_left:
        st.subheader("Profiles")
        active_db_id = db.get_config("active_profile_id", default=DEFAULT_PROFILE_ID)

        if not profiles:
            st.info("No profiles found.")
            settings_profile = None
        else:
            profile_radio_labels = [
                f"{p['name']}  ✅" if p["id"] == active_db_id else p["name"]
                for p in profiles
            ]
            sel_idx = st.radio(
                "Select profile to edit",
                range(len(profiles)),
                format_func=lambda i: profile_radio_labels[i],
                key="settings_profile_idx",
                label_visibility="collapsed",
            )
            settings_profile = profiles[sel_idx]

            if st.button("Set as active", key="set_active_btn"):
                db.set_config("active_profile_id", settings_profile["id"])
                st.success(f"'{settings_profile['name']}' is now the active profile.")
                st.rerun()

    with col_right:
        if not profiles or not settings_profile:  # type: ignore[name-defined]
            st.info("No profiles to edit.")
        else:
            st.subheader(f"Edit: {settings_profile['name']}")
            criteria = json.loads(settings_profile.get("criteria") or "{}")

            with st.form("edit_profile_form"):
                name = st.text_input("Profile name", value=settings_profile["name"])

                allowed_geo_zones = st.multiselect(
                    "Allowed geo zones",
                    ["europe", "global_remote", "us_only", "apac", "latam", "unknown"],
                    default=criteria.get("allowed_geo_zones", []),
                )
                allowed_work_modes = st.multiselect(
                    "Allowed work modes",
                    ["remote", "hybrid", "on-site", "unknown"],
                    default=criteria.get("allowed_work_modes", []),
                )
                location_kw_raw = st.text_area(
                    "Location keywords (one per line)",
                    value="\n".join(criteria.get("location_keywords", [])),
                )
                boost_kw_raw = st.text_area(
                    "Boost keywords (one per line)",
                    value="\n".join(criteria.get("boost_keywords", [])),
                )
                company_sizes = st.multiselect(
                    "Company sizes",
                    ["startup", "scaleup", "sme", "large"],
                    default=criteria.get("company_sizes", []),
                )
                score_threshold = st.slider(
                    "Score threshold", 1, 10, value=criteria.get("score_threshold", 5)
                )
                remote_or_hybrid = st.checkbox(
                    "Remote or hybrid only",
                    value=criteria.get("remote_or_hybrid", True),
                )

                if st.form_submit_button("💾 Save profile"):
                    updated = SearchProfile(
                        id=settings_profile["id"],
                        name=name,
                        allowed_geo_zones=allowed_geo_zones,
                        allowed_work_modes=allowed_work_modes,
                        location_keywords=[
                            kw.strip() for kw in location_kw_raw.splitlines() if kw.strip()
                        ],
                        boost_keywords=[
                            kw.strip() for kw in boost_kw_raw.splitlines() if kw.strip()
                        ],
                        company_sizes=company_sizes,
                        score_threshold=score_threshold,
                        remote_or_hybrid=remote_or_hybrid,
                    )
                    db.upsert_profile(updated)
                    st.success("Profile saved. Changes apply on next run.")
