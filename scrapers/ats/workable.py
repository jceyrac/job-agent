"""Workable scraper — board scraper (hardcoded slugs) + ATS adapter (DB targets).

API: POST apply.workable.com/api/v3/accounts/{id}/jobs
"""

import time
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting
from storage import JobStorage

BASE_URL = "https://apply.workable.com/api/v3/accounts"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)",
    "Content-Type": "application/json",
}

# Seed slugs — preserved as fallback for fresh installs and legacy companies.
WORKABLE_SLUGS_SEED = ["walletconnect", "walletconnect-foundation"]


def get_workable_slugs(db: JobStorage | None) -> list[str]:
    """Return Workable slugs from DB (watching + workable) merged with seed."""
    if db is None:
        return list(WORKABLE_SLUGS_SEED)
    rows = db.get_watching_companies_by_method("workable")
    slugs = [r["ats_identifier"] for r in rows if r.get("ats_identifier")]
    return list(dict.fromkeys(list(WORKABLE_SLUGS_SEED) + slugs))

POST_BODY = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}


class WorkableScraper(BaseScraper):
    SOURCE_NAME = "Workable"
    ENABLED = True
    ACQUISITION_MODEL = "company_keyed"
    SUPPORTS_DISCOVERY = True

    def _get_slugs(self) -> list[dict]:
        """Resolve scraping targets: DB targets (monitoring) or hardcoded slugs (broad scrape)."""
        if self._targets:
            return [{"slug": c["ats_identifier"], "name": c.get("name", c["ats_identifier"].title())}
                    for c in self._targets if c.get("ats_identifier")]
        return [{"slug": s, "name": s.replace("-", " ").title()} for s in get_workable_slugs(self._storage)]

    def fetch(self, job_filter: JobFilter | None = None) -> list[JobPosting]:
        slugs = self._get_slugs()
        if not slugs:
            print(f"[{self.SOURCE_NAME}] No targets — add slugs or use --monitored-only")
            return []

        jobs: list[JobPosting] = []
        for entry in slugs:
            slug = entry["slug"]
            name = entry["name"]
            try:
                with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                    next_page = None
                    while True:
                        body = dict(POST_BODY)
                        if next_page:
                            body["next_page"] = next_page
                        r = client.post(f"{BASE_URL}/{slug}/jobs", json=body)
                        if r.status_code != 200:
                            print(f"  [{self.SOURCE_NAME}] {slug}: HTTP {r.status_code}")
                            break
                        data = r.json()
                        results = data.get("results", []) or []

                        for item in results:
                            title = item.get("title", "")
                            loc = item.get("location") or {}
                            if isinstance(loc, dict):
                                location = ", ".join(
                                    p for p in [loc.get("city"), loc.get("country")]
                                    if p
                                ) or "Unknown"
                            else:
                                location = str(loc) if loc else "Unknown"

                            url = item.get("url", "") or \
                                  item.get("application_url", "") or \
                                  f"https://apply.workable.com/{slug}/j/{item.get('shortcode','')}"

                            posted_date = None
                            raw_date = item.get("created_at", "")
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
                                company=name,
                                location=location,
                                url=url,
                                posted_date=posted_date,
                                description=desc or None,
                                work_mode=None,
                                base_location=location,
                            ))

                        next_page = data.get("next_page")
                        if not next_page:
                            break
                        time.sleep(0.3)
                time.sleep(0.5)
            except Exception as e:
                print(f"  [{self.SOURCE_NAME}] {slug}: {e}")
                continue

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched across {len(slugs)} board(s)")
        return jobs
