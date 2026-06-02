"""tracker_views/settings.py — Profile management, stats, and actions."""
import os
import subprocess
import sys

import streamlit as st

from dotenv import load_dotenv
load_dotenv()

from tracker_views.shared import ensure_db, get_db, SECTOR_LABELS

SECRET_SECTOR_CODES = list(SECTOR_LABELS.values())

# ── Env vars the Setup section manages ──────────────────────────────────────
# (key, label, required)
_SETUP_ENV_VARS = [
    ("GROQ_API_KEY",     "Groq API key (primary scorer LLM)",        True),
    ("GEMINI_API_KEY",   "Gemini API key (scorer fallback)",         False),
    ("DEEPSEEK_API_KEY", "DeepSeek API key (extraction fallback)",   False),
]

_SETUP_EMAIL_VARS = [
    ("NOTIFY_TO",        "Notification recipient email",             False),
    ("GMAIL_FROM",       "Gmail sender address",                     False),
    ("GMAIL_APP_PASSWORD","Gmail app password",                      False),
]

_SETUP_ADVANCED_VARS = [
    ("JOPLIN_TOKEN",     "Joplin Web Clipper token",                 False),
    ("X_RAPIDAPI_KEY",   "RapidAPI key (Wellfound scraper)",         False),
]


def _upsert_env(key: str, value: str) -> None:
    """Upsert one KEY=VALUE line in .env, preserving all other lines."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    lines: list[str] = []
    found = False
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"{key}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)


def _textarea_to_list(value: str) -> list[str]:
    """Parse a text_area (one value per line) into a list, stripping blanks."""
    return [line.strip() for line in value.split("\n") if line.strip()]


def _render_run_controls(db):
    """Buttons to trigger scrape & score from the UI."""
    from profiles import DEFAULT_PROFILE_ID

    st.subheader("🚀 Run")

    # Unscored count
    with db._conn() as conn:
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        scored_distinct = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM job_scores"
        ).fetchone()[0]
        unscored = total_jobs - scored_distinct

    st.metric("Unscored jobs", unscored)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🕸 Run scrape", use_container_width=True,
                     help="Fetch new jobs from all enabled scrapers. Blocks the UI — this can take ~10–15 min."):
            with st.spinner("Scraping — this can take ~10–15 min, the page is blocked until it finishes…"):
                result = subprocess.run(
                    [sys.executable, "scrape.py"],
                    capture_output=True, text=True, timeout=1800,
                )
            st.text_area("Scrape output", result.stdout + "\n" + result.stderr, height=200)
            if result.returncode == 0:
                st.cache_data.clear()
                st.rerun()

    with c2:
        if st.button("🎯 Run scoring", use_container_width=True,
                     help="Score all unscored jobs for the active profile. Blocks the UI."):
            active_id = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)
            with st.spinner(f"Scoring [{active_id}] — this can take several minutes…"):
                result = subprocess.run(
                    [sys.executable, "score.py", "--profile", active_id],
                    capture_output=True, text=True, timeout=1800,
                )
            st.text_area("Scoring output", result.stdout + "\n" + result.stderr, height=200)
            if result.returncode == 0:
                st.cache_data.clear()
                st.rerun()

    st.caption("Runs block the UI (Streamlit single-thread). Background execution is a future enhancement.")


def _render_setup(db):
    """First-run Setup section: API keys, CV path, notification email.
    Secrets go ONLY to .env, never the DB, never the screen, never logs."""
    with st.expander("🔧 Setup", expanded=False):
        st.caption("Configure once — applies to all future runs. Secrets are stored in .env (gitignored).")

        # ── API keys ───────────────────────────────────────────────────
        st.write("**API keys**")

        for key, label, required in _SETUP_ENV_VARS:
            current = os.getenv(key)
            status = "✅ set" if current else ("❌ missing" if required else "⚪ not set")

            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                st.caption(f"{label}  _{status}_")
            with c2:
                new_val = st.text_input(
                    f"{key}_input", type="password",
                    placeholder="(paste new key)" if not current else "(set — re-enter to change)",
                    label_visibility="collapsed",
                )
            with c3:
                if st.button("Save", key=f"save_{key}"):
                    if new_val.strip():
                        _upsert_env(key, new_val.strip())
                        os.environ[key] = new_val.strip()
                        st.success(f"{key} saved to .env.")
                        st.rerun()

        # ── Email ──────────────────────────────────────────────────────
        st.divider()
        st.write("**Notification email**")

        for key, label, _required in _SETUP_EMAIL_VARS:
            current = os.getenv(key)
            status = "✅ set" if current else "⚪ not set"
            is_secret = "PASSWORD" in key

            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                st.caption(f"{label}  _{status}_")
            with c2:
                kwargs: dict = dict(placeholder="(enter value)", label_visibility="collapsed")
                if is_secret:
                    kwargs["type"] = "password"
                    kwargs["placeholder"] = "(set — re-enter to change)" if current else "(enter value)"
                new_val = st.text_input(f"{key}_input", **kwargs)
            with c3:
                if st.button("Save", key=f"save_{key}"):
                    if new_val.strip():
                        _upsert_env(key, new_val.strip())
                        os.environ[key] = new_val.strip()
                        st.success(f"{key} saved to .env.")
                        st.rerun()

        # ── Advanced env vars ──────────────────────────────────────────
        with st.expander("Advanced keys"):
            for key, label, _required in _SETUP_ADVANCED_VARS:
                current = os.getenv(key)
                status = "✅ set" if current else "⚪ not set"
                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    st.caption(f"{label}  _{status}_")
                with c2:
                    new_val = st.text_input(
                        f"{key}_input", type="password",
                        placeholder="(set — re-enter to change)" if current else "(enter value)",
                        label_visibility="collapsed",
                    )
                with c3:
                    if st.button("Save", key=f"save_{key}"):
                        if new_val.strip():
                            _upsert_env(key, new_val.strip())
                            os.environ[key] = new_val.strip()
                            st.success(f"{key} saved to .env.")
                            st.rerun()

        # ── CV path ────────────────────────────────────────────────────
        st.divider()
        st.write("**CV / Resume**")
        cv_path = db.get_config("cv.master_path") or ""
        c1, c2 = st.columns([3, 1])
        with c1:
            new_cv = st.text_input(
                "cv_path_input",
                value=cv_path,
                placeholder="/path/to/your/cv.docx",
                label_visibility="collapsed",
            )
        with c2:
            if new_cv != cv_path and st.button("Save CV path"):
                db.set_config("cv.master_path", new_cv.strip())
                st.cache_data.clear()
                st.success("CV path saved.")
                st.rerun()
        if cv_path:
            if os.path.exists(cv_path):
                st.caption(f"📄 File found: {cv_path}")
            else:
                st.caption(f"⚠️ File not found: {cv_path}")


def render():
    ensure_db()
    db = get_db()

    st.title("⚙️ Settings")
    _render_setup(db)
    _render_run_controls(db)
    _render_profile_editor(db)
    _render_scraper_toggles(db)
    _render_stats_actions(db)


def _render_profile_editor(db):
    """Editable form for the active search profile.  Load-mutate-save."""
    from profiles import load_active_profile

    st.subheader("🎯 Profile Editor")
    profile = load_active_profile(db)

    with st.form("profile_editor"):
        # ── Search ──────────────────────────────────────────────────────
        st.caption("Search")

        name = st.text_input("Profile name", value=profile.name)

        c1, c2 = st.columns(2)
        with c1:
            search_query_titles = st.text_area(
                "Search query titles (one per line)",
                value="\n".join(profile.search_query_titles),
                height=120,
                help="Queries sent to LinkedIn, Indeed, and other jobspy-based scrapers.",
            )
        with c2:
            search_locations = st.text_area(
                "Search locations (one per line)",
                value="\n".join(profile.search_locations),
                height=120,
                help="Location display names for jobspy. Indeed is auto-mapped to country slugs.",
            )

        score_threshold = st.slider(
            "Score threshold", 1, 10, profile.score_threshold,
            help="Minimum score for a job to appear in the digest.",
        )

        c1, c2, c3 = st.columns(3)
        WORK_MODES = ["remote", "hybrid", "on-site", "unknown"]
        GEO_ZONES = ["europe", "global_remote", "us_only", "apac", "latam", "unknown"]
        COMPANY_SIZES = ["startup", "scaleup", "sme", "large"]

        with c1:
            allowed_work_modes = st.multiselect(
                "Allowed work modes", WORK_MODES, default=profile.allowed_work_modes,
            )
        with c2:
            allowed_geo_zones = st.multiselect(
                "Allowed geo zones", GEO_ZONES, default=profile.allowed_geo_zones,
            )
        with c3:
            company_sizes = st.multiselect(
                "Company sizes", COMPANY_SIZES, default=profile.company_sizes,
            )

        # ── Scoring ─────────────────────────────────────────────────────
        st.divider()
        st.caption("Scoring")
        scoring_context = st.text_area(
            "Scoring context (injected at top of LLM scorer system prompt)",
            value=profile.scoring_context,
            height=400,
        )

        # ── Countries & filters (expander) ──────────────────────────────
        with st.expander("🌍 Countries & filters"):
            c1, c2 = st.columns(2)
            with c1:
                allowed_countries = st.text_area(
                    "Allowed countries (one per line; empty = no restriction)",
                    value="\n".join(profile.allowed_countries) if profile.allowed_countries else "",
                    height=150,
                )
                banned_countries = st.text_area(
                    "Banned countries (one per line)",
                    value="\n".join(profile.banned_countries),
                    height=120,
                )
                hybrid_ok_countries = st.text_area(
                    "Hybrid-ok countries (one per line)",
                    value="\n".join(profile.hybrid_ok_countries),
                    height=120,
                )
            with c2:
                denylisted_companies = st.text_area(
                    "Denylisted companies (one per line)",
                    value="\n".join(profile.denylisted_companies),
                    height=150,
                )
                excluded_sectors = st.multiselect(
                    "Excluded sectors",
                    list(SECTOR_LABELS.keys()),
                    default=[k for k, v in SECTOR_LABELS.items() if v in profile.excluded_sectors],
                )
                excluded_languages = st.text_area(
                    "Excluded languages (one per line — e.g. german, spanish)",
                    value="\n".join(profile.excluded_languages),
                    height=120,
                )

        # ── Scrape net & advanced (expander) ────────────────────────────
        with st.expander("🕸 Scrape net & advanced"):
            c1, c2 = st.columns(2)
            with c1:
                scrape_titles = st.text_area(
                    "Scrape titles (one per line)",
                    value="\n".join(profile.scrape_titles),
                    height=150,
                )
                scrape_exclude = st.text_area(
                    "Scrape exclude (one per line)",
                    value="\n".join(profile.scrape_exclude),
                    height=100,
                )
                greenhouse_boards = st.text_area(
                    "Greenhouse boards (one per line)",
                    value="\n".join(profile.greenhouse_boards),
                    height=150,
                )
                boost_keywords = st.text_area(
                    "Boost keywords (one per line)",
                    value="\n".join(profile.boost_keywords),
                    height=150,
                )
            with c2:
                pre_title_contains = st.text_area(
                    "pre_filter: title_contains (one per line)",
                    value="\n".join(profile.pre_filter.get("title_contains", [])),
                    height=120,
                )
                pre_exclude_title = st.text_area(
                    "pre_filter: exclude_title_contains (one per line)",
                    value="\n".join(profile.pre_filter.get("exclude_title_contains", [])),
                    height=120,
                )
                pre_exclude_location = st.text_area(
                    "pre_filter: exclude_location_contains (one per line)",
                    value="\n".join(profile.pre_filter.get("exclude_location_contains", [])),
                    height=150,
                )

        # ── Save ────────────────────────────────────────────────────────
        if st.form_submit_button("💾 Save profile", use_container_width=True):
            # Load-mutate-save: only set the fields exposed on the form
            profile.name = name
            profile.score_threshold = score_threshold
            profile.allowed_work_modes = allowed_work_modes
            profile.allowed_geo_zones = allowed_geo_zones
            profile.company_sizes = company_sizes
            profile.scoring_context = scoring_context

            profile.search_query_titles = _textarea_to_list(search_query_titles)
            profile.search_locations = _textarea_to_list(search_locations)

            profile.allowed_countries = _textarea_to_list(allowed_countries) or None
            profile.banned_countries = _textarea_to_list(banned_countries)
            profile.hybrid_ok_countries = _textarea_to_list(hybrid_ok_countries)
            profile.denylisted_companies = _textarea_to_list(denylisted_companies)
            profile.excluded_sectors = [SECTOR_LABELS[k] for k in excluded_sectors]
            profile.excluded_languages = _textarea_to_list(excluded_languages)

            profile.scrape_titles = _textarea_to_list(scrape_titles)
            profile.scrape_exclude = _textarea_to_list(scrape_exclude)
            profile.greenhouse_boards = _textarea_to_list(greenhouse_boards)
            profile.boost_keywords = _textarea_to_list(boost_keywords)

            # Mutate pre_filter keys without replacing the whole dict
            profile.pre_filter["title_contains"] = _textarea_to_list(pre_title_contains)
            profile.pre_filter["exclude_title_contains"] = _textarea_to_list(pre_exclude_title)
            profile.pre_filter["exclude_location_contains"] = _textarea_to_list(pre_exclude_location)

            db.upsert_profile(profile)
            st.cache_data.clear()
            st.success("Profile saved. Applies on next scrape/score run.")
            st.rerun()


def _render_scraper_toggles(db):
    """Enable/disable individual scrapers via their config keys."""
    from scrape import discover_scrapers

    st.divider()
    st.subheader("🔌 Scraper Toggles")

    scraper_classes = sorted(discover_scrapers(), key=lambda c: c.SOURCE_NAME)

    # Crypto/Web3 scrapers a non-Web3 user might want to disable
    CRYPTO_WEB3_NAMES = {
        "Web3Career", "CryptoJobs.com", "CryptoJobsList", "DeFi Jobs",
        "BeInCrypto", "Greenhouse",
    }

    cols = st.columns(3)
    for i, ScraperCls in enumerate(scraper_classes):
        name = ScraperCls.SOURCE_NAME
        key = ScraperCls.enabled_config_key()
        current_val = db.get_config(key)
        # None → use class default (ENABLED); otherwise parse "true"/"false"
        enabled = ScraperCls.ENABLED if current_val is None else current_val.lower() == "true"

        label = name
        if name in CRYPTO_WEB3_NAMES:
            label = f"{name} 🪙"

        with cols[i % 3]:
            new_val = st.checkbox(label, value=enabled, key=f"scraper_toggle_{name}")
            if new_val != enabled:
                db.set_config(key, "true" if new_val else "false")
                st.rerun()

    st.caption("🪙 = crypto / Web3 scrapers — toggle off if targeting non-Web3 roles.")


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

from tracker_views.shared import is_active_page
if is_active_page(__file__):
    render()
