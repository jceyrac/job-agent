import re
import requests
from datetime import date
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://web3.career"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://google.com",
}


class Web3CareerScraper(BaseScraper):
    SOURCE_NAME = "Web3Career"
    ENABLED = True

    URLS = [
        f"{BASE_URL}/product-manager-jobs",
        f"{BASE_URL}/jobs/product-manager",
    ]

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        html = self._fetch_html()
        if not html:
            print(f"[{self.SOURCE_NAME}] All URLs failed — scraper disabled for this run")
            return []
        return self._parse(html)

    def _fetch_html(self) -> str | None:
        for url in self.URLS:
            try:
                r = requests.get(url, headers=HEADERS, timeout=10)
                if r.status_code == 200:
                    print(f"[{self.SOURCE_NAME}] Fetched {url}")
                    return r.text
                print(f"[{self.SOURCE_NAME}] {url} → HTTP {r.status_code}")
            except Exception as e:
                print(f"[{self.SOURCE_NAME}] {url} → Error: {e}")
        return None

    def _parse(self, html: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("tr", class_="table_row")
        jobs = []
        for row in rows:
            try:
                # Title + relative URL
                title_tag = row.find("h2")
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                link_tag = title_tag.find_parent("a") or row.find("a", href=True)
                href = link_tag["href"] if link_tag else ""
                url = f"{BASE_URL}{href}" if href.startswith("/") else href

                # Company — first <h3>
                company_tag = row.find("h3")
                company = company_tag.get_text(strip=True) if company_tag else ""

                # Location — small grey <span>
                loc_tag = row.find("span", style=lambda s: s and "d5d3d3" in s)
                location = loc_tag.get_text(strip=True) if loc_tag else "Remote"

                # Date
                time_tag = row.find("time")
                posted_date = None
                if time_tag and time_tag.get("datetime"):
                    try:
                        from datetime import datetime
                        posted_date = datetime.fromisoformat(
                            time_tag["datetime"].split("+")[0].strip()
                        ).date()
                    except ValueError:
                        pass

                # Web3Career is a remote-first board.
                # Location is a base anchor when present; "," means no location specified.
                base_location = location if location and location != "," else None
                display_location = location if location and location != "," else "Remote"

                jobs.append(JobPosting(
                    source=self.SOURCE_NAME,
                    title=title,
                    company=company,
                    location=display_location,
                    url=url,
                    posted_date=posted_date,
                    description=None,
                    tags=[],
                    salary=None,
                    work_mode="remote",
                    base_location=base_location,
                ))
            except Exception as e:
                print(f"[{self.SOURCE_NAME}] Parse error on row: {e}")
                continue
        return jobs
