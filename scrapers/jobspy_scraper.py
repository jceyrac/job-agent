import math
import re
import time
from datetime import date, datetime
from scrapers.base import BaseScraper
from models import JobFilter, JobPosting

SEARCH_TERMS_LINKEDIN = [
    "product manager",
    "product owner",
    "head of product",
    "product lead",
]

SEARCH_TERMS_INDEED = [
    "product manager",
    "product owner",
    "head of product",
    "product lead",
]

# Always queried against Switzerland directly — not subject to the worldwide-first skip
SEARCH_TERMS_INDEED_CH = [
    "product manager",
    "product owner",
    "head of product",
    "product lead",
]

INDEED_COUNTRIES = [
    "worldwide",
    "uk",
    "france",
    "germany",
    "netherlands",
    "spain",
    "portugal",
    "poland",
    "united arab emirates",
]


class JobSpyScraper(BaseScraper):
    SOURCE_NAME = "JobSpy"
    ENABLED = True

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        try:
            from jobspy import scrape_jobs
        except ImportError:
            print(f"[{self.SOURCE_NAME}] python-jobspy not installed — pip install python-jobspy")
            return []

        start = time.time()
        all_jobs: list[JobPosting] = []
        seen_urls: set[str] = set()
        linkedin_total, indeed_total, dupes = 0, 0, 0

        # LinkedIn
        for term in SEARCH_TERMS_LINKEDIN:
            try:
                df = scrape_jobs(
                    site_name=["linkedin"],
                    search_term=term,
                    location="Europe",
                    results_wanted=20,  # keep conservative — LinkedIn rate-limits aggressively
                    hours_old=120,
                    linkedin_fetch_description=True,
                    verbose=0,
                )
                new, skipped = self._add_unique(df, "LinkedIn", seen_urls, all_jobs)
                linkedin_total += new
                dupes += skipped
                print(f"  [LinkedIn] '{term}': {new} new, {skipped} dupes")
            except Exception as e:
                print(f"  ⚠️ LinkedIn '{term}': {e}")
            time.sleep(3)

        # Indeed — try "worldwide" first, fall back to per-country if too few results
        for term in SEARCH_TERMS_INDEED:
            term_new = 0
            try:
                df = scrape_jobs(
                    site_name=["indeed"],
                    search_term=term,
                    results_wanted=30,
                    hours_old=120,
                    country_indeed="worldwide",
                    verbose=0,
                )
                new, skipped = self._add_unique(df, "Indeed", seen_urls, all_jobs)
                indeed_total += new
                dupes += skipped
                term_new += new
                print(f"  [Indeed]   '{term}' [worldwide]: {new} new, {skipped} dupes")
            except Exception as e:
                print(f"  ⚠️ Indeed '{term}' [worldwide]: {e}")
            time.sleep(2)

            if term_new >= 15:
                continue  # worldwide gave enough — skip per-country for this term

            for country in INDEED_COUNTRIES[1:]:  # skip "worldwide", already tried
                try:
                    df = scrape_jobs(
                        site_name=["indeed"],
                        search_term=term,
                        results_wanted=30,
                        hours_old=120,
                        country_indeed=country,
                        verbose=0,
                    )
                    new, skipped = self._add_unique(df, "Indeed", seen_urls, all_jobs)
                    indeed_total += new
                    dupes += skipped
                    term_new += new
                    print(f"  [Indeed]   '{term}' [{country}]: {new} new, {skipped} dupes")
                except Exception as e:
                    print(f"  ⚠️ Indeed '{term}' [{country}]: {e}")
                time.sleep(2)

        # Switzerland — always queried directly, bypasses worldwide-first logic
        for term in SEARCH_TERMS_INDEED_CH:
            try:
                df = scrape_jobs(
                    site_name=["indeed"],
                    search_term=term,
                    results_wanted=50,
                    hours_old=120,
                    country_indeed="switzerland",
                    verbose=0,
                )
                new, skipped = self._add_unique(df, "Indeed", seen_urls, all_jobs)
                indeed_total += new
                dupes += skipped
                print(f"  [Indeed]   '{term}' [switzerland]: {new} new, {skipped} dupes")
            except Exception as e:
                print(f"  ⚠️ Indeed '{term}' [switzerland]: {e}")
            time.sleep(2)

        elapsed = time.time() - start
        print(
            f"  [JobSpy] LinkedIn: {linkedin_total} | Indeed: {indeed_total} | "
            f"Dupes removed: {dupes} | Total unique: {len(all_jobs)} | "
            f"Time: {elapsed:.1f}s"
        )
        return all_jobs

    def _add_unique(self, df, source: str, seen_urls: set, all_jobs: list) -> tuple[int, int]:
        new, skipped = 0, 0
        for posting in self._dataframe_to_postings(df, source):
            if posting.url and posting.url in seen_urls:
                skipped += 1
            else:
                if posting.url:
                    seen_urls.add(posting.url)
                all_jobs.append(posting)
                new += 1
        return new, skipped

    def _dataframe_to_postings(self, df, source: str) -> list[JobPosting]:
        postings = []
        for _, row in df.iterrows():
            # Salary — guard against NaN
            s_min = row.get("min_amount")
            s_max = row.get("max_amount")
            currency = row.get("currency") or ""
            s_min = None if (s_min is None or (isinstance(s_min, float) and math.isnan(s_min))) else int(s_min)
            s_max = None if (s_max is None or (isinstance(s_max, float) and math.isnan(s_max))) else int(s_max)
            salary = f"{currency} {s_min or 0:,}–{s_max or 0:,}".strip() if (s_min or s_max) else None

            # Description — strip markdown/HTML
            description = row.get("description") or ""
            if description:
                description = re.sub(r"<[^>]+>", " ", description)
                description = re.sub(r"[#*`>\[\]]+", " ", description)
                description = re.sub(r"\s+", " ", description).strip()

            # Date — JobSpy returns datetime.date directly or float NaN when unavailable.
            # LinkedIn omits dates on promoted/featured listings; NaN is the honest signal.
            posted_date = row.get("date_posted")
            if posted_date is None or isinstance(posted_date, float):
                # float covers pandas NaN (which is truthy, so must be caught explicitly)
                posted_date = None
            elif not isinstance(posted_date, date):
                try:
                    posted_date = datetime.strptime(str(posted_date), "%Y-%m-%d").date()
                except ValueError:
                    posted_date = None

            raw_loc = str(row.get("location") or "").strip()
            wfh = str(row.get("work_from_home_type") or "").lower()
            is_remote = row.get("is_remote")

            if "hybrid" in wfh:
                work_mode = "hybrid"
                location = f"{raw_loc} (Hybrid)" if raw_loc else "Hybrid"
            elif is_remote or not raw_loc or "remote" in raw_loc.lower():
                work_mode = "remote"
                location = "Remote" if not raw_loc else raw_loc
            else:
                work_mode = "on-site"
                location = raw_loc

            # base_location: the city/state from the raw LinkedIn/Indeed location field
            base_location = raw_loc if raw_loc and raw_loc.lower() not in ("remote", "") else None

            postings.append(JobPosting(
                source=source,
                title=str(row.get("title") or ""),
                company=str(row.get("company") or ""),
                location=location,
                url=str(row.get("job_url") or ""),
                posted_date=posted_date if isinstance(posted_date, date) else None,
                description=description or None,
                tags=[],
                salary=salary,
                work_mode=work_mode,
                base_location=base_location,
            ))
        return postings
