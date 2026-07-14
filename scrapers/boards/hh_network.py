"""HeadHunter scraper — hh.ru, Moscow-only.

Scrapes the server-side rendered (SSR) search page using stable data-qa
attributes. The search/vacancy endpoint supports query params (area, page,
search_field, items_on_page) — confirmed working 2026-07-09.

Read-only discovery — the applicant-side API was closed Dec 2024.
No auth required. Egress via existing gluetun-scrape proxy.

Spec 023.
"""

import re
import time
from datetime import date

import requests
from bs4 import BeautifulSoup

from models import JobFilter, JobPosting
from scrapers.base import BaseScraper

# ── Config ─────────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "job-agent/1.0 (jceyrac@pm.me)"}
DETAIL_SLEEP = 0.5       # seconds between detail calls
ITEMS_PER_PAGE = 50       # max per SERP page
MAX_PAGES = 3             # 150 results max per query
MOSCOW_AREA = "1"         # hh.ru area ID for Moscow

# Latin PM keywords for the Option-A counter (observability only).
_LATIN_PM_KEYWORDS = [
    "product manager", "product owner", "product lead", "product director",
    "head of product", "vp product", "cpo", "chief product",
]


class HhNetworkScraper(BaseScraper):
    SOURCE_NAME = "HeadHunter"
    ENABLED = True
    ACQUISITION_MODEL = "board"
    SUPPORTS_DISCOVERY = True

    def _latin_title_count(self, postings: list[JobPosting]) -> int:
        return sum(
            1 for p in postings
            if any(kw in p.title.lower() for kw in _LATIN_PM_KEYWORDS)
        )

    def _parse_serp(self, html: str) -> list[dict]:
        """Parse SSR search results page into a list of raw job dicts.

        Uses the stable data-qa attributes that hh.ru renders server-side.
        Titles, companies, and locations are separate element lists that
        appear in matching order — we zip them together.
        """
        soup = BeautifulSoup(html, "html.parser")

        title_els = soup.select("[data-qa='serp-item__title']")
        company_els = soup.select("[data-qa='vacancy-serp__vacancy-employer']")
        loc_els = soup.select("[data-qa='vacancy-serp__vacancy-address']")
        salary_els = soup.select("[data-qa='vacancy-serp__vacancy-compensation']")

        n = min(len(title_els), len(company_els), len(loc_els))
        results = []

        for i in range(n):
            title_el = title_els[i]
            title = title_el.get_text(strip=True)
            if not title:
                continue

            # URL from the title link
            url = ""
            if title_el.name == "a" and title_el.get("href"):
                url = title_el["href"]
            if url and url.startswith("/"):
                url = f"https://hh.ru{url}"
            if not url or "adsrv.hh.ru" in url:
                continue

            company = company_els[i].get_text(strip=True) if i < len(company_els) else ""
            location_raw = loc_els[i].get_text(strip=True) if i < len(loc_els) else ""

            salary_text = None
            if i < len(salary_els):
                salary_text = salary_els[i].get_text(strip=True) or None

            # Work mode hint — look for remote indicators in location/salary
            work_mode_hint = "unknown"
            combined = (location_raw + " " + (salary_text or "")).lower()
            if any(w in combined for w in ("удален", "удалён", "remote", "удаленно")):
                work_mode_hint = "remote"

            # Location with country context
            if location_raw:
                full_location = f"{location_raw}, Russia"
            else:
                full_location = "Moscow, Russia"

            results.append({
                "title": title,
                "company": company,
                "url": url,
                "location": full_location,
                "salary_text": salary_text,
                "work_mode_hint": work_mode_hint,
            })

        return results

    def _fetch_detail(self, url: str) -> str:
        """Fetch full vacancy description from the detail page."""
        try:
            time.sleep(DETAIL_SLEEP)
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                return ""
            soup = BeautifulSoup(resp.text, "html.parser")
            desc_el = soup.select_one("[data-qa='vacancy-description']")
            raw = str(desc_el) if desc_el else resp.text
            text = re.sub(r"<[^>]+>", " ", raw)
            text = re.sub(r"\s+", " ", text).strip()
            return text
        except Exception:
            return ""

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        """Fetch Moscow vacancies from hh.ru via SSR search.

        Uses the /search/vacancy endpoint which supports area filtering
        (area=1 = Moscow) and title-only search (search_field=name).
        """
        titles = list(job_filter.titles) if job_filter.titles else ["product manager"]
        postings: list[JobPosting] = []
        seen_urls: set[str] = set()
        n_fetched = 0

        for query in titles:
            for page in range(MAX_PAGES):
                search_url = (
                    f"https://hh.ru/search/vacancy"
                    f"?area={MOSCOW_AREA}"
                    f"&text={requests.utils.quote(query)}"
                    f"&search_field=name"
                    f"&items_on_page={ITEMS_PER_PAGE}"
                    f"&page={page}"
                )
                try:
                    resp = requests.get(search_url, headers=HEADERS, timeout=30)
                    if resp.status_code != 200:
                        print(f"  ⚠️  [HeadHunter] returned {resp.status_code} "
                              f"for '{query}' page {page} — stopping")
                        break

                    jobs = self._parse_serp(resp.text)
                    n_fetched += len(jobs)
                    print(f"  [HeadHunter] '{query}' page {page}: {len(jobs)} results")

                    for j in jobs:
                        if j["url"] in seen_urls:
                            continue
                        seen_urls.add(j["url"])

                        description = self._fetch_detail(j["url"])

                        base_location = j["location"] if j["location"].lower() not in (
                            "remote", "", "удаленно") else None

                        postings.append(JobPosting(
                            source=self.SOURCE_NAME,
                            title=j["title"],
                            company=j["company"],
                            location=j["location"],
                            url=j["url"],
                            description=description or None,
                            posted_date=date.today(),
                            salary=j.get("salary_text"),
                            work_mode=j["work_mode_hint"],
                            base_location=base_location,
                            tags=[],
                        ))

                    # Stop if fewer results than page size
                    if len(jobs) < ITEMS_PER_PAGE:
                        break

                    time.sleep(0.3)  # courtesy delay between pages

                except Exception as e:
                    print(f"  ⚠️  [HeadHunter] error for '{query}' page {page}: {e}")
                    break

        n_latin = self._latin_title_count(postings)
        n_cyrillic = len(postings) - n_latin

        print(f"HeadHunter: {n_fetched} from SERP, {len(postings)} after dedup, "
              f"{n_latin} with Latin PM keyword")
        if n_cyrillic:
            print(f"  ({n_cyrillic} with no Latin PM keyword — will not survive "
                  f"SQL title pre-filter; Option B deferred per spec)")

        return postings
