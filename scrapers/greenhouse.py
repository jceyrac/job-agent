import re
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


def _parse_greenhouse_location(name: str) -> tuple[str, str]:
    """
    Parse a Greenhouse location.name string into (work_mode, base_location).

    Examples:
      "Remote - USA"          → ("remote", "United States")
      "GLOBAL - Remote"       → ("remote", "Worldwide")
      "Remote - EMEA"         → ("remote", "EMEA")
      "Vancouver, BC, Canada" → ("on-site", "Canada")
      "Dublin"                → ("on-site", "Not found")
      "Brooklyn, NY or Remote (North America)" → ("remote", "United States")
    """
    s = name.strip()
    lower = s.lower()

    work_mode = "remote" if "remote" in lower else ("hybrid" if "hybrid" in lower else "on-site")

    # Strip "Remote - " / "GLOBAL - Remote" prefix to isolate base region
    base_str = re.sub(r"(?i)^(global\s*-\s*remote|remote\s*-\s*)", "", s).strip()
    # Take first option when there are alternatives ("X or Y", "X, Y, Z or Remote in W")
    base_str = re.split(r"\s+or\s+", base_str, maxsplit=1)[0].strip()
    # Drop trailing parentheticals like "(North America)", "(Remote)"
    base_str = re.sub(r"\s*\(.*?\)", "", base_str).strip()

    # Try to extract a country from the end of "City, State, Country"
    parts = [p.strip() for p in base_str.split(",")]
    country_hint = parts[-1] if parts else ""

    COUNTRY_ALIASES = {
        "usa": "United States", "us": "United States", "united states": "United States",
        "uk": "United Kingdom", "united kingdom": "United Kingdom",
        "canada": "Canada", "germany": "Germany", "france": "France",
        "israel": "Israel", "singapore": "Singapore", "ireland": "Ireland",
        "emea": "EMEA", "apac": "APAC", "latam": "LATAM",
        "worldwide": "Worldwide", "global": "Worldwide",
    }

    # If entire base_str was stripped away (was "Remote" / "GLOBAL - Remote")
    if not base_str or base_str.lower() in ("remote", "global", "worldwide"):
        return work_mode, "Worldwide"

    # State abbreviations like "CA", "NY" → United States
    US_STATES = {"CA","NY","TX","WA","MA","IL","CO","FL","GA","VA","OR","PA","NJ","NC","AZ","MN","OH","MD","DC"}
    if country_hint in US_STATES or (len(parts) >= 2 and parts[-2] in US_STATES):
        return work_mode, "United States"

    resolved = COUNTRY_ALIASES.get(country_hint.lower())
    if resolved:
        return work_mode, resolved

    # Fall back to the last non-empty part as-is if it looks like a country (>3 chars)
    if len(country_hint) > 3:
        return work_mode, country_hint

    return work_mode, None


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
                        work_mode, base_location = _parse_greenhouse_location(location)

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

                        if base_location == "United States":
                            continue

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
                            work_mode=work_mode,
                            base_location=base_location,
                        ))

                    time.sleep(0.5)

                except Exception:
                    continue

        if board_summary:
            print(f"[{self.SOURCE_NAME}] {' | '.join(board_summary)}")
        print(f"[{self.SOURCE_NAME}] {len(jobs)} PM jobs fetched across {len(CRYPTO_WEB3_BOARDS)} boards")
        return jobs
