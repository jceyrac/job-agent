"""tracker_views/shared.py — constants, cached data loaders, badges, and nav helpers."""

import os
from datetime import date, timedelta

import streamlit as st

from paths import DB_PATH
from storage import JobStorage

# ── Constants ───────────────────────────────────────────────────────────────────

COUNTRY_OPTIONS = [
    "Switzerland", "Germany", "France", "United Kingdom", "Ireland",
    "Netherlands", "Belgium", "Luxembourg", "Spain", "Portugal",
    "Italy", "Austria", "Sweden", "Norway", "Denmark", "Finland",
    "Poland", "Czechia", "Romania", "Estonia", "Lithuania", "Greece",
    "United States", "Canada", "Singapore", "Israel",
    "United Arab Emirates", "Australia", "Japan",
]

SECTOR_LABELS = {
    "Web3 / Crypto":              "web3_crypto",
    "Fintech":                    "fintech",
    "Tech / SaaS":                "tech_saas",
    "AI / ML":                    "ai_ml",
    "E-commerce":                 "e_commerce",
    "Healthcare":                 "healthcare",
    "Pharma":                     "pharma",
    "Retail / FMCG":              "retail",
    "Manufacturing / Industrial": "manufacturing",
    "Government / Public sector": "government",
    "Consulting":                 "consulting",
    "Education":                  "education",
    "Media / Entertainment":      "media",
    "Energy / Utilities":         "energy",
    "Other":                      "other",
}
_SECTOR_CODE_TO_LABEL = {v: k for k, v in SECTOR_LABELS.items()}

COUNTRY_FLAG = {
    "Switzerland": "🇨🇭", "Germany": "🇩🇪", "France": "🇫🇷",
    "United Kingdom": "🇬🇧", "Ireland": "🇮🇪", "Netherlands": "🇳🇱",
    "Belgium": "🇧🇪", "Luxembourg": "🇱🇺", "Spain": "🇪🇸", "Portugal": "🇵🇹",
    "Italy": "🇮🇹", "Austria": "🇦🇹", "Sweden": "🇸🇪", "Norway": "🇳🇴",
    "Denmark": "🇩🇰", "Finland": "🇫🇮", "Poland": "🇵🇱", "Czechia": "🇨🇿",
    "Romania": "🇷🇴", "Estonia": "🇪🇪", "Lithuania": "🇱🇹",
    "United States": "🇺🇸", "Canada": "🇨🇦", "Singapore": "🇸🇬",
    "Israel": "🇮🇱", "United Arab Emirates": "🇦🇪",
    "Australia": "🇦🇺", "Japan": "🇯🇵",
}

COMPANY_STATUSES = [
    "prospect", "watching", "active_outreach", "engaged",
    "dormant", "passed_by_me", "declined_by_them", "blacklisted",
]

from storage import (
    CONTACT_ROLE_FAMILIES, CONTACT_SENIORITIES,
    INTERACTION_TYPES, INTERACTION_DIRECTIONS, INTERACTION_OUTCOMES,
)

_ENGAGED_STATUSES = {"applied", "rejected", "archived", "saved", "queued", "ready"}
_STALE_CUTOFF_DAYS = 30

_DATE_FILTER_DAYS = {
    "1 day": 1, "3 days": 3, "1 week": 7,
    "2 weeks": 14, "3 weeks": 21, "1 month": 30,
}

# ── DB singleton ────────────────────────────────────────────────────────────────

@st.cache_resource
def get_db() -> JobStorage:
    """Return a cached JobStorage instance (survives reruns)."""
    return JobStorage(DB_PATH)


def ensure_db():
    """Check DB exists. If not, show warning and stop."""
    if not os.path.exists(DB_PATH):
        st.warning("No data yet. Run scrape.py first.")
        st.stop()


# ── Secret management (shared by Settings and onboarding) ────────────────────

def env_is_set(key: str) -> bool:
    """Return True if the env var is set and non-empty."""
    return bool(os.getenv(key))


def upsert_env(key: str, value: str) -> None:
    """Upsert one KEY=VALUE line in repo-root .env, preserving all other lines."""
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


def set_secret(key: str, value: str) -> None:
    """Persist a secret to .env AND os.environ, then refresh the scorer's Groq
    client so in-process callers (wizard, Settings) see it immediately.
    Never logs or returns the value."""
    value = value.strip()
    upsert_env(key, value)
    os.environ[key] = value
    try:
        from scorer import reload_client  # local import: avoid load cost/cycle
        reload_client()
    except Exception:
        pass  # scorer import/refresh best-effort; .env+environ already set


# ── Cached data loaders ─────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_jobs(profile_id: str | None = None,
              exclude_archived: bool = False) -> list[dict]:
    from profiles import get_active_profile
    if profile_id is None:
        profile_id = get_active_profile().id
    return get_db().get_all_for_tracker(profile_id, exclude_archived=exclude_archived)


@st.cache_data(ttl=60, show_spinner=False)
def load_companies(
    status: list[str] | None = None,
    search: str | None = None,
    exclude_blacklisted: bool = True,
    countries: tuple[str, ...] = (),
    sectors: tuple[str, ...] = (),
    sizes: tuple[str, ...] = (),
    min_job_count: int | None = None,
    last_interaction_within_days: int | None = None,
) -> list[dict]:
    return get_db().get_companies(
        status=status, search=search, exclude_blacklisted=exclude_blacklisted,
        countries=list(countries) or None,
        sectors=list(sectors) or None,
        sizes=list(sizes) or None,
        min_job_count=min_job_count,
        last_interaction_within_days=last_interaction_within_days,
    )


@st.cache_data(ttl=60, show_spinner=False)
def load_contacts(
    company_id: int | None = None,
    search: str | None = None,
    is_unverified: bool | None = None,
    role_family: str | None = None,
    exclude_blacklisted: bool = True,
) -> list[dict]:
    return get_db().get_all_contacts(
        company_id=company_id, search=search,
        is_unverified=is_unverified, role_family=role_family,
        exclude_blacklisted_companies=exclude_blacklisted,
    )


@st.cache_data(ttl=60, show_spinner=False)
def load_dashboard_data() -> dict:
    return get_db().get_dashboard_data()


@st.cache_data(ttl=60, show_spinner=False)
def load_applications_index() -> dict[str, dict]:
    rows = get_db().get_all_applications()
    return {r["job_id"]: r for r in rows}


@st.cache_data(ttl=60, show_spinner=False)
def load_profiles() -> list[dict]:
    return get_db().get_all_profiles()


@st.cache_data(ttl=60, show_spinner=False)
def load_company_by_id(company_id: int) -> dict | None:
    return get_db().get_company_by_id(company_id)


@st.cache_data(ttl=60, show_spinner=False)
def load_jobs_for_company(company_id: int) -> list[dict]:
    return get_db().get_jobs_for_company(company_id)


# ── Badges ──────────────────────────────────────────────────────────────────────

def score_badge(score) -> str:
    if score is None:
        return "❓ —/10"
    if score >= 9:
        return f"🔥 {score}/10"
    elif score >= 7:
        return f"⭐ {score}/10"
    return f"👀 {score}/10"


def company_status_badge(status: str) -> str:
    colors = {
        "prospect":          "⚪",
        "watching":          "👀",
        "active_outreach":   "📤",
        "engaged":           "💬",
        "dormant":           "💤",
        "passed_by_me":      "🚫",
        "declined_by_them":  "❌",
        "blacklisted":       "⛔",
    }
    emoji = colors.get(status, "❓")
    return f"{emoji} {status.replace('_', ' ')}"


def relationship_badge(status: str) -> str:
    badges = {
        "offer":           "🏆 Offer",
        "interviewing":    "🎯 Interviewing",
        "applied":         "📝 Applied",
        "replied":         "💬 Replied",
        "cold_contacted":  "📤 Contacted",
        "engaged":         "🤝 Engaged",
        "declined":        "🚫 Declined",
        "none":            "—",
    }
    return badges.get(status, status)


def sector_label(code: str) -> str:
    return _SECTOR_CODE_TO_LABEL.get(code, code)


def unverified_badge() -> str:
    return "⚠️ Unverified"


# ── Filters ─────────────────────────────────────────────────────────────────────

def _is_stale_unengaged(j: dict) -> bool:
    if j.get("status") in _ENGAGED_STATUSES:
        return False
    raw = j.get("posted_date") or ""
    try:
        return date.fromisoformat(str(raw)[:10]) < date.today() - timedelta(days=_STALE_CUTOFF_DAYS)
    except (ValueError, TypeError):
        return False


def apply_filters(
    jobs: list[dict],
    *,
    min_score: int = 0,
    show_stale: bool = False,
    date_filter: str = "Any",
    scraped_filter: str = "Any",
    location_filter: list[str] | None = None,
    work_mode_filter: list[str] | None = None,
    geo_zone_filter: list[str] | None = None,
    country_code_filter: list[str] | None = None,
    company_size_filter: list[str] | None = None,
    sector_filter: list[str] | None = None,
    language_filter: list[str] | None = None,
    source_filter: list[str] | None = None,
    status_filter: list[str] | None = None,
    show_archived_view: bool = False,
) -> list[dict]:
    """Apply all filters sequentially. Parameterized so it's reusable across pages."""
    if not show_stale:
        jobs = [j for j in jobs if not _is_stale_unengaged(j)]
    result = [j for j in jobs if
              j.get("status") == "unscored" or (j.get("score") or 0) >= min_score]
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
    if scraped_filter and scraped_filter != "Any":
        max_days = _DATE_FILTER_DAYS.get(scraped_filter)
        if max_days:
            cutoff = date.today() - timedelta(days=max_days)
            filtered = []
            for j in result:
                raw = j.get("last_seen") or ""
                try:
                    if date.fromisoformat(str(raw)[:10]) >= cutoff:
                        filtered.append(j)
                except (ValueError, TypeError):
                    pass
            result = filtered
    if location_filter:
        result = [j for j in result if j.get("location") in location_filter]
    if work_mode_filter:
        result = [j for j in result if (j.get("work_mode") or "unknown") in work_mode_filter]
    if geo_zone_filter:
        result = [j for j in result if (j.get("geo_zone") or "unknown") in geo_zone_filter]
    if country_code_filter:
        result = [j for j in result if (j.get("country_code") or "") in country_code_filter]
    if company_size_filter:
        result = [j for j in result if (j.get("company_size") or "unknown") in company_size_filter]
    if sector_filter:
        result = [j for j in result if (j.get("industry_sector") or "other") in sector_filter]
    if language_filter:
        result = [j for j in result if (j.get("language_required") or "unknown") in language_filter]
    if source_filter:
        result = [j for j in result if j.get("source") in source_filter]
    if show_archived_view:
        result = [j for j in result if j.get("status") == "archived"]
    elif status_filter:
        result = [j for j in result if j.get("status") in status_filter]
    return result


# ── Page guard ──────────────────────────────────────────────────────────────────

def is_active_page(file_path: str) -> bool:
    """Check if *file_path* is the currently executing Streamlit page.

    Usage in each tracker_views/*.py at module level::

        from tracker_views.shared import is_active_page
        if is_active_page(__file__):
            render()
    """
    import os
    from streamlit.runtime.scriptrunner import get_script_run_ctx

    ctx = get_script_run_ctx()
    if ctx is None:
        return False
    script_path = getattr(ctx, "page_script_path", None)
    if script_path is None:
        # Older Streamlit that doesn't expose page_script_path — assume active
        return True
    return os.path.basename(script_path) == os.path.basename(file_path)


# ── Navigation helpers ──────────────────────────────────────────────────────────

def nav_to_entity(entity_id: int | str):
    """Set query_params so the current page renders detail view."""
    st.query_params["id"] = str(entity_id)
    st.session_state["detail_id"] = str(entity_id)


def clear_detail():
    """Clear query_params to return to list view."""
    st.session_state["_jobs_detail_ts"] = 0
    if "id" in st.query_params:
        del st.query_params["id"]
    st.session_state.pop("detail_id", None)


def get_detail_id() -> str | None:
    """Get the current detail ID, or None if in list mode.

    Uses st.session_state as a fallback because st.query_params is not
    available during every Streamlit execution pass (e.g. initial script
    compilation or fragment reruns).
    """
    qp_id = st.query_params.get("id") if st.query_params else None
    ss_id = st.session_state.get("detail_id")
    result = qp_id or ss_id
    if qp_id:
        st.session_state["detail_id"] = qp_id
    return result


def md_link(text: str, url: str) -> str:
    """Markdown link that escapes parentheses in the link text."""
    safe = text.replace("(", "&#40;").replace(")", "&#41;")
    return f"[{safe}]({url})"


# ── Channel link renderers ──────────────────────────────────────────────────────

def email_link(email: str) -> str:
    return f"[📧 {email}](mailto:{email})"


def linkedin_link(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1] if url else ""
    return f"[🔗 LinkedIn]({url})" if url else ""


def x_link(handle: str) -> str:
    if not handle:
        return ""
    h = handle.lstrip("@")
    return f"[𝕏 @{h}](https://x.com/{h})"


def telegram_link(handle: str) -> str:
    if not handle:
        return ""
    h = handle.lstrip("@")
    return f"[📱 @{h}](https://t.me/{h})"


def github_link(handle: str) -> str:
    if not handle:
        return ""
    h = handle.lstrip("@")
    return f"[🐙 {h}](https://github.com/{h})"


def phone_link(phone: str) -> str:
    if not phone:
        return ""
    return f"[📞 {phone}](tel:{phone})"


# ---------------------------------------------------------------------------
# Monitoring helpers (Phase 8a)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=30)
def load_all_monitorable_companies(_db) -> list[dict]:
    """Cached loader for the monitorable-company set used by Settings and filters."""
    return _db.get_all_monitorable_companies()


def _scraper_slug(source_name: str) -> str:
    """Mirrors BaseScraper._config_prefix() for lightweight UI config lookups."""
    slug = source_name.lower().replace(" ", "_").replace(":", "_")
    return f"scraper.{slug}"


def _read_config_bool(db, key: str) -> bool | None:
    val = db.get_config(key)
    if val is None:
        return None
    return val.lower() == "true"


def is_monitoring_source_enabled(db, company: dict) -> bool:
    """Return False only when the company's scraper is explicitly disabled via config.
    Defaults to True when the config key is absent (scraper is enabled).

    company must have 'ats_provider' — we derive the slug from it, or from the
    SOURCE_NAME of the dedicated scraper.
    """
    provider = (company.get("ats_provider") or "").strip()
    if not provider:
        # Dedicated scraper: look up scraper_id
        sid = company.get("scraper_id")
        if not sid:
            return True  # shouldn't happen since we filter before calling
        provider = sid
    slug = _scraper_slug(provider)
    enabled_val = db.get_config(f"{slug}.enabled")
    if enabled_val is None:
        return True  # not explicitly disabled → enabled
    return enabled_val.lower() == "true"


def monitoring_badge(company: dict) -> str:
    """Return an HTML badge string for the company's monitoring status, or ''."""
    ats = company.get("ats_provider")
    sid = company.get("scraper_id")
    if not ats and not sid:
        return ""  # not monitorable
    provider = ats or sid or "?"
    monitored = company.get("monitored")
    if monitored:
        return (f'<span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;'
                f'border-radius:4px;font-size:12px;font-weight:600">'
                f'🟢 Monitored · {provider}</span>')
    else:
        return (f'<span style="background:#f5f5f5;color:#666;padding:2px 8px;'
                f'border-radius:4px;font-size:12px;font-weight:600">'
                f'⚪ Monitorable · {provider}</span>')
