"""tracker_views/shared.py — constants, cached data loaders, badges, and nav helpers."""

import os
from datetime import date, timedelta

import streamlit as st

from profiles import ALL_PROFILES, DEFAULT_PROFILE_ID
from storage import JobStorage

# ── Constants ───────────────────────────────────────────────────────────────────

DB_PATH = "data/jobs.db"

COUNTRY_OPTIONS = [
    "Switzerland", "Germany", "France", "United Kingdom", "Ireland",
    "Netherlands", "Belgium", "Luxembourg", "Spain", "Portugal",
    "Italy", "Austria", "Sweden", "Norway", "Denmark", "Finland",
    "Poland", "Czechia", "Romania", "Estonia", "Lithuania",
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


# ── Cached data loaders ─────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_jobs(profile_id: str | None, exclude_archived: bool = False) -> list[dict]:
    db = get_db()
    if profile_id is None:
        return db.get_all_jobs_best_score(exclude_archived=exclude_archived)
    return db.get_all_for_tracker(profile_id, exclude_archived=exclude_archived)


@st.cache_data(ttl=60, show_spinner=False)
def load_companies(
    status: list[str] | None = None,
    search: str | None = None,
    exclude_blacklisted: bool = True,
) -> list[dict]:
    return get_db().get_companies(
        status=status, search=search, exclude_blacklisted=exclude_blacklisted,
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
def load_profiles() -> list[dict]:
    return get_db().get_all_profiles()


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


# ── Navigation helpers ──────────────────────────────────────────────────────────

def nav_to_entity(entity_id: int | str):
    """Set query_params so the current page renders detail view."""
    st.query_params["id"] = str(entity_id)


def clear_detail():
    """Clear query_params to return to list view."""
    if "id" in st.query_params:
        del st.query_params["id"]


def get_detail_id() -> str | None:
    """Get the current detail ID, or None if in list mode."""
    return st.query_params.get("id")


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
