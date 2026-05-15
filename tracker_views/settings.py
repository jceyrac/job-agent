"""tracker_views/settings.py — Profile management, stats, and actions."""
import subprocess
import sys

import streamlit as st

from tracker_views.shared import (
    ensure_db, get_db, load_dashboard_data, load_profiles,
    COUNTRY_OPTIONS, SECTOR_LABELS,
)
from profiles import SearchProfile, ALL_PROFILES


_SECTOR_CODE_TO_LABEL = {v: k for k, v in SECTOR_LABELS.items()}


def render():
    ensure_db()
    db = get_db()

    st.title("⚙️ Settings")

    tab1, tab2 = st.tabs(["Profile Management", "Stats & Actions"])

    with tab1:
        _render_profile_management(db)

    with tab2:
        _render_stats_actions(db)


def _render_profile_management(db):
    profiles = load_profiles()
    profile_ids = [p["id"] for p in profiles]

    col_left, col_right = st.columns([1, 2])

    with col_left:
        st.subheader("Profiles")
        if not profile_ids:
            st.info("No profiles found. Import profiles from profiles.py.")
            return

        active_id = db.get_config("active_profile_id") or ""
        idx = 0
        for i, pid in enumerate(profile_ids):
            if pid == active_id:
                idx = i
                break

        selected = st.radio(
            "Select profile",
            profile_ids,
            index=idx,
            key="settings_profile_select",
        )
        if st.button("Set as active"):
            db.set_config("active_profile_id", selected)
            st.success(f"'{selected}' is now active.")

    with col_right:
        if selected not in ALL_PROFILES:
            st.warning(f"Profile '{selected}' not found in ALL_PROFILES. Cannot edit.")
            return

        profile = ALL_PROFILES[selected]
        with st.form("edit_profile_form"):
            st.subheader(f"Edit: {profile.name}")
            name = st.text_input("Profile name", value=profile.name)

            geo_zones = st.multiselect(
                "Allowed geo zones",
                ["europe", "us_only", "global_remote", "apac", "latam", "unknown"],
                default=profile.allowed_geo_zones,
            )
            work_modes = st.multiselect(
                "Allowed work modes",
                ["remote", "hybrid", "on-site", "unknown"],
                default=profile.allowed_work_modes,
            )

            loc_kws = "\n".join(profile.location_keywords)
            location_keywords_str = st.text_area(
                "Location keywords (one per line)", value=loc_kws, height=80,
            )

            boost_kws = "\n".join(profile.boost_keywords)
            boost_keywords_str = st.text_area(
                "Boost keywords (one per line)", value=boost_kws, height=80,
            )

            company_sizes = st.multiselect(
                "Company sizes",
                ["startup", "scaleup", "sme", "large"],
                default=profile.company_sizes,
            )
            score_threshold = st.slider(
                "Score threshold", 1, 10, profile.score_threshold,
            )

            allowed_countries = st.multiselect(
                "Allowed countries",
                COUNTRY_OPTIONS,
                default=profile.allowed_countries or [],
            )

            # Sectors: label → code
            sector_labels = list(SECTOR_LABELS.keys())
            excluded_sector_labels = [
                _SECTOR_CODE_TO_LABEL.get(s, s) for s in profile.excluded_sectors
            ]
            excluded_sectors = st.multiselect(
                "Excluded sectors",
                sector_labels,
                default=[l for l in excluded_sector_labels if l in sector_labels],
            )
            excluded_sector_codes = [SECTOR_LABELS[l] for l in excluded_sectors]

            excluded_languages = st.multiselect(
                "Excluded languages",
                ["english", "french", "german", "italian", "spanish", "multiple"],
                default=profile.excluded_languages,
            )

            if st.form_submit_button("Save Profile", type="primary"):
                updated = SearchProfile(
                    id=selected,
                    name=name,
                    allowed_geo_zones=geo_zones,
                    allowed_work_modes=work_modes,
                    location_keywords=[k.strip() for k in location_keywords_str.split("\n") if k.strip()],
                    boost_keywords=[k.strip() for k in boost_keywords_str.split("\n") if k.strip()],
                    company_sizes=company_sizes,
                    score_threshold=score_threshold,
                    remote_or_hybrid=profile.remote_or_hybrid,
                    scoring_context=profile.scoring_context,
                    allowed_countries=allowed_countries or None,
                    banned_countries=profile.banned_countries,
                    hybrid_ok_countries=profile.hybrid_ok_countries,
                    denylisted_companies=profile.denylisted_companies,
                    excluded_sectors=excluded_sector_codes,
                    excluded_languages=excluded_languages,
                    pre_filter=profile.pre_filter,
                )
                db.upsert_profile(updated)
                st.cache_data.clear()
                st.success(f"Profile '{selected}' saved.")
                st.rerun()


def _render_stats_actions(db):
    st.subheader("Database Stats")

    # Total counts
    with db._conn() as conn:
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        total_scored = conn.execute("SELECT COUNT(*) FROM job_scores").fetchone()[0]
        total_jobs_no_score = total_jobs - conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM job_scores"
        ).fetchone()[0]
        total_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        total_contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        unverified = conn.execute("SELECT COUNT(*) FROM contacts WHERE is_unverified = 1").fetchone()[0]
        total_interactions = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Jobs", total_jobs, f"{total_scored} scored")
    c2.metric("Companies", total_companies)
    c3.metric("Contacts", total_contacts, f"{unverified} unverified")
    c4.metric("Interactions", total_interactions)

    # Contacts by role_family
    with db._conn() as conn:
        by_role = conn.execute(
            "SELECT role_family, COUNT(*) FROM contacts GROUP BY role_family ORDER BY COUNT(*) DESC"
        ).fetchall()
    if by_role:
        st.caption("Contacts by role: " + " · ".join(
            f"**{r[0] or 'unknown'}**: {r[1]}" for r in by_role
        ))

    st.divider()

    # Actions
    st.subheader("Actions")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔄 Clear Cache", use_container_width=True):
            st.cache_data.clear()
            st.success("Cache cleared.")
            st.rerun()

    with c2:
        if st.button("🔍 Re-extract Job Fields", use_container_width=True):
            with st.spinner("Running score.py --extract ..."):
                result = subprocess.run(
                    [sys.executable, "score.py", "--extract"],
                    capture_output=True, text=True, timeout=600,
                )
                st.text_area("Output", result.stdout + "\n" + result.stderr, height=200)
                st.cache_data.clear()

render()
