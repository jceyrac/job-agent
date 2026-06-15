"""Workable ATS adapter — company-keyed scraper driven by DB targets.

Board public JSON: apply.workable.com/api/v3/accounts/{id}/jobs
"""

import time
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://apply.workable.com/api/v3/accounts"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)"}


class WorkableScraper(BaseScraper):
    SOURCE_NAME = "Workable"
    ENABLED = True

    def fetch(self, job_filter: JobFilter | None = None) -> list[JobPosting]:
        if self._targets is None:
            print(f"[{self.SOURCE_NAME}] No targets — use --monitored-only or pass targets")
            return []

        jobs: list[JobPosting] = []
        for company in self._targets:
            account_id = company.get("ats_identifier")
            if not account_id:
                continue
            try:
                with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                    r = client.get(f"{BASE_URL}/{account_id}/jobs")
                    if r.status_code != 200:
                        print(f"  [{self.SOURCE_NAME}] {account_id}: HTTP {r.status_code}")
                        continue
                    data = r.json()
                    results = data.get("results", []) or data.get("jobs", []) or []

                    for item in results:
                        title = item.get("title", "")
                        location = item.get("location", "") or ""
                        if isinstance(location, dict):
                            location = location.get("name", location.get("city", ""))
                        url = item.get("application_url", "") or \
                              f"https://apply.workable.com/{account_id}/j/{item.get('shortcode', '')}"

                        posted_date = None
                        raw_date = item.get("published", "") or item.get("created_at", "")
                        if raw_date:
                            try:
                                posted_date = datetime.fromisoformat(
                                    raw_date[:10]).date()
                            except (ValueError, TypeError):
                                pass

                        desc = (item.get("description", "") or "")[:500]

                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=title,
                            company=company.get("name", account_id.title()),
                            location=location or "Unknown",
                            url=url,
                            posted_date=posted_date,
                            description=desc or None,
                            work_mode=None,
                            base_location=location or None,
                        ))
                time.sleep(0.5)
            except Exception as e:
                print(f"  [{self.SOURCE_NAME}] {account_id}: {e}")
                continue

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched across "
              f"{len(self._targets)} companies")
        return jobs
