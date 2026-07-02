"""Gem ATS adapter — company-keyed scraper driven by DB targets.

API: api.gem.com/job_board/v0/{vanity_slug}/job_posts/

Response: JSON array at root. Each job object has title, location (object
with name), offices (array of objects with location.name), content_plain,
first_published_at, absolute_url.

No authentication required. One API call per monitored company.
"""

import time
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://api.gem.com/job_board/v0"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)"}


def _extract_location(job: dict) -> str:
    """Extract location from Gem job object. Prefers offices[0].location.name,
    falls back to location.name, then 'Unknown'."""
    offices = job.get("offices") or []
    if offices:
        loc = offices[0].get("location") or {}
        if isinstance(loc, dict) and loc.get("name"):
            return loc["name"]
    loc = job.get("location") or {}
    if isinstance(loc, dict) and loc.get("name"):
        return loc["name"]
    return "Unknown"


class GemScraper(BaseScraper):
    SOURCE_NAME = "Gem"
    ENABLED = True
    ACQUISITION_MODEL = "company_keyed"
    SUPPORTS_DISCOVERY = False

    def fetch(self, job_filter: JobFilter | None = None) -> list[JobPosting]:
        if self._targets is None:
            print(f"[{self.SOURCE_NAME}] No targets — use --monitored-only or pass targets")
            return []

        jobs: list[JobPosting] = []
        for company in self._targets:
            slug = company.get("ats_identifier")
            if not slug:
                continue
            try:
                with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                    r = client.get(f"{BASE_URL}/{slug}/job_posts/")
                    if r.status_code != 200:
                        print(f"  [{self.SOURCE_NAME}] {slug}: HTTP {r.status_code}")
                        continue
                    data = r.json()
                    postings = data if isinstance(data, list) else data.get("results", []) or []

                    if not postings:
                        print(f"  [{self.SOURCE_NAME}] {slug}: 0 jobs")

                    for item in postings:
                        title = item.get("title", "")

                        # Location: prefer offices[0].location.name
                        location = _extract_location(item)

                        # URL: prefer absolute_url
                        url = item.get("absolute_url", "") or \
                              f"https://jobs.gem.com/{slug}/{item.get('id', '')}"

                        # Date: prefer first_published_at, fall back to created_at
                        posted_date = None
                        raw_date = item.get("first_published_at", "") or \
                                   item.get("created_at", "")
                        if raw_date:
                            try:
                                posted_date = datetime.fromisoformat(
                                    raw_date[:10]).date()
                            except (ValueError, TypeError):
                                pass

                        # Description: prefer content_plain (clean), fall back to content
                        desc = (item.get("content_plain", "") or
                                item.get("content", "") or "")[:500]

                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=title,
                            company=company.get("name", slug.replace("-", " ").title()),
                            location=location,
                            url=url,
                            posted_date=posted_date,
                            description=desc or None,
                            work_mode=None,
                            base_location=location,
                        ))
                time.sleep(1.0)
            except Exception as e:
                print(f"  [{self.SOURCE_NAME}] {slug}: {e}")
                continue

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched across "
              f"{len(self._targets)} companies")
        return jobs
