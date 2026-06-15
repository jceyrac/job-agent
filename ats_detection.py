"""
ats_detection.py — ATS provider detection from a careers URL.

Detection flow:
  1. Hostname pattern matching (fast, no network)
  2. Vanity domain page fetch (one HTTP GET, search for embedded ATS)
  3. Manual fallback (user types provider + identifier)

Returns {"provider": str, "identifier": str} or raises DetectionError.
"""

import re
from urllib.parse import urlparse

import httpx

# Provider → (hostname patterns, identifier extraction regex)
# Order matters: more specific patterns first to avoid false matches.
HOSTNAME_PATTERNS: list[tuple[str, str, str]] = [
    # (provider, hostname_pattern, identifier_regex_from_url)
    ("greenhouse",     r"boards\.greenhouse\.io",         r"/boards\.greenhouse\.io/([^/]+)"),
    ("lever",          r"jobs\.lever\.co",                r"/jobs\.lever\.co/([^/]+)"),
    ("ashby",          r"\.ashbyhq\.com",                 r"/([^/]+)$"),  # e.g., jobs.ashbyhq.com/kraken
    ("ashby",          r"jobs\.ashbyhq\.com",             r"/jobs\.ashbyhq\.com/([^/]+)"),
    ("workday",        r"\.myworkdayjobs\.com",           r"/([^/]+)/([^/]+)"),  # tenant/site
    ("smartrecruiters", r"jobs\.smartrecruiters\.com",    r"/smartrecruiters\.com/([^/]+)"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com", r"/smartrecruiters\.com/([^/]+)"),
    ("workable",       r"apply\.workable\.com",           r"/apply\.workable\.com/([^/]+)"),
]

# ATS board URL patterns to search in page HTML for vanity-domain detection
EMBEDDED_ATS_PATTERNS: list[tuple[str, str]] = [
    ("greenhouse", r"boards\.greenhouse\.io/([^/\"'&?#]+)"),
    ("lever",      r"jobs\.lever\.co/([^/\"'&?#]+)"),
    ("ashby",      r"jobs\.ashbyhq\.com/([^/\"'&?#]+)"),
    ("workday",    r"([^./]+)\.myworkdayjobs\.com/(?:[^/]+/)?([^/\"'&?#]+)/jobs"),
    ("smartrecruiters", r"jobs\.smartrecruiters\.com/(?:[^/]+/)?([^/\"'&?#]+)/job"),
    ("workable",   r"apply\.workable\.com/([^/\"'&?#]+)"),
]

KNOWN_PROVIDERS = frozenset({
    "greenhouse", "lever", "ashby", "workday",
    "smartrecruiters", "workable",
})


class DetectionError(Exception):
    """Raised when ATS detection fails at all levels."""
    pass


def detect_ats_from_hostname(careers_url: str) -> dict | None:
    """Try to detect ATS provider from hostname pattern matching.
    Returns {"provider", "identifier"} or None.
    """
    parsed = urlparse(careers_url)
    hostname = parsed.hostname or ""
    path = parsed.path or ""

    for provider, pattern, id_regex in HOSTNAME_PATTERNS:
        if re.search(pattern, hostname + path):
            m = re.search(id_regex, path)
            if m:
                identifier = m.group(1)
                # For workday, we need tenant/site
                if provider == "workday" and m.lastindex and m.lastindex >= 2:
                    identifier = f"{m.group(1)}/{m.group(2)}"
                return {"provider": provider, "identifier": identifier}
    return None


def detect_ats_from_page(careers_url: str) -> dict | None:
    """Fetch the careers page and search for embedded ATS board URLs.
    Returns {"provider", "identifier"} or None.
    """
    try:
        with httpx.Client(
            headers={"User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)"},
            timeout=10, follow_redirects=True,
        ) as client:
            r = client.get(careers_url)
            if r.status_code != 200:
                return None
            html = r.text
    except Exception:
        return None

    for provider, pattern in EMBEDDED_ATS_PATTERNS:
        m = re.search(pattern, html)
        if m:
            identifier = m.group(1)
            if provider == "workday" and m.lastindex and m.lastindex >= 2:
                identifier = f"{m.group(1)}/{m.group(2)}"
            return {"provider": provider, "identifier": identifier}
    return None


def resolve_scrape_method(careers_url: str) -> dict:
    """Full detection pipeline: hostname → page fetch → error.

    Returns {"provider": str, "identifier": str}.
    Raises DetectionError if no ATS identified.
    """
    # Step 1: hostname pattern match
    result = detect_ats_from_hostname(careers_url)
    if result:
        return result

    # Step 2: fetch page and search for embedded ATS
    result = detect_ats_from_page(careers_url)
    if result:
        return result

    raise DetectionError(
        "No ATS provider detected from the careers URL. "
        "You can enter the provider and identifier manually."
    )


def validate_manual_entry(provider: str, identifier: str) -> dict:
    """Validate a manual provider + identifier entry.
    Returns {"provider", "identifier"} or raises DetectionError.
    """
    provider = provider.lower().strip()
    identifier = identifier.strip()
    if provider not in KNOWN_PROVIDERS:
        raise DetectionError(
            f"Unknown provider '{provider}'. Known: {', '.join(sorted(KNOWN_PROVIDERS))}"
        )
    if not identifier:
        raise DetectionError("Identifier is required.")
    return {"provider": provider, "identifier": identifier}
