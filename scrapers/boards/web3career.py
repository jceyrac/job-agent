import json
import re
import time
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


def _clean_text(text: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#*`>\[\]]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _fetch_detail(url: str) -> tuple[str | None, str | None]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None, None
        soup = BeautifulSoup(r.text, "html.parser")
        description = None
        detail_location = None
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(sc.string or "")
                for node in (d.get("@graph", [d]) if isinstance(d, dict) else [d]):
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        desc = node.get("description", "")
                        if desc:
                            description = _clean_text(desc)
                        jl = node.get("jobLocation")
                        if isinstance(jl, dict):
                            addr = jl.get("address", {})
                            if isinstance(addr, dict):
                                locality = (addr.get("addressLocality") or "").strip()
                                country = (addr.get("addressCountry") or "").strip()
                                parts = [p for p in [locality, country] if p]
                                if parts:
                                    detail_location = ", ".join(parts)
            except Exception:
                pass
        if not detail_location:
            for p in soup.select(".mysticky p"):
                text = p.get_text(strip=True)
                if text.startswith("Location:"):
                    detail_location = text[len("Location:"):].strip()
                    break
        if not description:
            el = soup.select_one(".main-border-sides-job")
            if el:
                description = _clean_text(el.get_text(separator=" ", strip=True))
        return description, detail_location
    except Exception:
        return None, None


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
                title_tag = row.find("h2")
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                link_tag = title_tag.find_parent("a") or row.find("a", href=True)
                href = link_tag["href"] if link_tag else ""
                url = f"{BASE_URL}{href}" if href.startswith("/") else href

                company_tag = row.find("h3")
                company = company_tag.get_text(strip=True) if company_tag else ""

                loc_tag = row.find("span", style=lambda s: s and "d5d3d3" in s)
                raw_listing = loc_tag.get_text(strip=True) if loc_tag else ""
                listing_location = raw_listing if raw_listing and raw_listing != "," else None

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

                description, detail_location = _fetch_detail(url) if url else (None, None)
                time.sleep(0.3)

                resolved = detail_location or listing_location or None
                display_location = resolved or "Remote"

                jobs.append(JobPosting(
                    source=self.SOURCE_NAME,
                    title=title,
                    company=company,
                    location=display_location,
                    url=url,
                    posted_date=posted_date,
                    description=description,
                    tags=[],
                    salary=None,
                    work_mode="remote",
                    base_location=resolved,
                ))
            except Exception as e:
                print(f"[{self.SOURCE_NAME}] Parse error on row: {e}")
                continue
        return jobs
