import json
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

# RSS feed exists but returns 0 items (feed is empty / requires paid plan).
# Scraping __NEXT_DATA__ from the product-manager category page instead.

BASE_URL = "https://cryptojobslist.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": "https://google.com",
}


class CryptoJobsListScraper(BaseScraper):
    SOURCE_NAME = "CryptoJobsList"
    ENABLED = True
    URL = f"{BASE_URL}/product-manager"

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            from bs4 import BeautifulSoup
            r = httpx.get(self.URL, headers=HEADERS, timeout=15, follow_redirects=True)
            if r.status_code != 200:
                print(f"[{self.SOURCE_NAME}] ⚠️ HTTP {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            script = soup.find("script", id="__NEXT_DATA__")
            if not script:
                print(f"[{self.SOURCE_NAME}] ⚠️ __NEXT_DATA__ not found")
                return []

            data = json.loads(script.string)
            raw_jobs = data["props"]["pageProps"].get("jobs", [])

            jobs = []
            for item in raw_jobs:
                remote = item.get("remote", False)
                raw_location = item.get("jobLocation", "") or ""
                if remote:
                    location = "Remote"
                    work_mode = "remote"
                elif raw_location:
                    location = raw_location
                    work_mode = "unknown"
                else:
                    location = "Remote"
                    work_mode = "remote"

                posted_date = None
                raw_date = item.get("publishedAt", "")
                if raw_date:
                    try:
                        posted_date = datetime.fromisoformat(raw_date[:10]).date()
                    except ValueError:
                        pass

                tags = [t for t in (item.get("tags") or []) if t]
                salary = item.get("salaryString") or None
                slug = item.get("seoSlug", "")
                url = f"{BASE_URL}/{slug}" if slug else BASE_URL

                # locationEnhancedObj provides structured country data
                enhanced = (item.get("locationEnhancedObj") or [{}])[0]
                country = enhanced.get("country") or enhanced.get("formattedAddress") or None
                base_location = country if country else ("Worldwide" if remote else None)

                jobs.append(JobPosting(
                    source=self.SOURCE_NAME,
                    title=item.get("jobTitle", ""),
                    company=item.get("companyName", ""),
                    location=location,
                    url=url,
                    posted_date=posted_date,
                    description=None,
                    tags=tags,
                    salary=salary,
                    work_mode=work_mode,
                    base_location=base_location,
                ))

            print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
            return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] ⚠️ Error: {e}")
            return []
