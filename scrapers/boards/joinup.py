"""Joinup.ch scraper — Swiss startup job board (ETH Entrepreneur Club + Swisspreneur).

Acquisition mechanics (for future maintainers):
  - The job list is NOT in the rendered DOM. It lives in the server-rendered
    ``__NEXT_DATA__`` JSON embedded in ``/browse/jobs``, at path
    ``props.pageProps.serverState.initialResults.jobs.results[0].hits`` — a
    Typesense-backed InstantSearch response (Algolia-like fields).
  - Pagination is credential-free and SSR'd via the 1-indexed ``?page=N`` query
    param (``?page=2`` server-renders internal page=1). No Typesense key is
    exposed in the page (backend sits behind a Cloud Run app) and none is
    needed. No login.
  - Hits are sorted newest-first by ``created`` (descending id), so we page
    forward until a full page falls entirely before ``JobFilter.date_from``,
    then stop — the only early stop, and it is a freshness bound, not a
    relevance filter.

NOTE: this relies on undocumented site behavior (the ``__NEXT_DATA__`` shape and
the SSR'd pagination) that may change without notice. If Joinup renames the
``results[0]`` keys or stops server-rendering ``?page=N``, this scraper returns
``[]`` without auto-disabling (see ``fetch``).

Spec 027.
"""

from __future__ import annotations

import ast
import json
import re
import time
from datetime import datetime, timezone

import httpx

from models import JobFilter, JobPosting
from scrapers.base import BaseScraper

BASE_URL = "https://joinup.ch"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)


class JoinupScraper(BaseScraper):
    SOURCE_NAME = "Joinup"
    ENABLED = True
    ACQUISITION_MODEL = "board"
    SUPPORTS_DISCOVERY = True

    BASE_URL = BASE_URL
    PAGE_DELAY = 0.5     # seconds between page requests (courtesy)
    MAX_PAGES = 120      # hard safety cap; the date cutoff is the real bound

    # ── Parsing helpers ────────────────────────────────────────────────────

    def _extract_next_data(self, html: str) -> dict | None:
        """Extract and json-parse the ``__NEXT_DATA__`` script node.

        Returns ``None`` (never raises) if the node is absent or not valid JSON.
        """
        if not html:
            return None
        m = _NEXT_DATA_RE.search(html)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _page_results(data: dict) -> dict | None:
        """Navigate to ``results[0]`` and return hits + pagination meta.

        Returns ``None`` on any shape change (missing/renamed keys).
        """
        try:
            results = data["props"]["pageProps"]["serverState"]["initialResults"]["jobs"]["results"]
        except (KeyError, TypeError):
            return None
        if not results:
            return None
        r0 = results[0]
        return {
            "hits": r0.get("hits") or [],
            "nbPages": r0.get("nbPages", 0),
            "nbHits": r0.get("nbHits", 0),
        }

    @staticmethod
    def _parse_skills(skills) -> list[str]:
        """Parse ``skills`` defensively: it may be a real list, or a stringified
        python list (``"['Building World Class Teams']"``). Fallback ``[]``."""
        if skills is None:
            return []
        if isinstance(skills, list):
            return [s for s in skills if isinstance(s, str)]
        if isinstance(skills, str):
            try:
                parsed = ast.literal_eval(skills)
            except (ValueError, SyntaxError):
                return []
            if isinstance(parsed, list):
                return [s for s in parsed if isinstance(s, str)]
        return []

    @staticmethod
    def _clean_tag(value) -> str | None:
        """Normalize a raw tag value (nbsp → space), or None if empty."""
        if not value:
            return None
        if not isinstance(value, str):
            value = str(value)
        cleaned = value.replace("\xa0", " ").strip()
        return cleaned or None

    @classmethod
    def _hit_to_job(cls, hit: dict) -> JobPosting:
        """Map one Typesense hit → JobPosting (no relevance classification)."""
        title = hit.get("title") or hit.get("headline") or ""
        company = hit.get("startup") or ""
        location = hit.get("location") or ""
        slug = hit.get("slug") or ""
        url = f"{cls.BASE_URL}/job/{slug}" if slug else ""

        posted_date = None
        created = hit.get("created")
        if created is not None:
            try:
                posted_date = datetime.fromtimestamp(
                    int(created) / 1000.0, tz=timezone.utc
                ).date()
            except (ValueError, TypeError, OverflowError, OSError):
                posted_date = None

        # Tags: skills + jobType + paymentType (raw) + startupIndustry. These
        # surface signals for the extractor WITHOUT classifying contract_type/comp.
        tags = list(cls._parse_skills(hit.get("skills")))
        for extra in (hit.get("jobType"), hit.get("paymentType"), hit.get("startupIndustry")):
            cleaned = cls._clean_tag(extra)
            if cleaned:
                tags.append(cleaned)

        # work_mode: only "Remote" is a deterministic remote signal; everything
        # else is left None for the scorer (Constitution IV).
        work_mode = "remote" if location == "Remote" else None

        return JobPosting(
            source=cls.SOURCE_NAME,
            title=title,
            company=company,
            location=location,
            url=url,
            posted_date=posted_date,
            description=hit.get("description") or None,
            tags=tags,
            work_mode=work_mode,
            # base_location, geo_zone, company_size, contract_type,
            # company_country, industry_sector, comp_* → None (scorer's job)
        )

    # ── Page fetch ─────────────────────────────────────────────────────────

    def _fetch_page(self, page: int) -> dict | None:
        """GET ``/browse/jobs?page=N`` (1-indexed) and return page results.

        Returns ``None`` on HTTP error or unparseable ``__NEXT_DATA__``.
        """
        url = f"{BASE_URL}/browse/jobs?page={page}"
        try:
            r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        data = self._extract_next_data(r.text)
        if data is None:
            return None
        return self._page_results(data)

    # ── Main fetch ─────────────────────────────────────────────────────────

    def fetch(self, job_filter: JobFilter | None) -> list[JobPosting]:
        """Fetch Joinup jobs from ``__NEXT_DATA__``, paginating the SSR'd pages.

        Wide net: every parsed hit within the freshness window is returned. The
        ONLY early stop is the date cutoff (``job_filter.date_from``) — a
        freshness bound, not a relevance filter. Fit judgment stays with the
        scorer (Constitution I).
        """
        first = self._fetch_page(1)
        if first is None:
            print(f"[{self.SOURCE_NAME}] ⚠️ __NEXT_DATA__ missing/unparseable — 0 jobs")
            return []

        jobs: list[JobPosting] = []
        seen_ids: set = set()
        date_from = job_filter.date_from if job_filter else None

        nb_pages = int(first.get("nbPages") or 0)
        max_page = min(max(1, nb_pages), self.MAX_PAGES)

        results = first
        for page in range(1, max_page + 1):
            if page > 1:
                time.sleep(self.PAGE_DELAY)
                results = self._fetch_page(page)
                if results is None:
                    print(f"[{self.SOURCE_NAME}] ⚠️ page {page} unparseable — stopping")
                    break

            hits = results.get("hits") or []
            if not hits:
                break

            page_all_old = True
            for hit in hits:
                job = self._hit_to_job(hit)
                # A hit is "older" only when posted_date is present AND before date_from.
                if date_from is None or job.posted_date is None or job.posted_date >= date_from:
                    page_all_old = False

                hit_id = hit.get("id")
                if hit_id is not None and hit_id in seen_ids:
                    continue
                if hit_id is not None:
                    seen_ids.add(hit_id)
                jobs.append(job)

            if date_from is not None and page_all_old:
                break

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
        return jobs
