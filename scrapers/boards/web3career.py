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

MAX_PAGES = 3  # shallow pagination; a URL stops early once its own pages start recycling


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

    # Static curated tag list (Constitution I: scrape broad, scorer is the single
    # filter point). Every URL is AGGREGATED, not a fallback — one failing doesn't
    # block the others. 2026-08-25 verification (browser-UA curl):
    #   /product-manager-jobs          → 15/page, works
    #   /product-manager+remote-jobs   → subset of base; adds 26 distinct over base's
    #                                    3 pages (45 + 26 = 71 unique total)
    #   /jobs/product-manager          → 404 (removed)
    #   /product-owner+remote-jobs     → 0 offers (removed)
    #   /product-manager+europe-jobs   → 0 offers, no region+tag grammar (removed)
    #   /web3-jobs-europe              → generic roles only, ~0 distinct PM/PO EU
    #                                    (location-agnostic base tag already covers
    #                                     Europe) — Europe branch dropped.
    URLS = [
        f"{BASE_URL}/product-manager-jobs",
        f"{BASE_URL}/product-manager+remote-jobs",
    ]

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        rows = self._collect_rows()
        if not rows:
            print(f"[{self.SOURCE_NAME}] All URLs failed — scraper disabled for this run")
            return []
        return self._build_postings(rows)

    def _fetch_one(self, url: str) -> str | None:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                print(f"[{self.SOURCE_NAME}] Fetched {url}")
                return r.text
            print(f"[{self.SOURCE_NAME}] {url} → HTTP {r.status_code}")
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] {url} → Error: {e}")
        return None

    def _extract_rows(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        rows = []
        for row in soup.find_all("tr", class_="table_row"):
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

                rows.append({
                    "title": title,
                    "company": company,
                    "url": url,
                    "listing_location": listing_location,
                    "posted_date": posted_date,
                })
            except Exception as e:
                print(f"[{self.SOURCE_NAME}] Parse error on row: {e}")
                continue
        return rows

    def _collect_rows(self) -> list[dict]:
        """Aggregate rows across all URLs and pages, deduped by job URL.

        Each URL is fetched independently and paginated up to MAX_PAGES. Dedup is
        GLOBAL (a job may appear on more than one tag), but the recycling stop is
        PER-URL: a subset tag (product-manager+remote ⊂ product-manager) can have a
        page whose rows are all already seen globally while its NEXT page still adds
        fresh jobs, so keying the stop on global dedup would truncate it. Instead we
        stop a URL only when a page repeats that URL's own earlier pages (recycle) or
        is empty/HTTP-failed.
        """
        seen_urls: set[str] = set()
        rows: list[dict] = []
        for url in self.URLS:
            url_seen: set[str] = set()
            for page in range(1, MAX_PAGES + 1):
                page_url = f"{url}?page={page}" if page > 1 else url
                html = self._fetch_one(page_url)
                if html is None:
                    break
                page_rows = self._extract_rows(html)
                if not page_rows:
                    break  # empty page = past the end of this tag
                fresh_for_url = [r for r in page_rows if r["url"] not in url_seen]
                for r in fresh_for_url:
                    url_seen.add(r["url"])
                new_rows = [r for r in fresh_for_url if r["url"] not in seen_urls]
                for r in new_rows:
                    seen_urls.add(r["url"])
                rows.extend(new_rows)
                if not fresh_for_url:
                    break  # this URL's own pages started recycling
        return rows

    def _build_postings(self, rows: list[dict]) -> list[JobPosting]:
        jobs = []
        for row in rows:
            url = row["url"]
            description, detail_location = _fetch_detail(url) if url else (None, None)
            time.sleep(0.3)

            resolved = detail_location or row["listing_location"] or None
            display_location = resolved or "Remote"

            jobs.append(JobPosting(
                source=self.SOURCE_NAME,
                title=row["title"],
                company=row["company"],
                location=display_location,
                url=url,
                posted_date=row["posted_date"],
                description=description,
                tags=[],
                salary=None,
                work_mode=None,  # deferred to LLM extraction (remote/hybrid/on-site/unknown)
                base_location=resolved,
            ))
        return jobs
