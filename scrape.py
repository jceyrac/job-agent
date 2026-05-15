import importlib
import os
import pkgutil
import re

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


def _normalize_key(title: str, company: str) -> str:
    if not title or not company:
        return ""
    # Strip parenthetical qualifiers: " (m/w/d)", " (Remote)", " (f/m/x)", etc.
    title = re.sub(r"\s*\([^)]*\)", "", title)
    company = re.sub(r"\s*\([^)]*\)", "", company)
    title = title.lower().strip()
    company = company.lower().strip()
    # Normalize common abbreviations so "Senior PM" ≈ "Senior Product Manager"
    title = re.sub(r"\bproduct manager\b", "pm", title)
    title = re.sub(r"\bproduct owner\b", "po", title)
    title = re.sub(r"\bsenior\b", "sr", title)
    title = re.sub(r"\bhead of\b", "head", title)
    title = re.sub(r"\bmanager\b", "mgr", title)
    combined = f"{title} {company}"
    cleaned = re.sub(r"[^a-z0-9 ]", "", combined)
    return re.sub(r"\s+", " ", cleaned).strip()


def dedupe_cross_source(jobs: list[JobPosting]) -> list[JobPosting]:
    groups: dict[str, list[JobPosting]] = {}
    no_key: list[JobPosting] = []
    for job in jobs:
        key = _normalize_key(job.title, job.company)
        if not key:
            no_key.append(job)
        else:
            groups.setdefault(key, []).append(job)

    deduped: list[JobPosting] = list(no_key)
    for group in groups.values():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            def _sort_key(j: JobPosting):
                desc_len = len(j.description or "")
                # None posted_date loses to any real date
                date_val = j.posted_date.toordinal() if j.posted_date else 0
                return (desc_len, date_val)
            deduped.append(max(group, key=_sort_key))

    return deduped


def dedupe_against_db(jobs: list[JobPosting], storage: JobStorage) -> list[JobPosting]:
    """Drop scraped jobs whose (normalize_title, normalize_company) matches
    an existing row with status in {applied, ready, queued, archived}.
    Keeps the existing row, updates its last_seen.
    Does NOT dedup against status='new' — those are still in the queue.
    """
    engaged = storage.get_engaged_job_keys()
    if not engaged:
        return jobs

    # Build in-memory set of (title, company, id) keys from engaged jobs
    engaged_keys: dict[str, str] = {}  # normalized_key → job_id
    for row in engaged:
        key = _normalize_key(row["title"], row["company"])
        if key:
            engaged_keys[key] = row["id"]

    if not engaged_keys:
        return jobs

    touched_ids: set[str] = set()
    result: list[JobPosting] = []
    dropped = 0

    for job in jobs:
        key = _normalize_key(job.title, job.company)
        if key and key in engaged_keys:
            dropped += 1
            touched_ids.add(engaged_keys[key])
        else:
            result.append(job)

    if touched_ids:
        storage.touch_many(list(touched_ids))

    if dropped:
        print(f"DB dedup: {dropped} job(s) dropped (already engaged) — "
              f"{len(touched_ids)} existing row(s) last_seen updated")

    return result


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
    before_cross = len(all_jobs)
    all_jobs = dedupe_cross_source(all_jobs)
    print(f"Cross-source dedup: {before_cross} → {len(all_jobs)} unique")

    db = JobStorage(DB_PATH)
    before_db_dedup = len(all_jobs)
    all_jobs = dedupe_against_db(all_jobs, db)

    print(f"\nTotal unique jobs after dedup: {len(all_jobs)}")
    if total_excluded_date:
        print(f"📅 {total_excluded_date} jobs excluded (posted > 30 days ago)")
    total_before = db.get_stats(DEFAULT_PROFILE_ID)["total"]

    for job in all_jobs:
        company_id = None
        if job.company and job.company.strip():
            try:
                company_id = db.upsert_company(job.company.strip())
            except ValueError:
                pass  # name normalizes to empty — skip company link
        db.save_unscored(job, company_id=company_id)

    total_after = db.get_stats(DEFAULT_PROFILE_ID)["total"]
    new_count = total_after - total_before
    already_count = len(all_jobs) - new_count

    print(f"\nScrape complete: {len(all_jobs)} fetched, {new_count} new, {already_count} already in DB")


if __name__ == "__main__":
    main()
