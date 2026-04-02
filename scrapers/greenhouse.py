import time
from datetime import datetime

import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

BASE_URL = "https://boards-api.greenhouse.io/v1/boards"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
}

CRYPTO_WEB3_BOARDS = [
    "coinbase",
    "chainalysis",
    "paxos",
    "avalabs",
    "consensys",
    "fireblocks",
    "anchorage",
    "figment",
    "bitgo",
    "kraken",
    "gemini",
    "ripple",
    "near",
    "aptos",
    "mysten",        # Sui blockchain
    "alchemy",
    "infura",
    "opensea",
    "dydx",
    "uniswap",
    "aave",
    "blockdaemon",
    "ledger",
    "blockchain",    # Blockchain.com
    "circle",
    "robinhood",
    "stripe",        # fintech
    "brex",          # fintech
    "mercury",       # fintech
    "ramp",          # fintech
]

PM_TITLE_KEYWORDS = [
    "product manager",
    " pm ",
    "head of product",
    "vp product",
    "vp of product",
    "director of product",
    "product lead",
    "product owner",
    "cpo",
    "chief product",
]


def _is_pm_title(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in PM_TITLE_KEYWORDS)


class GreenhouseScraper(BaseScraper):
    SOURCE_NAME = "Greenhouse"
    ENABLED = True

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print(f"[{self.SOURCE_NAME}] ⚠️ beautifulsoup4 not installed")
            return []

        jobs: list[JobPosting] = []
        board_summary: list[str] = []

        with httpx.Client(headers=HEADERS, timeout=10, follow_redirects=True) as client:
            for token in CRYPTO_WEB3_BOARDS:
                try:
                    r = client.get(f"{BASE_URL}/{token}/jobs?content=true")
                    if r.status_code == 404:
                        continue
                    if r.status_code != 200:
                        continue

                    data = r.json()
                    raw_jobs = data.get("jobs", [])
                    pm_jobs = [j for j in raw_jobs if _is_pm_title(j.get("title", ""))]

                    if pm_jobs:
                        board_summary.append(f"{token}: {len(pm_jobs)} PM job{'s' if len(pm_jobs) > 1 else ''}")

                    for item in pm_jobs:
                        # Extract company from absolute_url or fall back to token
                        abs_url = item.get("absolute_url", "")
                        company = token.capitalize()
                        # greenhouse URLs contain the company slug: /company-name/jobs/...
                        if "greenhouse.io" in abs_url:
                            parts = abs_url.split("/")
                            for i, p in enumerate(parts):
                                if p == "jobs" and i > 0:
                                    company = parts[i - 1].replace("-", " ").title()
                                    break

                        location_obj = item.get("location") or {}
                        location = location_obj.get("name") or "Unknown"

                        posted_date = None
                        raw_date = item.get("updated_at", "")
                        if raw_date:
                            try:
                                posted_date = datetime.fromisoformat(raw_date[:10]).date()
                            except ValueError:
                                pass

                        raw_content = item.get("content") or ""
                        description = ""
                        if raw_content:
                            description = BeautifulSoup(raw_content, "html.parser").get_text(" ", strip=True)[:300]

                        jobs.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=item.get("title", ""),
                            company=company,
                            location=location,
                            url=abs_url,
                            posted_date=posted_date,
                            description=description or None,
                            tags=[],
                            salary=None,
                            work_mode="unknown",
                        ))

                    time.sleep(0.5)

                except Exception:
                    continue

        if board_summary:
            print(f"[{self.SOURCE_NAME}] {' | '.join(board_summary)}")
        print(f"[{self.SOURCE_NAME}] {len(jobs)} PM jobs fetched across {len(CRYPTO_WEB3_BOARDS)} boards")
        return jobs
