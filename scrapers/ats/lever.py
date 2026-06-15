"""Lever ATS adapter — company-keyed scraper driven by DB targets.

API: api.lever.co/v0/postings/{token}?mode=json
"""

import time
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://api.lever.co/v0/postings"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)"}


class LeverScraper(BaseScraper):
    SOURCE_NAME = "Lever"
    ENABLED = True

    def fetch(self, job_filter: JobFilter | None = None) -> list[JobPosting]:
        if self._targets is None:
            print(f"[{self.SOURCE_NAME}] No targets — use --monitored-only or pass targets")
            return []

        jobs: list[JobPosting] = []
        for company in self._targets:
            token = company.get("ats_identifier")
            if not token:
                continue
            try:
                with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                    r = client.get(f"{BASE_URL}/{token}?mode=json")
                    if r.status_code != 200:
                        print(f"  [{self.SOURCE_NAME}] {token}: HTTP {r.status_code}")
                        continue
                    data = r.json()
                    for item in data:
                        categories = item.get("categories", {})
                        title = item.get("text", "")
                        location = categories.get("location", "")
                        team = categories.get("team", "")
                        commitment = categories.get("commitment", "")

                        posted_date = None
                        raw_date = item.get("createdAt") or item.get("updatedAt", "")
                        if raw_date:
                            try:
                                posted_date = datetime.fromtimestamp(
                                    raw_date / 1000).date()
                            except (ValueError, TypeError, OSError):
                                pass

                        desc_raw = "\n".join(
                            item.get("descriptionBody", []) or []
                            or item.get("lists", []) or []
                            or item.get("additional", "")
                        )[:500]

                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=title,
                            company=company.get("name", token.title()),
                            location=location or "Unknown",
                            url=item.get("applyUrl", ""),
                            posted_date=posted_date,
                            description=desc_raw or None,
                            work_mode=None,
                            base_location=location or None,
                        ))
                time.sleep(0.5)
            except Exception as e:
                print(f"  [{self.SOURCE_NAME}] {token}: {e}")
                continue

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched across "
              f"{len(self._targets)} companies")
        return jobs
