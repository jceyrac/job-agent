"""tracker_views/settings.py — Profile management, stats, and actions."""
import os
import platform
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
    ("DEEPSEEK_API_KEY", "DeepSeek API key (LLM extraction + scoring)", True),
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
    """Read all currently available data from a subprocess pipe without blocking.

    Uses select() on Unix/macOS (supports arbitrary file descriptors).
    Falls back to a non-blocking thread-based read on Windows, where
    select() only works with sockets.
    """
    if platform.system() == "Windows":
        # Windows: read with a short timeout via a thread so we don't block
        import threading
        chunks = []

        def _reader():
            try:
                chunk = pipe.read(65536)
                if chunk:
                    chunks.append(chunk)
            except (ValueError, OSError):
                pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=0.05)   # 50 ms — fast enough for live output polling
        return b"".join(chunks).decode("utf-8", errors="replace")
    else:
        import select as _select
        import os as _os
        chunks = []
        fd = pipe.fileno()
        while True:
            ready, _, _ = _select.select([pipe], [], [], 0)
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


def _render_purge(db):
    """Retention period widget + live preview of purgeable job count."""
    st.subheader("🧹 DB Purge")

    retention = int(db.get_config("purge_retention_days", default="30"))
    new_retention = st.number_input(
        "Retention period (days)",
        min_value=7, max_value=180, value=retention,
        help="Jobs older than this many days with status 'new' and no notes "
             "are automatically purged at the start of each pipeline run.",
    )
    if new_retention != retention:
        if st.button("Save retention", key="save_retention"):
            db.set_config("purge_retention_days", str(new_retention))
            st.success(f"Retention set to {new_retention} days.")
            st.rerun()

    purgeable = db.count_purgeable_jobs(new_retention)
    st.caption(
        f"ℹ️ With this setting, {purgeable} job(s) would be purged "
        f"on next run (stale, untouched)."
    )


def render():
    ensure_db()
    db = get_db()

    st.title("⚙️ Settings")
    _render_setup(db)
    st.divider()
    _render_profile_editor(db)
    st.divider()
    _render_broad_scraping(db)
    st.divider()
    _render_company_monitoring(db)
    st.divider()
    _render_purge(db)
    st.divider()
    _render_reonboard(db)


def _render_company_monitoring(db):
    """Company-keyed ATS sources. Each provider can be paused without losing
    its company list (non-destructive master switch with cascade re-arm)."""
    from scrape import discover_scrapers
    from tracker_views.shared import load_all_monitorable_companies

    st.subheader("📡 Company Monitoring")
    st.caption("Company-keyed ATS sources. Each provider can be paused without losing its company list.")

    # ── Pipeline toggle ──────────────────────────────────────────────────
    mon_cfg = db.get_config("monitoring.enabled_in_pipeline")
    mon_enabled = mon_cfg is None or mon_cfg.lower() == "true"
    scrape_cfg = db.get_config("scrape.enabled_in_pipeline")
    scrape_enabled = scrape_cfg is None or scrape_cfg.lower() == "true"

    new_mon = st.checkbox(
        "Include monitored-company scrape in the full pipeline run",
        value=mon_enabled,
        key="monitoring_enabled_in_pipeline",
        help="When on, `python main.py` runs `scrape.py --monitored-only` as part of the pipeline.",
    )
    if new_mon != mon_enabled:
        if not new_mon and not scrape_enabled:
            st.warning("Cannot disable both scrape sources — at least one must remain active.")
        else:
            db.set_config("monitoring.enabled_in_pipeline",
                          "true" if new_mon else "false")
            st.rerun()

    # ── Per-provider monitoring ──────────────────────────────────────────
    scraper_classes = sorted(discover_scrapers(), key=lambda c: c.SOURCE_NAME)
    company_keyed = [c for c in scraper_classes if getattr(c, "ACQUISITION_MODEL", "board") == "company_keyed"]

    all_companies = load_all_monitorable_companies(db)
    if not all_companies:
        st.caption("No monitorable companies found. Add companies on the Companies page.")
        return

    # Group monitorable companies by ats_provider
    by_provider: dict[str, list[dict]] = {}
    for comp in all_companies:
        provider = (comp.get("ats_provider") or "").strip().lower()
        if provider:
            by_provider.setdefault(provider, []).append(comp)

    for ScraperCls in company_keyed:
        name = ScraperCls.SOURCE_NAME
        provider_key = name.lower()
        companies = by_provider.get(provider_key, [])

        n_monitorable = len(companies)
        n_monitored = sum(1 for c in companies if c.get("monitored"))

        with st.expander(f"{name} — {n_monitored} monitored / {n_monitorable} monitorable"):
            # Master switch
            gate = db.get_config(f"monitoring.ats.{provider_key}.enabled")
            gate_enabled = gate is None or gate.lower() == "true"

            new_gate = st.checkbox(
                f"Monitor {name} in the pipeline",
                value=gate_enabled,
                key=f"monitoring_ats_{provider_key}",
                help="Non-destructive — pausing preserves all company monitored flags.",
            )
            if new_gate != gate_enabled:
                db.set_config(f"monitoring.ats.{provider_key}.enabled",
                              "true" if new_gate else "false")
                st.rerun()

            # Monitored companies list
            monitored = [c for c in companies if c.get("monitored")]
            if monitored:
                st.caption(f"**{len(monitored)} monitored:**")
                for comp in monitored:
                    cname = comp.get("name", comp.get("ats_identifier", "?"))
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.text(f"• {cname}")
                    with c2:
                        if st.button("🔕", key=f"unmonitor_{comp.get('id', comp.get('ats_identifier'))}",
                                     help=f"Stop monitoring {cname}"):
                            # Use existing storage method to clear monitored flag
                            with db._conn() as conn:
                                conn.execute(
                                    "UPDATE companies SET monitored = 0 WHERE id = ?",
                                    (comp.get("id"),),
                                )
                            st.cache_data.clear()
                            st.rerun()
            else:
                st.caption("No monitored companies for this provider.")

            # Add-to-monitoring dropdown
            unmonitored = [c for c in companies if not c.get("monitored")]
            if unmonitored:
                options = {f"{c.get('name', c.get('ats_identifier', '?'))}": c for c in unmonitored}
                selected_label = st.selectbox(
                    "Add a monitorable company to monitoring",
                    [""] + list(options.keys()),
                    key=f"add_mon_{provider_key}",
                )
                if selected_label and st.button("➕ Monitor", key=f"btn_mon_{provider_key}"):
                    comp = options[selected_label]
                    with db._conn() as conn:
                        conn.execute(
                            "UPDATE companies SET monitored = 1 WHERE id = ?",
                            (comp.get("id"),),
                        )
                    # Cascade re-arm: flip master switch back on if paused
                    if not gate_enabled:
                        db.set_config(f"monitoring.ats.{provider_key}.enabled", "true")
                    st.cache_data.clear()
                    st.rerun()

    # Summary
    active = sum(1 for c in all_companies if c.get("monitored"))
    st.caption(f"{len(all_companies)} monitorable companies ({active} monitored)")
    st.caption("Add new companies or run ATS detection on the Companies page.")


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

        job_titles_input = st.text_area(
            "Job titles — one per line; used as search queries, the PM title gate, and the scoring pre-filter",
            value="\n".join(profile.job_titles),
            height=120,
            help="Single source of truth for all title matching. Drives LinkedIn/Indeed queries, Greenhouse filtering, scrape net, and SQL pre-filter.",
        )

        c1, c2 = st.columns(2)
        with c1:
            title_exclude_input = st.text_area(
                "Exclude titles containing — one per line",
                value="\n".join(profile.title_exclude),
                height=80,
                help="Drop jobs whose title contains any of these words (e.g. junior, intern).",
            )
        with c2:
            search_locations = st.text_area(
                "Scrape locations override (one per line — leave empty to search everywhere you accept jobs)",
                value="\n".join(profile.search_locations),
                height=80,
                help="Override where LinkedIn/Indeed search. Empty = derive from Geography by work mode below (all accepted countries).",
            )

        score_threshold = st.slider(
            "Score threshold", 1, 10, profile.score_threshold,
            help="Minimum score for a job to appear in the digest.",
        )

        c1, c2, c3 = st.columns(3)
        WORK_MODES = ["remote", "hybrid", "on-site", "unknown"]
        COMPANY_SIZES = ["startup", "scaleup", "sme", "large"]
        CONTRACT_TYPES = ["permanent", "freelance", "contract", "internship", "unknown"]

        with c1:
            allowed_work_modes = st.multiselect(
                "Allowed work modes", WORK_MODES, default=profile.allowed_work_modes,
            )
        with c2:
            company_sizes = st.multiselect(
                "Company sizes", COMPANY_SIZES, default=profile.company_sizes,
            )
        with c3:
            allowed_contract_types = st.multiselect(
                "Allowed contract types", CONTRACT_TYPES, default=profile.allowed_contract_types,
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
            st.markdown("**Geography by work mode**")
            st.caption("Acceptance filters — jobs outside these countries are rejected at Tier-0. Distinct from Scrape locations above, which only narrow the search query.")
            wmg = profile.work_mode_geography or {}
            onsite_countries = st.text_area(
                "On-site countries (one per line — where you can commute)",
                value="\n".join((wmg.get("on-site", {}) or {}).get("countries", [])),
                height=80,
            )
            hybrid_countries = st.text_area(
                "Hybrid countries (one per line)",
                value="\n".join((wmg.get("hybrid", {}) or {}).get("countries", [])),
                height=80,
            )
            remote_countries = st.text_area(
                "Remote countries (one per line — timezone-bounded)",
                value="\n".join((wmg.get("remote", {}) or {}).get("countries", [])),
                height=150,
            )
            GEO_ZONES = ["europe", "global_remote", "us_only", "apac", "latam", "unknown"]
            remote_geo_zones = st.multiselect(
                "Remote geo-zone fallback (used when a remote role's country is unknown)",
                GEO_ZONES,
                default=(wmg.get("remote", {}) or {}).get("geo_zones", []),
            )

            st.divider()

            c1, c2 = st.columns(2)
            with c1:
                languages_spoken_input = st.text_area(
                    "Languages you work in — one per line (e.g. french / english)",
                    value="\n".join(profile.languages_spoken),
                    height=80,
                    help="Positive allowlist. A role requiring a language not listed here is rejected at Tier-0. Empty = no language restriction.",
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

        # ── Advanced scrape inputs (expander) ────────────────────────────
        with st.expander("🕸 Advanced scrape inputs"):
            c1, c2 = st.columns(2)
            with c1:
                greenhouse_boards = st.text_area(
                    "Greenhouse boards (one per line)",
                    value="\n".join(profile.greenhouse_boards),
                    height=150,
                )
            with c2:
                pre_exclude_location = st.text_area(
                    "pre_filter: exclude_location_contains (one per line)",
                    value="\n".join(profile.pre_filter.get("exclude_location_contains", [])),
                    height=150,
                    help="Cheap pre-extraction filter — drops jobs whose location contains these terms before LLM spend. US terms are auto-included by default.",
                )

        # ── Save ────────────────────────────────────────────────────────
        if st.form_submit_button("💾 Save profile", use_container_width=True):
            # Load-mutate-save: only set the fields exposed on the form
            profile.name = name
            profile.score_threshold = score_threshold
            profile.allowed_work_modes = allowed_work_modes
            profile.company_sizes = company_sizes
            profile.scoring_context = scoring_context

            profile.job_titles = _textarea_to_list(job_titles_input)
            profile.title_exclude = _textarea_to_list(title_exclude_input)
            profile.search_locations = _textarea_to_list(search_locations)
            profile.allowed_contract_types = allowed_contract_types
            profile.languages_spoken = _textarea_to_list(languages_spoken_input)

            profile.work_mode_geography = {
                "on-site": {"countries": _textarea_to_list(onsite_countries)},
                "hybrid":  {"countries": _textarea_to_list(hybrid_countries)},
                "remote":  {
                    "countries": _textarea_to_list(remote_countries),
                    "geo_zones": remote_geo_zones,
                },
            }
            profile.denylisted_companies = _textarea_to_list(denylisted_companies)
            profile.excluded_sectors = [SECTOR_LABELS[k] for k in excluded_sectors]

            profile.greenhouse_boards = _textarea_to_list(greenhouse_boards)

            # Keep exclude_location_contains (cheap pre-extraction US dropper)
            profile.pre_filter["exclude_location_contains"] = _textarea_to_list(pre_exclude_location)

            db.upsert_profile(profile)
            st.cache_data.clear()
            st.success("Profile saved. Applies on next scrape/score run.")
            st.rerun()


def _render_broad_scraping(db):
    """Query-driven boards and discovery-capable ATS scrapers.
    Each toggle governs broad discovery only; monitoring has its own switches below."""
    from scrape import discover_scrapers

    st.subheader("🕸 Broad scraping")
    st.caption("Query-driven boards and discovery sweeps of known companies. "
               "Independent of the monitoring path below — toggles here do not affect "
               "monitored-company scraping, and vice versa.")

    # ── Pipeline toggle ──────────────────────────────────────────────────
    scrape_cfg = db.get_config("scrape.enabled_in_pipeline")
    scrape_enabled = scrape_cfg is None or scrape_cfg.lower() == "true"
    mon_cfg = db.get_config("monitoring.enabled_in_pipeline")
    mon_enabled = mon_cfg is None or mon_cfg.lower() == "true"

    new_scrape = st.checkbox(
        "Include broad scrape in the full pipeline run",
        value=scrape_enabled,
        key="scrape_enabled_in_pipeline",
        help="When on, `python main.py` runs the broad scrape as a pipeline step. "
             "At least one scrape source must remain active.",
    )
    if new_scrape != scrape_enabled:
        if not new_scrape and not mon_enabled:
            st.warning("Cannot disable both scrape sources — at least one must remain active.")
        else:
            db.set_config("scrape.enabled_in_pipeline",
                          "true" if new_scrape else "false")
            st.rerun()

    # ── Partition scrapers ───────────────────────────────────────────────
    scraper_classes = sorted(discover_scrapers(), key=lambda c: c.SOURCE_NAME)
    boards = [c for c in scraper_classes if getattr(c, "ACQUISITION_MODEL", "board") == "board"]
    company_keyed = [c for c in scraper_classes if getattr(c, "ACQUISITION_MODEL", "board") == "company_keyed"]
    discovery_ats = [c for c in company_keyed if getattr(c, "SUPPORTS_DISCOVERY", False)]

    toggles = boards + discovery_ats

    cols = st.columns(3)
    for i, ScraperCls in enumerate(toggles):
        name = ScraperCls.SOURCE_NAME
        key = ScraperCls.enabled_config_key()
        current_val = db.get_config(key)
        enabled = ScraperCls.ENABLED if current_val is None else current_val.lower() == "true"

        # Label discovery ATS with a suffix so user knows this governs broad sweep only
        label = f"{name} (discovery)" if ScraperCls in discovery_ats else name

        with cols[i % 3]:
            new_val = st.checkbox(label, value=enabled, key=f"scraper_toggle_{name}")
            if new_val != enabled:
                db.set_config(key, "true" if new_val else "false")
                st.rerun()

    if discovery_ats:
        st.caption("(discovery) = company-keyed ATS that also run in the broad sweep. "
                   "Their monitoring toggle is in the Company Monitoring section below.")


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
