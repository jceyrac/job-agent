"""Ashby ATS adapter — company-keyed scraper driven by DB targets.

API: api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

Public endpoint, no auth required. Returns JSON with top-level "jobs" array.
No pagination — all listed jobs in a single call.
"""

import time
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)"}


class AshbyScraper(BaseScraper):
    SOURCE_NAME = "Ashby"
    ENABLED = True

    def fetch(self, job_filter: JobFilter | None = None) -> list[JobPosting]:
        if self._targets is None:
            print(f"[{self.SOURCE_NAME}] No targets — use --monitored-only or pass targets")
            return []

        jobs: list[JobPosting] = []
        for company in self._targets:
            board = company.get("ats_identifier")
            if not board:
                continue
            try:
                with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                    r = client.get(f"{BASE_URL}/{board}",
                                   params={"includeCompensation": "true"})
                    if r.status_code != 200:
                        print(f"  [{self.SOURCE_NAME}] {board}: HTTP {r.status_code}")
                        continue
                    data = r.json()
                    postings = data.get("jobs", [])

                    if not postings:
                        print(f"  [{self.SOURCE_NAME}] {board}: 0 jobs")

                    for item in postings:
                        # Skip unlisted jobs
                        if not item.get("isListed", True):
                            continue

                        title = item.get("title", "")
                        location = item.get("location", "") or "Unknown"

                        # URL: prefer jobUrl, fallback to applyUrl
                        url = item.get("jobUrl", "") or item.get("applyUrl", "") or ""

                        # Date: publishedAt
                        posted_date = None
                        raw_date = item.get("publishedAt", "")
                        if raw_date:
                            try:
                                posted_date = datetime.fromisoformat(
                                    raw_date[:10]).date()
                            except (ValueError, TypeError):
                                pass

                        # Description: descriptionHtml
                        desc = (item.get("descriptionHtml", "") or
                                item.get("description", "") or "")[:500]

                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=title,
                            company=company.get("name", board.title()),
                            location=location,
                            url=url,
                            posted_date=posted_date,
                            description=desc or None,
                            work_mode=None,
                            base_location=location,
                        ))
                time.sleep(1.0)
            except Exception as e:
                print(f"  [{self.SOURCE_NAME}] {board}: {e}")
                continue

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched across "
              f"{len(self._targets)} companies")
        return jobs
