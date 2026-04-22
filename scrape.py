import importlib
import os
import pkgutil

from dotenv import load_dotenv
load_dotenv()

from filters import JobFilterEngine
from models import JobFilter, JobPosting
from profiles import DEFAULT_PROFILE_ID
from storage import JobStorage

DB_PATH = "data/jobs.db"


def discover_scrapers():
    package_path = os.path.join(os.path.dirname(__file__), "scrapers")
    scraper_classes = []
    for _, module_name, _ in pkgutil.iter_modules([package_path]):
        if module_name in ("base", "__init__"):
            continue
        module = importlib.import_module(f"scrapers.{module_name}")
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and hasattr(obj, "fetch")
                and hasattr(obj, "SOURCE_NAME")
                and obj.__name__ != "BaseScraper"
                and getattr(obj, "ENABLED", True)
            ):
                scraper_classes.append(obj)
    return scraper_classes


def deduplicate(jobs: list[JobPosting]) -> list[JobPosting]:
    seen = set()
    unique = []
    for job in jobs:
        if job.url not in seen:
            seen.add(job.url)
            unique.append(job)
    return unique


def main():
    # Universal PM/PO filter — no profile dependency.
    # remote_or_hybrid=False so on-site CH jobs are kept; web3_remote's
    # work_mode gate runs post-scoring in score.py via allowed_work_modes.
    job_filter = JobFilter(
        titles=[
            "product manager", "head of product", "cpo", "vp product",
            "product owner", "product lead", "product engineer", "project manager",
        ],
        exclude=["junior", "intern", "stage", "apprentice"],
        remote_or_hybrid=False,
    )

    scraper_classes = discover_scrapers()
    print(f"Scrapers found: {[s.SOURCE_NAME for s in scraper_classes]}")

    all_jobs: list[JobPosting] = []
    total_excluded_date = 0

    for ScraperClass in scraper_classes:
        scraper = ScraperClass()
        print(f"Fetching from {scraper.SOURCE_NAME}...")
        raw = scraper.fetch(job_filter)
        print(f"  → {len(raw)} jobs fetched")
        filtered, excl_date, _ = JobFilterEngine.apply(raw, job_filter)
        total_excluded_date += excl_date
        print(f"  → {len(filtered)} after filter")
        all_jobs.extend(filtered)

    all_jobs = deduplicate(all_jobs)
    print(f"\nTotal unique jobs after dedup: {len(all_jobs)}")
    if total_excluded_date:
        print(f"📅 {total_excluded_date} jobs excluded (posted > 30 days ago)")

    db = JobStorage(DB_PATH)
    total_before = db.get_stats(DEFAULT_PROFILE_ID)["total"]

    for job in all_jobs:
        db.save_unscored(job)

    total_after = db.get_stats(DEFAULT_PROFILE_ID)["total"]
    new_count = total_after - total_before
    already_count = len(all_jobs) - new_count

    print(f"\nScrape complete: {len(all_jobs)} fetched, {new_count} new, {already_count} already in DB")


if __name__ == "__main__":
    main()
