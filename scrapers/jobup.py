import re
from datetime import date, timedelta
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting


def _parse_relative_date(text: str) -> date | None:
    """Convert Jobup relative date text to an approximate date."""
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

import httpx

BASE_URL = "https://www.jobup.ch"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "fr-CH,fr;q=0.9,en;q=0.5",
    "Referer": "https://google.com",
}


class JobupScraper(BaseScraper):
    SOURCE_NAME = "Jobup"
    ENABLED = True
    SEARCH_TERMS = ["product+manager", "product+owner", "head+of+product", "product+lead"]

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            from bs4 import BeautifulSoup
            seen_urls: set[str] = set()
            jobs = []
            for term in self.SEARCH_TERMS:
                url = f"{BASE_URL}/en/jobs/?term={term}&region=0"
                r = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
                if r.status_code != 200:
                    print(f"[{self.SOURCE_NAME}] ⚠️ HTTP {r.status_code} for term={term}")
                    continue

                soup = BeautifulSoup(r.text, "html.parser")
                cards = soup.select('[data-cy="serp-item"]')
                if not cards:
                    print(f"[{self.SOURCE_NAME}] ⚠️ No job cards found for term={term}")
                    continue

                for card in cards:
                    try:
                        link = card.select_one('[data-cy="job-link"]')
                        if not link:
                            continue

                        title = link.get("title", "").strip()
                        href = link.get("href", "")
                        card_url = f"{BASE_URL}{href}" if href.startswith("/") else href
                        if card_url in seen_urls:
                            continue
                        seen_urls.add(card_url)

                        # Extract text fields: location follows "Place of work" label
                        texts = [t.strip() for t in card.get_text("\n").split("\n") if t.strip()]

                        # First text field is relative date ("Last week", "2 days ago", etc.)
                        posted_date = _parse_relative_date(texts[0]) if texts else None

                        location = "Switzerland"
                        for i, t in enumerate(texts):
                            if t == "Place of work" and i + 2 < len(texts):
                                location = texts[i + 2]  # skip ":"

                        # Company: last text before UI stop-words, excluding metadata keys/values
                        stop_words = {"Promoted", "Easy apply", "New"}
                        meta_fragments = {"Place of work", "Workload", "Contract type",
                                          "Permanent", "Temporary", "Fixed-term", ":"}
                        company = ""
                        for t in reversed(texts):
                            if t in stop_words or t.startswith("Is this"):
                                continue
                            if not any(m in t for m in meta_fragments) and not t.endswith("%"):
                                company = t
                                break

                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=title,
                            company=company,
                            location=location,
                            url=card_url,
                            posted_date=posted_date,
                            description=None,
                            tags=[],
                            salary=None,
                            work_mode="on-site",   # Jobup is a Swiss on-site board
                            base_location=location if location != "Switzerland" else "Switzerland",
                        ))
                    except Exception as e:
                        print(f"[{self.SOURCE_NAME}] Parse error: {e}")
                        continue

            print(f"[{self.SOURCE_NAME}] {len(jobs)} jobs fetched")
            return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] ⚠️ Error: {e}")
            return []
