"""tracker_views/settings.py — Profile management, stats, and actions."""
import os
import select
import subprocess
import sys
import time
from urllib.parse import urlparse

import streamlit as st

from dotenv import load_dotenv
load_dotenv()

from tracker_views.shared import ensure_db, get_db, set_secret, SECTOR_LABELS

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


def _textarea_to_list(value: str) -> list[str]:
    """Parse a text_area (one value per line) into a list, stripping blanks."""
    return [line.strip() for line in value.split("\n") if line.strip()]


def _read_available(pipe) -> str:
    """Read all currently available data from a subprocess pipe without blocking."""
    import os as _os
    chunks = []
    fd = pipe.fileno()
    while True:
        ready, _, _ = select.select([pipe], [], [], 0)
        if not ready:
            break
        try:
            chunk = _os.read(fd, 65536)
        except (ValueError, OSError):
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _render_run_controls(db):
    """Buttons to trigger scrape & score from the UI with stop capability."""
    from profiles import DEFAULT_PROFILE_ID

    st.subheader("🚀 Run")

    # ── Pipeline config checkboxes ───────────────────────────────────────
    c_p1, c_p2 = st.columns(2)
    with c_p1:
        scrape_cfg = db.get_config("scrape.enabled_in_pipeline")
        scrape_enabled = scrape_cfg is None or scrape_cfg.lower() == "true"
        new_scrape = st.checkbox(
            "Include broad scrape in the full pipeline run",
            value=scrape_enabled,
            key="scrape_enabled_in_pipeline",
            help="When off, `python main.py` skips the broad scrape step. "
                 "You can still run it on demand with the button below.",
        )
        if new_scrape != scrape_enabled:
            db.set_config("scrape.enabled_in_pipeline",
                          "true" if new_scrape else "false")
            st.rerun()
    with c_p2:
        mon_cfg = db.get_config("monitoring.enabled_in_pipeline")
        mon_enabled = mon_cfg is None or mon_cfg.lower() == "true"
        new_mon = st.checkbox(
            "Include monitored-company scrape in the full pipeline run",
            value=mon_enabled,
            key="monitoring_enabled_in_pipeline",
            help="When on and at least one company is monitored, `python main.py` "
                 "will also run `scrape.py --monitored-only` as part of the full "
                 "pipeline.",
        )
        if new_mon != mon_enabled:
            db.set_config("monitoring.enabled_in_pipeline",
                          "true" if new_mon else "false")
            st.rerun()

    # ── Session-state keys for background processes ──────────────────────
    if "bg_process" not in st.session_state:
        st.session_state.bg_process = None       # Popen | None
    if "bg_label" not in st.session_state:
        st.session_state.bg_label = ""            # "scrape" | "score"
    if "bg_output" not in st.session_state:
        st.session_state.bg_output = ""            # accumulated stdout+stderr
    if "bg_start" not in st.session_state:
        st.session_state.bg_start = 0.0

    proc = st.session_state.bg_process
    label = st.session_state.bg_label
    bg_output = st.session_state.bg_output
    bg_start = st.session_state.bg_start

    # ── Live metric (always fresh — recomputed on every render) ──────────
    with db._conn() as conn:
        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        scored_distinct = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM job_scores"
        ).fetchone()[0]
        unscored = total_jobs - scored_distinct
    st.metric("Unscored jobs", unscored)

    # ── If a process is running, show its status ─────────────────────────
    if proc is not None and proc.poll() is None:
        elapsed = int(time.time() - bg_start)
        mins, secs = divmod(elapsed, 60)
        st.info(f"⏳ **{label.title()}** running — {mins}m {secs}s elapsed")

        # Read any new output (stderr is merged into stdout via Popen)
        try:
            new_out = _read_available(proc.stdout)
            if new_out:
                st.session_state.bg_output += new_out
        except Exception:
            pass

        if st.session_state.bg_output:
            with st.expander("Live output", expanded=False):
                st.text(st.session_state.bg_output[-8000:])

        c1, c2 = st.columns([1, 3])
        with c1:
            if st.button(f"⏹ Stop {label}", use_container_width=True, type="primary"):
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                # Read any final output
                try:
                    remaining = proc.stdout.read()
                    if remaining:
                        st.session_state.bg_output += remaining.decode("utf-8", errors="replace")
                except Exception:
                    pass
                st.session_state.bg_process = None
                st.session_state.bg_label = ""
                st.warning(f"{label.title()} stopped.")
                st.rerun()
        with c2:
            if st.button("🔄 Refresh output", use_container_width=True):
                st.rerun()
        return

    # ── If a process just finished, show results ─────────────────────────
    if proc is not None and proc.poll() is not None:
        ret = proc.returncode
        # Read any remaining output (stderr is merged into stdout via Popen)
        try:
            remaining = proc.stdout.read()
            if remaining:
                st.session_state.bg_output += remaining.decode("utf-8", errors="replace")
        except Exception:
            pass

        final_output = st.session_state.bg_output

        # Clean up session state
        st.session_state.bg_process = None
        st.session_state.bg_label = ""
        st.session_state.bg_output = ""
        st.session_state.bg_start = 0.0

        if ret == 0:
            st.success(f"✅ {label.title()} completed successfully!")
        else:
            st.error(f"❌ {label.title()} exited with code {ret}")

        st.text_area(f"{label.title()} output", final_output, height=200)
        if ret == 0:
            st.cache_data.clear()
            st.rerun()
        return

    # ── No process running — show launch buttons ─────────────────────────
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("🕸 Run scrape", use_container_width=True,
                     help="Fetch new jobs from all enabled scrapers. Can be stopped."):
            proc = subprocess.Popen(
                [sys.executable, "-u", "scrape.py"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=False,  # binary mode for non-blocking reads
            )
            st.session_state.bg_process = proc
            st.session_state.bg_label = "scrape"
            st.session_state.bg_output = ""
            st.session_state.bg_start = time.time()
            st.rerun()

    with c2:
        if st.button("🎯 Run monitoring", use_container_width=True,
                     help="Run scrape.py --monitored-only. Fetches all openings "
                          "from monitored companies, independent of the broad scrape."):
            proc = subprocess.Popen(
                [sys.executable, "-u", "scrape.py", "--monitored-only"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=False,
            )
            st.session_state.bg_process = proc
            st.session_state.bg_label = "monitoring"
            st.session_state.bg_output = ""
            st.session_state.bg_start = time.time()
            st.rerun()

    with c3:
        if st.button("🎯 Run scoring", use_container_width=True,
                     help="Score all unscored jobs for the active profile. Can be stopped."):
            active_id = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)
            proc = subprocess.Popen(
                [sys.executable, "-u", "score.py", "--profile", active_id],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=False,
            )
            st.session_state.bg_process = proc
            st.session_state.bg_label = "score"
            st.session_state.bg_output = ""
            st.session_state.bg_start = time.time()
            st.rerun()


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
                        set_secret(key, new_val.strip())
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
                        set_secret(key, new_val.strip())
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
                            set_secret(key, new_val.strip())
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
    st.divider()
    _render_profile_editor(db)
    st.divider()
    _render_scraper_toggles(db)
    st.divider()
    _render_monitored_companies(db)
    st.divider()
    _render_reonboard(db)


def _render_monitored_companies(db):
    """Monitored companies management panel — three-state model with st.toggle."""
    from tracker_views.shared import (
        load_all_monitorable_companies,
        is_monitoring_source_enabled,
        monitoring_badge,
    )
    st.subheader("🎯 Monitored Companies")

    # ── Add company form ──
    with st.expander("➕ Add a company to monitor", expanded=False):
        careers_url = st.text_input(
            "Careers URL",
            placeholder="https://boards.greenhouse.io/fireblocks",
            key="mon_add_url",
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔍 Detect ATS", use_container_width=True,
                         disabled=not careers_url):
                try:
                    from ats_detection import resolve_scrape_method, DetectionError
                    result = resolve_scrape_method(careers_url)
                    st.success(
                        f"Detected: **{result['provider']}** "
                        f"→ `{result['identifier']}`"
                    )
                    name = urlparse(careers_url).hostname.replace("boards.", "")\
                        .replace("jobs.", "").split(".")[0].title()
                    company_name = st.text_input("Company name", value=name, key="mon_add_name")
                    if st.button("✅ Add & Monitor", use_container_width=True):
                        cid = db.upsert_company_from_detection(
                            company_name, careers_url,
                            result["provider"], result["identifier"])
                        db.set_company_monitored(cid, True)
                        st.success(f"Added **{company_name}** — monitoring active")
                        st.cache_data.clear()
                        st.rerun()
                except DetectionError as e:
                    st.warning(str(e))
                    with st.form("mon_manual_entry"):
                        st.caption("Manual entry:")
                        provider = st.selectbox(
                            "Provider",
                            ["greenhouse", "lever", "ashby", "workday",
                             "smartrecruiters", "workable"],
                        )
                        identifier = st.text_input("Identifier (token / board / account ID)")
                        company_name = st.text_input("Company name")
                        if st.form_submit_button("Add manually"):
                            try:
                                cid = db.upsert_company_from_detection(
                                    company_name, careers_url, provider, identifier)
                                db.set_company_monitored(cid, True)
                                st.success(f"Added **{company_name}**")
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as ex:
                                st.error(str(ex))
        with c2:
            pass

    # ── All monitorable companies list (fix: sourced from get_all_monitorable,
    #   not get_monitored — paused companies remain visible) ──
    all_monitorable = load_all_monitorable_companies(db)
    if not all_monitorable:
        st.info("No monitorable companies yet. Add one above!")
        return

    active_count = sum(1 for c in all_monitorable if c.get("monitored"))
    st.caption(f"{len(all_monitorable)} monitorable companies ({active_count} active)")

    for company in all_monitorable:
        cid = company["id"]
        name = company["name"]
        provider = company.get("ats_provider", "—")
        identifier = company.get("ats_identifier", "")
        is_monitored = bool(company.get("monitored"))
        source_enabled = is_monitoring_source_enabled(db, company)

        cols = st.columns([4, 2, 1])
        with cols[0]:
            badge_html = monitoring_badge(company)
            if badge_html:
                st.html(badge_html)
            st.markdown(f"**{name}**  \n`{provider}` → `{identifier}`")
        with cols[1]:
            if not source_enabled:
                st.caption("⚪ Scraper disabled")
            else:
                st.caption("Active" if is_monitored else "Paused")
        with cols[2]:
            if not source_enabled:
                st.toggle("Monitor", value=False, disabled=True,
                          key=f"mon_toggle_{cid}",
                          help=f"Enable the {provider} scraper in Settings to monitor this company.")
            else:
                new_val = st.toggle("Monitor", value=is_monitored,
                                    key=f"mon_toggle_{cid}")
                if new_val != is_monitored:
                    db.set_company_monitored(cid, new_val)
                    st.cache_data.clear()
                    st.rerun()

    st.markdown("---")

def _render_reonboard(db):
    """Re-run the onboarding wizard to regenerate the profile."""
    with st.expander("🔄 Re-run onboarding / regenerate profile", expanded=False):
        st.caption(
            "Return to the onboarding wizard to regenerate your profile's "
            "scoring rubric.  Your current profile is NOT deleted until "
            "you save a new one in the wizard."
        )
        confirm = st.checkbox("Yes, I want to re-run the onboarding wizard.")
        if confirm and st.button("Re-run onboarding", use_container_width=True):
            db.set_config("onboarding_complete", "false")
            st.session_state.pop("onboarding_step", None)
            st.session_state.pop("q", None)
            st.session_state.pop("cv_text", None)
            st.session_state.pop("generated_criteria", None)
            st.switch_page("tracker_views/onboarding.py")


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
            "Scoring context",
            value=profile.scoring_context,
            height=400,
            help="Injected at the top of the LLM scorer system prompt. Describe your ideal role, priorities, and hard filters.",
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
