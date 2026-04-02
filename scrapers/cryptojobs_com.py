import re
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

import httpx

BASE_URL = "https://www.cryptojobs.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://google.com",
}


class CryptoJobsComScraper(BaseScraper):
    SOURCE_NAME = "CryptoJobs.com"
    ENABLED = True
    URL = f"{BASE_URL}/jobs?keyword=product+manager"

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            from bs4 import BeautifulSoup
            r = httpx.get(self.URL, headers=HEADERS, timeout=15, follow_redirects=True)
            if r.status_code != 200:
                print(f"[{self.SOURCE_NAME}] ⚠️ HTTP {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            articles = soup.find_all("article")
            # First article is the search/filter form — skip it
            job_articles = [a for a in articles if a.find("aside")]

            jobs = []
            for article in job_articles:
                try:
                    # Title + URL
                    title_tag = article.select_one("aside h2 a")
                    if not title_tag:
                        continue
                    title = title_tag.get_text(strip=True)
                    href = title_tag.get("href", "")
                    url = href if href.startswith("http") else f"{BASE_URL}{href}"

                    # Company — bold text inside first info list item with link
                    company_tag = article.select_one("ul.info li a b")
                    company = company_tag.get_text(strip=True) if company_tag else ""

                    # Location — li with map-marker icon
                    location = "Remote"
                    for li in article.select("ul.info li"):
                        if li.select_one("i.la-map-marker"):
                            loc_a = li.find("a")
                            if loc_a:
                                location = loc_a.get_text(strip=True)

                    # Work mode — li with clock icon contains Onsite/Remote/Hybrid
                    work_mode = "unknown"
                    for li in article.select("ul.info li"):
                        if li.select_one("i.la-clock"):
                            mode_text = li.get_text(strip=True).lower()
                            if "remote" in mode_text:
                                work_mode = "remote"
                            elif "hybrid" in mode_text:
                                work_mode = "hybrid"
                            elif "onsite" in mode_text or "on-site" in mode_text:
                                work_mode = "on-site"

                    # Tags from "other" ul
                    tags = [a.get_text(strip=True) for a in article.select("ul.other li a")]

                    jobs.append(JobPosting(
                        source=self.SOURCE_NAME,
                        title=title,
                        company=company,
                        location=location,
                        url=url,
                        posted_date=None,
                        description=None,
                        tags=tags,
                        salary=None,
                        work_mode=work_mode,
                    ))
                except Exception as e:
                    print(f"[{self.SOURCE_NAME}] Parse error: {e}")
                    continue

            print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
            return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] ⚠️ Error: {e}")
            return []
