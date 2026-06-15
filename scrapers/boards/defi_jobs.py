import json
import re
import time
from datetime import date
import httpx

from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

# Source cascade (first that returns PM results wins):
#   A) defi.jobs HTML      — Webflow static, last updated 2023, title-only (no company/loc)
#   B) cryptocurrencyjobs.co — JS-rendered, static scraping returns no job data
#   C) crypto.jobs HTML    — 100 live jobs, company + location available → fallback

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": "https://google.com",
}

PM_TITLE_KEYWORDS = [
    "product manager",
    "head of product",
    "vp product",
    "vp of product",
    "director of product",
    "product lead",
    "product owner",
    "chief product",
    "cpo",
]

_CRYPTO_JOBS_WORK_MODE = {
    "🌍 remote": "remote",
    "🏢 hybrid": "hybrid",
    "🏙️ on-site": "on-site",
    "🏙 on-site": "on-site",
}


def _is_pm_title(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in PM_TITLE_KEYWORDS)


def _clean_text(text: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#*`>\[\]]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _fetch_description(url: str) -> str | None:
    """Fetch job description from a detail page via JSON-LD structured data."""
    try:
        from bs4 import BeautifulSoup
        r = httpx.get(url, headers=HEADERS, timeout=12, follow_redirects=True)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(sc.string or "")
                for node in (d.get("@graph", [d]) if isinstance(d, dict) else [d]):
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        desc = node.get("description", "")
                        if desc:
                            return _clean_text(desc)
            except Exception:
                pass
        return None
    except Exception:
        return None


class DeFiJobsScraper(BaseScraper):
    SOURCE_NAME = "DeFi Jobs"
    ENABLED = True

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            print(f"[{self.SOURCE_NAME}] ⚠️ beautifulsoup4 not installed")
            return []

        # ── Option A: defi.jobs HTML ──────────────────────────────────────────
        try:
            r = httpx.get("https://defi.jobs/", headers=HEADERS, timeout=10, follow_redirects=True)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                job_links = soup.find_all("a", class_="job-link", href=True)
                seen: set[str] = set()
                jobs: list[JobPosting] = []
                for a in job_links:
                    href = a.get("href", "")
                    if href in seen:
                        continue
                    seen.add(href)
                    title_tag = a.select_one(".j-title")
                    title = title_tag.get_text(strip=True) if title_tag else ""
                    if not title or not _is_pm_title(title):
                        continue
                    url = f"https://defi.jobs{href}" if href.startswith("/") else href
                    description = _fetch_description(url)
                    time.sleep(0.3)
                    jobs.append(JobPosting(
                        source=self.SOURCE_NAME,
                        title=title,
                        company="",
                        location="Remote",
                        url=url,
                        posted_date=None,
                        description=description,
                        tags=[],
                        salary=None,
                        work_mode="remote",
                        base_location=None,
                    ))
                if jobs:
                    print(f"[{self.SOURCE_NAME}] Source: defi.jobs — {len(jobs)} jobs fetched")
                    return jobs
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] defi.jobs error: {e}")

        # ── Option B: cryptocurrencyjobs.co ──────────────────────────────────
        # JS-rendered SPA — static scraping returns no job data, skipping silently

        # ── Option C: crypto.jobs HTML (full listing, client-side PM filter) ─
        # Each job appears twice (mobile + desktop); only the desktop card has itemprop='title'.
        # We filter on itemprop='title' presence directly — no href dedup needed.
        try:
            r = httpx.get("https://crypto.jobs/jobs", headers=HEADERS, timeout=15, follow_redirects=True)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                raw_links = [
                    a for a in soup.find_all("a", class_="job-url", href=True)
                    if a.select_one("[itemprop='title']")
                ]
                jobs2: list[JobPosting] = []
                for a in raw_links:
                    href = a.get("href", "")
                    title_tag = a.select_one("[itemprop='title']")
                    title = title_tag.get_text(strip=True) if title_tag else ""
                    if not title or not _is_pm_title(title):
                        continue
                    company_tag = a.select_one("[itemprop='name']")
                    company = company_tag.get_text(strip=True) if company_tag else ""
                    small = a.select_one(".hidden-xs small")
                    work_mode = "unknown"
                    location = "Remote"
                    if small:
                        spans = small.get_text(" ", strip=True).lower()
                        for key, val in _CRYPTO_JOBS_WORK_MODE.items():
                            if key in spans:
                                work_mode = val
                                break
                        if "hybrid" in spans:
                            location = "Hybrid"
                    posted_date = None
                    job_row = a.parent.parent
                    date_meta = job_row.select_one("meta[itemprop='datePosted']") if job_row else None
                    if date_meta and date_meta.get("content"):
                        try:
                            posted_date = date.fromisoformat(date_meta["content"][:10])
                        except ValueError:
                            pass
                    description = _fetch_description(href)
                    time.sleep(0.3)
                    jobs2.append(JobPosting(
                        source=self.SOURCE_NAME,
                        title=title,
                        company=company,
                        location=location,
                        url=href,
                        posted_date=posted_date,
                        description=description,
                        tags=[],
                        salary=None,
                        work_mode="remote",
                        base_location=None,
                    ))
                if jobs2:
                    print(f"[{self.SOURCE_NAME}] Source: crypto.jobs (fallback) — {len(jobs2)} jobs fetched")
                    return jobs2
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] crypto.jobs error: {e}")

        print(f"[{self.SOURCE_NAME}] ⚠️ All options failed — returning []")
        return []
