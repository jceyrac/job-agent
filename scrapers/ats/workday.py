"""Workday ATS adapter — company-keyed scraper driven by DB targets.

API: POST https://{tenant}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
JSON body, paginated via offset/limit.  No browser needed.

ats_identifier format: "tenant/site" (e.g., "lombardodier/lausanne")
"""

import json
import time
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)",
    "Content-Type": "application/json",
}


class WorkdayScraper(BaseScraper):
    SOURCE_NAME = "Workday"
    ENABLED = True

    def fetch(self, job_filter: JobFilter | None = None) -> list[JobPosting]:
        if self._targets is None:
            print(f"[{self.SOURCE_NAME}] No targets — use --monitored-only or pass targets")
            return []

        jobs: list[JobPosting] = []
        for company in self._targets:
            identifier = company.get("ats_identifier", "")
            if not identifier:
                continue
            parts = identifier.split("/", 1)
            tenant = parts[0]
            site = parts[1] if len(parts) > 1 else tenant

            try:
                with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
                    base_url = f"https://{tenant}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
                    offset = 0
                    limit = 20
                    while True:
                        body = {
                            "limit": limit,
                            "offset": offset,
                            "searchText": "",
                        }
                        r = client.post(base_url, json=body)
                        if r.status_code != 200:
                            print(f"  [{self.SOURCE_NAME}] {identifier}: HTTP {r.status_code}")
                            break
                        data = r.json()
                        postings = data.get("jobPostings", [])
                        if not postings:
                            break

                        for item in postings:
                            title = item.get("title", "")
                            location = item.get("locationsText", "") or ""
                            url = item.get("externalPath", "") or ""
                            if url and not url.startswith("http"):
                                url = f"https://{tenant}.myworkdayjobs.com{url}"

                            posted_date = None
                            raw_date = item.get("postedDate", "")
                            if raw_date:
                                try:
                                    posted_date = datetime.fromisoformat(
                                        raw_date[:10]).date()
                                except (ValueError, TypeError):
                                    pass

                            jobs.append(JobPosting(
                                source=self.SOURCE_NAME,
                                title=title,
                                company=company.get("name", tenant.title()),
                                location=location or "Unknown",
                                url=url,
                                posted_date=posted_date,
                                description=None,
                                work_mode=None,
                                base_location=location or None,
                            ))
                        offset += limit
                        if len(postings) < limit:
                            break
                        time.sleep(0.3)
                time.sleep(0.5)
            except Exception as e:
                print(f"  [{self.SOURCE_NAME}] {identifier}: {e}")
                continue

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched across "
              f"{len(self._targets)} companies")
        return jobs
