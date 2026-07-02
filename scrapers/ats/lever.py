"""Lever scraper — board scraper (hardcoded slugs) + ATS adapter (DB targets).

API: api.lever.co/v0/postings/{token}?mode=json
"""

import time
from datetime import datetime
from html.parser import HTMLParser

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting
from storage import JobStorage

BASE_URL = "https://api.lever.co/v0/postings"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)"}

# Seed slugs — preserved as fallback for fresh installs and legacy companies.
LEVER_SLUGS_SEED = ["impossiblecloud"]


def get_lever_slugs(db: JobStorage | None) -> list[str]:
    """Return Lever slugs from DB (watching + lever) merged with seed."""
    if db is None:
        return list(LEVER_SLUGS_SEED)
    rows = db.get_watching_companies_by_method("lever")
    slugs = [r["ats_identifier"] for r in rows if r.get("ats_identifier")]
    return list(dict.fromkeys(list(LEVER_SLUGS_SEED) + slugs))


class _MLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
    def handle_data(self, data):
        self.text.append(data)
    def get_text(self):
        return " ".join(self.text)


def _strip_html(html: str) -> str:
    if not html:
        return ""
    s = _MLStripper()
    s.feed(html)
    return s.get_text()


class LeverScraper(BaseScraper):
    SOURCE_NAME = "Lever"
    ENABLED = True
    ACQUISITION_MODEL = "company_keyed"
    SUPPORTS_DISCOVERY = True

    def _get_slugs(self) -> list[dict]:
        """Resolve scraping targets: DB targets (monitoring) or hardcoded slugs (broad scrape)."""
        if self._targets:
            return [{"slug": c["ats_identifier"], "name": c.get("name", c["ats_identifier"].title())}
                    for c in self._targets if c.get("ats_identifier")]
        return [{"slug": s, "name": s.replace("-", " ").title()} for s in get_lever_slugs(self._storage)]

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
                    r = client.get(f"{BASE_URL}/{slug}?mode=json")
                    if r.status_code != 200:
                        print(f"  [{self.SOURCE_NAME}] {slug}: HTTP {r.status_code}")
                        continue
                    data = r.json()
                    for item in data:
                        categories = item.get("categories", {})
                        title = item.get("text", "")
                        location = categories.get("location", "")

                        posted_date = None
                        raw_date = item.get("createdAt") or item.get("updatedAt", "")
                        if raw_date:
                            try:
                                posted_date = datetime.fromtimestamp(
                                    raw_date / 1000).date()
                            except (ValueError, TypeError, OSError):
                                pass

                        # Prefer descriptionPlain (strip HTML), fall back to descriptionBody
                        desc_raw = item.get("descriptionPlain", "")
                        if not desc_raw:
                            desc_body = item.get("descriptionBody", [])
                            desc_raw = "\n".join(desc_body) if isinstance(desc_body, list) else str(desc_body)
                        description = _strip_html(desc_raw)[:500] if desc_raw else None

                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=title,
                            company=name,
                            location=location or "Unknown",
                            url=item.get("hostedUrl", item.get("applyUrl", "")),
                            posted_date=posted_date,
                            description=description,
                            work_mode=None,
                            base_location=location or None,
                        ))
                time.sleep(0.5)
            except Exception as e:
                print(f"  [{self.SOURCE_NAME}] {slug}: {e}")
                continue

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched across {len(slugs)} board(s)")
        return jobs
