import time

from scrapers.base import BaseScraper
from scrapers._jobspy_helpers import scrape_with_timeout, add_unique
from models import JobFilter, JobPosting

SEARCH_TERMS_LINKEDIN = [
    "product manager",
    "product owner",
    "head of product",
    "product lead",
    "product director",
    "senior product",
    "lead product manager",
]

LINKEDIN_LOCATIONS = [
    "Switzerland",
    "France",
    "United Kingdom",
    "Netherlands",
    "Spain",
    "Portugal",
    "Austria",
    "Belgium",
    "Ireland",
    "Italy",
    "Germany",
    "Czechia",
    "Hungary",
    "Türkiye",
]


class LinkedInScraper(BaseScraper):
    SOURCE_NAME = "LinkedIn"
    ENABLED = True

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            from jobspy import scrape_jobs
        except ImportError:
            print(f"[{self.SOURCE_NAME}] python-jobspy not installed — pip install python-jobspy")
            return []

        from profiles import get_active_profile
        _p = get_active_profile()
        terms = _p.search_query_titles or SEARCH_TERMS_LINKEDIN
        locations = _p.search_locations or LINKEDIN_LOCATIONS

        start = time.time()
        all_jobs: list[JobPosting] = []
        seen_urls: set[str] = set()
        linkedin_total, dupes = 0, 0

        # LinkedIn — query each target location directly. Switzerland gets more
        # results because it's the primary target market.
        for term in terms:
            for location in locations:
                results_wanted = 25 if location == "Switzerland" else 15
                try:
                    df = scrape_with_timeout(
                        90,
                        site_name=["linkedin"],
                        search_term=term,
                        location=location,
                        results_wanted=results_wanted,
                        hours_old=240,
                        linkedin_fetch_description=True,
                        verbose=0,
                    )
                    if df is None:
                        print(f"  ⚠️ [LinkedIn] '{term}' [{location}]: timed out — skipped")
                        time.sleep(2)
                        continue
                    new, skipped = add_unique(df, "LinkedIn", seen_urls, all_jobs)
                    linkedin_total += new
                    dupes += skipped
                    print(f"  [LinkedIn] '{term}' [{location}]: {new} new, {skipped} dupes")
                except Exception as e:
                    print(f"  ⚠️ LinkedIn '{term}' [{location}]: {e}")
                time.sleep(4)

        elapsed = time.time() - start
        print(
            f"  [LinkedIn] Total: {linkedin_total} | "
            f"Dupes removed: {dupes} | Total unique: {len(all_jobs)} | "
            f"Time: {elapsed:.1f}s"
        )
        return all_jobs
