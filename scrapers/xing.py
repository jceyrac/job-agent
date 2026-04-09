import json
import re
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://google.com",
}


class XingScraper(BaseScraper):
    SOURCE_NAME = "Xing"
    ENABLED = True
    URL = "https://www.xing.com/jobs/search?keywords=product+manager+crypto&location=Remote"

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            from bs4 import BeautifulSoup
            r = httpx.get(self.URL, headers=HEADERS, timeout=15, follow_redirects=True)

            if r.status_code == 403:
                print(f"[{self.SOURCE_NAME}] ⚠️ Blocked (HTTP 403)")
                return []
            if r.status_code != 200:
                print(f"[{self.SOURCE_NAME}] ⚠️ HTTP {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")

            # Try __NEXT_DATA__ or embedded JSON
            nd = soup.find("script", id="__NEXT_DATA__")
            if nd:
                data = json.loads(nd.string)
                jobs_data = (data.get("props", {}).get("pageProps", {})
                             .get("jobs", data.get("props", {}).get("pageProps", {})
                                  .get("results", [])))
                if jobs_data:
                    jobs = []
                    for item in jobs_data:
                        raw_loc = item.get("location", "")
                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=item.get("title", ""),
                            company=item.get("company", {}).get("name", "") if isinstance(item.get("company"), dict) else str(item.get("company", "")),
                            location=raw_loc or "Remote",
                            url=item.get("url", item.get("jobUrl", "")),
                            posted_date=None,
                            description=None,
                            tags=[],
                            salary=None,
                            work_mode="unknown",
                            base_location=raw_loc if raw_loc else None,
                        ))
                    print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
                    return jobs

            # Fallback: look for job cards in HTML
            cards = soup.select("[data-testid='job-listing-item']") or soup.select(".job-listing")
            if not cards:
                print(f"[{self.SOURCE_NAME}] ⚠️ No job data found (JS-rendered?)")
                return []

            jobs = []
            for card in cards:
                title_el = card.select_one("h2, h3, [class*='title']")
                title = title_el.get_text(strip=True) if title_el else ""
                link = card.select_one("a[href]")
                url = link["href"] if link else ""
                if url and not url.startswith("http"):
                    url = "https://www.xing.com" + url
                jobs.append(JobPosting(
                    source=self.SOURCE_NAME,
                    title=title,
                    company="",
                    location="Remote",
                    url=url,
                    posted_date=None,
                    description=None,
                    tags=[],
                    salary=None,
                    work_mode="unknown",
                    base_location=None,
                ))

            print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
            return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] ⚠️ Error: {e}")
            return []
