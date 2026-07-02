"""Tangem career page scraper — Super.so / Notion-based.

Careers page: https://careers.tangem.com/
Job listings are rendered as HTML links grouped under h3 section headers.
No API — pure HTML scraping with BeautifulSoup.
"""

import time

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://careers.tangem.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; job_agent/1.0)"}

# Paths to skip — not job listings
SKIP_PATHS = {
    "/", "/talent-network", "/privacy", "/terms",
    "/don-t-see-your-role", "/closed-positions",
}

# Sections containing active job listings
JOB_SECTIONS = {"it and product", "sales and marketing"}


class TangemScraper(BaseScraper):
    SOURCE_NAME = "Tangem"
    ENABLED = True
    ACQUISITION_MODEL = "company_keyed"
    SUPPORTS_DISCOVERY = True

    def fetch(self, job_filter: JobFilter | None = None) -> list[JobPosting]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print(f"[{self.SOURCE_NAME}] ⚠️ beautifulsoup4 not installed")
            return []

        jobs: list[JobPosting] = []
        try:
            with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                r = client.get(BASE_URL)
                if r.status_code != 200:
                    print(f"  [{self.SOURCE_NAME}] HTTP {r.status_code}")
                    return []
                soup = BeautifulSoup(r.text, "html.parser")

                # Find all section headers and their following job links
                h3s = soup.find_all("h3")
                for h3 in h3s:
                    section_text = h3.get_text(strip=True).lower()
                    # Strip emoji prefixes like "📍"
                    section_text = section_text.lstrip("📍").strip()
                    if section_text not in JOB_SECTIONS:
                        continue

                    # Job links are <a> elements after this h3, before the next h3
                    current = h3
                    while True:
                        current = current.find_next_sibling()
                        if current is None or current.name == "h3":
                            break
                        if current.name != "a":
                            continue
                        href = (current.get("href") or "").strip()
                        if not href or not href.startswith("/"):
                            continue
                        if href.lower() in SKIP_PATHS or "/closed-positions/" in href.lower():
                            continue

                        # Extract title from link text — strip leading emoji
                        title = current.get_text(strip=True)
                        # Remove leading emoji if present
                        if title and not title[0].isascii():
                            title = title.lstrip("📍🔧💼📊🎨🛡️🔒⚙️📱🖥️🔗💻🗂️📈📉🎯🏷️🔍📝✏️🔔🚀💡🧑‍💻👩‍💻👨‍💻").strip()

                        if not title:
                            continue

                        # Strip leading emoji from title
                        import re
                        title = re.sub(r'^[\U0001F300-\U0001F9FF\s]+', '', title)

                        url = f"{BASE_URL}{href}"

                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=title,
                            company="Tangem",
                            location=section_text.title(),
                            url=url,
                            posted_date=None,
                            description=None,
                            work_mode=None,
                            base_location=None,
                        ))
        except Exception as e:
            print(f"  [{self.SOURCE_NAME}] {e}")
            return []

        print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
        return jobs
