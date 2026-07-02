"""SmartRecruiters ATS adapter — company-keyed scraper driven by DB targets.

API: api.smartrecruiters.com/v1/companies/{id}/postings
Public API, paginated.
"""

import time
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://api.smartrecruiters.com/v1/companies"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)"}


class SmartRecruitersScraper(BaseScraper):
    SOURCE_NAME = "SmartRecruiters"
    ENABLED = True
    ACQUISITION_MODEL = "company_keyed"
    SUPPORTS_DISCOVERY = False

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
                    offset = 0
                    limit = 100
                    while True:
                        r = client.get(
                            f"{BASE_URL}/{company_id}/postings",
                            params={"offset": offset, "limit": limit,
                                    "language": "en"},
                        )
                        if r.status_code != 200:
                            print(f"  [{self.SOURCE_NAME}] {company_id}: HTTP {r.status_code}")
                            break
                        data = r.json()
                        postings = data.get("content", [])
                        if not postings:
                            break

                        for item in postings:
                            title = item.get("name", "")
                            location = item.get("location", {}) or {}
                            loc_str = f"{location.get('city', '')}, {location.get('country', '')}".strip(", ")

                            url = item.get("ref", "") or ""
                            if url and not url.startswith("http"):
                                url = f"https://jobs.smartrecruiters.com/{url}"

                            posted_date = None
                            raw_date = item.get("releaseDate", "")
                            if raw_date:
                                try:
                                    posted_date = datetime.fromisoformat(
                                        raw_date[:10]).date()
                                except (ValueError, TypeError):
                                    pass

                            desc = (item.get("jobAd", {}).get("sections", {})
                                    or {})
                            desc_text = ""
                            if isinstance(desc, dict):
                                desc_text = " ".join(
                                    v for v in desc.values()
                                    if isinstance(v, str)
                                )[:500]

                            jobs.append(JobPosting(
                                source=self.SOURCE_NAME,
                                title=title,
                                company=company.get("name",
                                    company_id.replace("-", " ").title()),
                                location=loc_str or "Unknown",
                                url=url,
                                posted_date=posted_date,
                                description=desc_text or None,
                                work_mode=None,
                                base_location=loc_str or None,
                            ))

                        offset += limit
                        if len(postings) < limit:
                            break
                        time.sleep(0.3)
                time.sleep(0.5)
            except Exception as e:
                print(f"  [{self.SOURCE_NAME}] {company_id}: {e}")
                continue

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched across "
              f"{len(self._targets)} companies")
        return jobs
