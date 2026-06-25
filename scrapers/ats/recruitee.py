"""Recruitee ATS adapter — company-keyed scraper driven by DB targets.

API: career.recruitee.com/api/c/{company_id}/widget/?widget=true

Public endpoint, no auth required. Returns JSON with top-level "offers" array.
One API call per monitored company.
"""

import time
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://career.recruitee.com/api/c"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)"}


class RecruiteeScraper(BaseScraper):
    SOURCE_NAME = "Recruitee"
    ENABLED = True

    def fetch(self, job_filter: JobFilter | None = None) -> list[JobPosting]:
        if self._targets is None:
            print(f"[{self.SOURCE_NAME}] No targets — use --monitored-only or pass targets")
            return []

        jobs: list[JobPosting] = []
        for company in self._targets:
            company_id = company.get("ats_identifier")
            if not company_id:
                continue
            try:
                with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                    r = client.get(f"{BASE_URL}/{company_id}/widget/",
                                   params={"widget": "true"})
                    if r.status_code != 200:
                        print(f"  [{self.SOURCE_NAME}] {company_id}: HTTP {r.status_code}")
                        continue
                    data = r.json()
                    offers = data.get("offers", [])

                    if not offers:
                        print(f"  [{self.SOURCE_NAME}] {company_id}: 0 jobs")

                    for item in offers:
                        title = item.get("title", "")
                        company_name = item.get("company_name") or company.get("name", "")

                        # Location: prefer city + country
                        city = item.get("city", "")
                        country = item.get("country", "")
                        location = f"{city}, {country}" if city and country else \
                                   item.get("location", "") or "Unknown"

                        # URL: careers_url is the job page
                        url = item.get("careers_url", "") or \
                              item.get("careers_apply_url", "") or ""

                        # Date: published_at
                        posted_date = None
                        raw_date = item.get("published_at", "")
                        if raw_date:
                            try:
                                posted_date = datetime.fromisoformat(
                                    raw_date[:10]).date()
                            except (ValueError, TypeError):
                                pass

                        # Description
                        desc = (item.get("description", "") or "")[:500]

                        # Work mode
                        work_mode = None
                        if item.get("remote"):
                            work_mode = "remote"
                        elif item.get("hybrid"):
                            work_mode = "hybrid"
                        elif item.get("on_site"):
                            work_mode = "on-site"

                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=title,
                            company=company_name,
                            location=location,
                            url=url,
                            posted_date=posted_date,
                            description=desc or None,
                            work_mode=work_mode,
                            base_location=location,
                        ))
                time.sleep(1.0)
            except Exception as e:
                print(f"  [{self.SOURCE_NAME}] {company_id}: {e}")
                continue

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched across "
              f"{len(self._targets)} companies")
        return jobs
