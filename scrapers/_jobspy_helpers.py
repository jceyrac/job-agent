"""Shared helpers for jobspy-based scrapers (LinkedIn, Indeed)."""

import concurrent.futures
import math
import re
from datetime import date, datetime

from models import JobPosting

KNOWN_COUNTRIES = [
    "United States", "USA", "U.S.",
    "Canada",
    "United Kingdom", "UK", "Ireland",
    "Germany", "France", "Switzerland", "Netherlands", "Belgium",
    "Luxembourg", "Spain", "Portugal", "Italy", "Austria",
    "Sweden", "Norway", "Denmark", "Finland",
    "Poland", "Czech Republic", "Czechia", "Romania", "Hungary",
    "Singapore", "India", "Australia", "Japan", "Hong Kong", "China",
    "Brazil", "Mexico", "Argentina",
    "United Arab Emirates", "UAE", "Israel",
    "Serbia", "Turkey", "Turkiye",
]

_COUNTRY_PATTERN = re.compile(
    r"<span[^>]*>\s*(" + "|".join(re.escape(c) for c in KNOWN_COUNTRIES) + r")\s*</span>",
    re.IGNORECASE,
)


def _extract_country_from_html(html: str) -> str | None:
    if not html:
        return None
    m = _COUNTRY_PATTERN.search(html)
    return m.group(1) if m else None


def patch_requests_for_indeed():
    """Monkey-patch requests.Session.request with curl_cffi for browser TLS."""
    import requests
    import curl_cffi.requests as cr

    _cffi_session = cr.Session(impersonate="chrome124")
    _original_request = requests.Session.request

    def _patched_request(self, method, url, **kwargs):
        return _cffi_session.request(method, url, **kwargs)

    requests.Session.request = _patched_request
    return _original_request


def unpatch_requests(original_request):
    import requests
    requests.Session.request = original_request


def scrape_with_timeout(timeout_seconds: int, **kwargs):
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(__import__('jobspy').scrape_jobs, **kwargs)
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        return None
    finally:
        executor.shutdown(wait=False)


def dataframe_to_postings(df, source: str) -> list[JobPosting]:
    postings = []
    for _, row in df.iterrows():
        # Salary — guard against NaN
        s_min = row.get("min_amount")
        s_max = row.get("max_amount")
        currency = row.get("currency") or ""
        s_min = None if (s_min is None or (isinstance(s_min, float) and math.isnan(s_min))) else int(s_min)
        s_max = None if (s_max is None or (isinstance(s_max, float) and math.isnan(s_max))) else int(s_max)
        salary = f"{currency} {s_min or 0:,}–{s_max or 0:,}".strip() if (s_min or s_max) else None

        # Description — extract country from raw HTML before stripping
        raw_description = row.get("description") or ""
        html_country = _extract_country_from_html(raw_description) if source == "LinkedIn" else None
        description = raw_description
        if description:
            description = re.sub(r"<[^>]+>", " ", description)
            description = re.sub(r"[#*`>\[\]]+", " ", description)
            description = re.sub(r"\s+", " ", description).strip()

        # Date — JobSpy returns datetime.date directly or float NaN when unavailable.
        posted_date = row.get("date_posted")
        if posted_date is None or isinstance(posted_date, float):
            posted_date = None
        elif not isinstance(posted_date, date):
            try:
                posted_date = datetime.strptime(str(posted_date), "%Y-%m-%d").date()
            except ValueError:
                posted_date = None

        raw_loc = str(row.get("location") or "").strip()
        wfh = str(row.get("work_from_home_type") or "").lower()
        is_remote = row.get("is_remote")

        if "hybrid" in wfh:
            work_mode = "hybrid"
            location = f"{raw_loc} (Hybrid)" if raw_loc else "Hybrid"
        elif is_remote or not raw_loc or "remote" in raw_loc.lower():
            work_mode = "remote"
            location = "Remote" if not raw_loc else raw_loc
        else:
            work_mode = "on-site"
            location = raw_loc

        # base_location: prefer raw location field; fall back to HTML-extracted country (LinkedIn only)
        base_location = raw_loc if raw_loc and raw_loc.lower() not in ("remote", "") else None
        if not base_location and html_country:
            base_location = html_country

        postings.append(JobPosting(
            source=source,
            title=str(row.get("title") or ""),
            company=str(row.get("company") or ""),
            location=location,
            url=str(row.get("job_url") or ""),
            posted_date=posted_date if isinstance(posted_date, date) else None,
            description=description or None,
            tags=[],
            salary=salary,
            work_mode=work_mode,
            base_location=base_location,
        ))
    return postings


def add_unique(df, source: str, seen_urls: set, all_jobs: list) -> tuple[int, int]:
    new, skipped = 0, 0
    for posting in dataframe_to_postings(df, source):
        if posting.url and posting.url in seen_urls:
            skipped += 1
        else:
            if posting.url:
                seen_urls.add(posting.url)
            all_jobs.append(posting)
            new += 1
    return new, skipped
