"""Regex-based contact reference extraction from job descriptions.

Extracts emails and LinkedIn profile URLs without LLM calls.
Exported for use in the --extract pass (score.py).
"""

import re

# RFC-light email pattern — case-insensitive
_EMAIL_RE = re.compile(
    r"\b([a-zA-Z0-9][a-zA-Z0-9._%+\-]*@[a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,})\b",
    re.IGNORECASE,
)

# LinkedIn profile URL pattern — protocol optional, case-insensitive
_LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-%]+",
    re.IGNORECASE,
)

_ROLE_ACCOUNT_PREFIXES = frozenset({
    "careers", "jobs", "hr", "recruiting", "talent", "apply", "info", "contact",
})


def _is_role_account(email: str) -> bool:
    """Check if the local part of an email looks like a role/team address."""
    local = email.split("@")[0].lower().strip()
    return local in _ROLE_ACCOUNT_PREFIXES


def _normalize_linkedin(url: str) -> str:
    """Normalize a LinkedIn URL to https://www.linkedin.com/in/<slug>."""
    # Extract the slug
    m = re.search(r"linkedin\.com/in/([\w\-%]+)", url, re.IGNORECASE)
    if not m:
        return url
    slug = m.group(1).rstrip("/")
    return f"https://www.linkedin.com/in/{slug}"


def extract_contact_references(description: str) -> list[dict]:
    """Extract email and LinkedIn references from a job description.

    Returns a list of dicts, each with keys:
        email, linkedin_url, role_account, source_pattern

    Deduplicated within a single description. Empty input returns [].
    """
    if not description:
        return []

    results: list[dict] = []
    seen_emails: set[str] = set()
    seen_linkedin: set[str] = set()

    for m in _EMAIL_RE.finditer(description):
        email = m.group(1).lower()
        if email in seen_emails:
            continue
        seen_emails.add(email)
        results.append({
            "email": email,
            "linkedin_url": None,
            "role_account": _is_role_account(email),
            "source_pattern": "email_regex",
        })

    for m in _LINKEDIN_RE.finditer(description):
        raw = m.group(0)
        normalized = _normalize_linkedin(raw)
        if normalized in seen_linkedin:
            continue
        seen_linkedin.add(normalized)
        results.append({
            "email": None,
            "linkedin_url": normalized,
            "role_account": False,
            "source_pattern": "linkedin_regex",
        })

    return results
