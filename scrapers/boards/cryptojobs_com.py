import re
import time
from datetime import date, timedelta
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting


def _parse_relative_date(text: str) -> date | None:
    """Convert relative date text to an approximate date."""
    t = text.lower().strip()
    today = date.today()
    if "hour" in t or t == "today":
        return today
    if t == "yesterday":
        return today - timedelta(days=1)
    if t == "last week":
        return today - timedelta(days=7)
    m = re.match(r"(\d+)\s+day", t)
    if m:
        return today - timedelta(days=int(m.group(1)))
    m = re.match(r"(\d+)\s+week", t)
    if m:
        return today - timedelta(weeks=int(m.group(1)))
    m = re.match(r"(\d+)\s+month", t)
    if m:
        return today - timedelta(days=int(m.group(1)) * 30)
    return None


def _clean_text(text: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#*`>\[\]]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


import httpx

BASE_URL = "https://www.cryptojobs.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://google.com",
}


def _fetch_description(url: str) -> str | None:
    try:
        from bs4 import BeautifulSoup
        r = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        el = soup.select_one(".details-area")
        if el:
            return _clean_text(el.get_text(separator=" ", strip=True))
        return None
    except Exception:
        return None


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
            job_articles = [a for a in articles if a.find("aside")]

            jobs = []
            for article in job_articles:
                try:
                    title_tag = article.select_one("aside h2 a")
                    if not title_tag:
                        continue
                    title = title_tag.get_text(strip=True)
                    href = title_tag.get("href", "")
                    url = href if href.startswith("http") else f"{BASE_URL}{href}"

                    company_tag = article.select_one("ul.info li a b")
                    company = company_tag.get_text(strip=True) if company_tag else ""

                    location = "Remote"
                    for li in article.select("ul.info li"):
                        if li.select_one("i.la-map-marker"):
                            loc_a = li.find("a")
                            if loc_a:
                                location = loc_a.get_text(strip=True)

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

                    tags = [a.get_text(strip=True) for a in article.select("ul.other li a")]

                    posted_date = None
                    date_span = article.select_one("ul.date span")
                    if date_span:
                        posted_date = _parse_relative_date(date_span.get_text(strip=True))

                    base_location = location if location and location != "Remote" else None

                    description = _fetch_description(url)
                    time.sleep(0.3)

                    jobs.append(JobPosting(
                        source=self.SOURCE_NAME,
                        title=title,
                        company=company,
                        location=location,
                        url=url,
                        posted_date=posted_date,
                        description=description,
                        tags=tags,
                        salary=None,
                        work_mode=work_mode,
                        base_location=base_location,
                    ))
                except Exception as e:
                    print(f"[{self.SOURCE_NAME}] Parse error: {e}")
                    continue

            print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
            return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] ⚠️ Error: {e}")
            return []
