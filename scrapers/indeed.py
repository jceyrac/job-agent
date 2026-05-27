"""Indeed scraper using python-jobspy.

Re-enable after IP block clears:
  python -c "from storage import JobStorage; db = JobStorage('data/jobs.db'); db.set_config('scraper.indeed.enabled', 'true')"
"""

import time

from scrapers.base import BaseScraper
from scrapers._jobspy_helpers import scrape_with_timeout, add_unique
from models import JobFilter, JobPosting

SEARCH_TERMS_INDEED = [
    "product manager",
    "product owner",
    "head of product",
    "product lead",
    "product director",
    "senior product",
    "lead product manager",
]

INDEED_COUNTRIES = [
    "switzerland",
    "france",
    "uk",
    "netherlands",
    "spain",
    "portugal",
    "austria",
    "belgium",
    "ireland",
    "italy",
    "czechia",
    "germany",
    "hungary",
    "türkiye",
]

MAX_CONSECUTIVE_TIMEOUTS = 3


class IndeedScraper(BaseScraper):
    SOURCE_NAME = "Indeed"
    ENABLED = True

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        all_jobs: list[JobPosting] = []
        seen_urls: set[str] = set()
        total, dupes = 0, 0
        consecutive_timeouts = 0

        for term in SEARCH_TERMS_INDEED:
            for country in INDEED_COUNTRIES:
                results_wanted = 50 if country == "switzerland" else 25
                try:
                    df = scrape_with_timeout(
                        60,
                        site_name=["indeed"],
                        search_term=term,
                        results_wanted=results_wanted,
                        hours_old=240,
                        country_indeed=country,
                        verbose=0,
                    )
                    if df is None:
                        consecutive_timeouts += 1
                        print(
                            f"  ⚠️ [Indeed] '{term}' [{country}]: timed out "
                            f"({consecutive_timeouts}/{MAX_CONSECUTIVE_TIMEOUTS})"
                        )
                        if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                            self.disable(
                                f"{MAX_CONSECUTIVE_TIMEOUTS} consecutive timeouts — likely IP block"
                            )
                            return all_jobs
                        time.sleep(2)
                        continue
                    consecutive_timeouts = 0
                    new, skipped = add_unique(df, "Indeed", seen_urls, all_jobs)
                    total += new
                    dupes += skipped
                    print(f"  [Indeed]   '{term}' [{country}]: {new} new, {skipped} dupes")
                except Exception as e:
                    print(f"  ⚠️ Indeed '{term}' [{country}]: {e}")
                time.sleep(2)

        print(f"  [Indeed] Total: {total} | Dupes: {dupes} | Unique: {len(all_jobs)}")
        return all_jobs
