import importlib
import os
import pkgutil
import re
import time

from dotenv import load_dotenv
load_dotenv()

from filters import JobFilterEngine
from models import JobFilter, JobPosting
from paths import DB_PATH
from storage import JobStorage


def discover_scrapers():
    scrapers_dir = os.path.join(os.path.dirname(__file__), "scrapers")
    scraper_classes = []

    # Scan sub-packages: boards/, ats/, company_sites/ (and root-level for legacy)
    for subpkg in ("boards", "ats", "company_sites"):
        pkg_path = os.path.join(scrapers_dir, subpkg)
        if not os.path.isdir(pkg_path):
            continue
        for _, module_name, _ in pkgutil.iter_modules([pkg_path]):
            if module_name in ("base", "__init__"):
                continue
            module = importlib.import_module(f"scrapers.{subpkg}.{module_name}")
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

    # Also scan root-level scrapers/ for non-migrated modules (greenhouse, disabled scrapers)
    for _, module_name, _ in pkgutil.iter_modules([scrapers_dir]):
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
                if obj not in scraper_classes:
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
    import argparse
    parser = argparse.ArgumentParser(description="Job scraping pipeline")
    parser.add_argument("--monitored-only", action="store_true",
                        help="Only scrape monitored companies (company-keyed, no broad boards)")
    args = parser.parse_args()

    t0 = time.monotonic()
    db = JobStorage(DB_PATH)

    from profiles import load_active_profile
    profile = load_active_profile(db)

    if args.monitored_only:
        _run_monitored_only(db, profile)
    else:
        _run_broad_scrape(db, profile)

    elapsed = round(time.monotonic() - t0, 1)
    print(f"\nScrape complete ({elapsed:.0f}s)")


def _run_monitored_only(db: JobStorage, profile) -> None:
    """Company-keyed monitoring run — only monitored companies, no broad boards."""
    import importlib
    t_start = time.monotonic()

    monitored = db.get_monitored_companies()
    if not monitored:
        print("Nothing to monitor — no companies with monitored=true.")
        db.log_run(
            profile_id=profile.id,
            jobs_scraped=0, jobs_scored=0, jobs_above_threshold=0,
            status="scraped", duration_seconds=0, run_type="monitored_only",
        )
        return

    # Group companies by ats_provider
    by_provider: dict[str, list[dict]] = {}
    for company in monitored:
        provider = company.get("ats_provider")
        if provider:
            by_provider.setdefault(provider, []).append(company)

    print(f"Monitored-only run: {len(monitored)} companies across "
          f"{len(by_provider)} providers ({', '.join(sorted(by_provider))})")

    seen_urls: set[str] = set()
    total_fetched = 0
    total_new = 0

    for provider, companies in sorted(by_provider.items()):
        print(f"\n[{provider}] {len(companies)} companies")
        try:
            module = importlib.import_module(f"scrapers.ats.{provider}")
        except (ImportError, ModuleNotFoundError):
            # Fall back to root-level scraper
            try:
                module = importlib.import_module(f"scrapers.{provider}")
            except (ImportError, ModuleNotFoundError):
                print(f"  ⚠️ No adapter found for provider '{provider}' — skipped")
                continue

        ScraperClass = None
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (isinstance(obj, type) and hasattr(obj, "fetch")
                    and hasattr(obj, "SOURCE_NAME")
                    and obj.__name__ != "BaseScraper"):
                ScraperClass = obj
                break

        if ScraperClass is None:
            print(f"  ⚠️ No scraper class found in {module} — skipped")
            continue

        scraper = ScraperClass(storage=db, targets=companies)
        print(f"  Fetching from {scraper.SOURCE_NAME}...")

        # Fetch all openings (no filtering — pure function per spec)
        try:
            raw = scraper.fetch(None)  # No JobFilter in monitoring mode
        except Exception as e:
            print(f"  ❌ [{scraper.SOURCE_NAME}] fetch failed: {e}")
            continue

        total_fetched += len(raw)
        print(f"  → {len(raw)} jobs fetched")

        # URL dedup + DB dedup
        unique_batch = []
        for job in raw:
            if job.url and job.url in seen_urls:
                continue
            unique_batch.append(job)
            if job.url:
                seen_urls.add(job.url)

        if not unique_batch:
            print(f"  → 0 new (all URL-duplicates)")
            continue

        unique_batch = dedupe_against_db(unique_batch, db)

        # Write to DB with monitored_company_id
        before_count = db.get_stats(profile.id)["total"]

        # Build lookup: company name → company id for monitored_company_id
        company_name_to_id: dict[str, int] = {}
        for c in companies:
            company_name_to_id[c["name"].lower()] = c["id"]

        for job in unique_batch:
            company_id = None
            if job.company and job.company.strip():
                try:
                    company_id = db.upsert_company(job.company.strip())
                except ValueError:
                    pass
            # Find monitored_company_id by matching company name
            mon_id = company_name_to_id.get(
                (job.company or "").strip().lower()
            ) or company_id
            db.save_unscored(job, company_id=company_id,
                            monitored_company_id=mon_id)

        after_count = db.get_stats(profile.id)["total"]
        batch_new = after_count - before_count
        total_new += batch_new
        print(f"  → {batch_new} new saved to DB")

        # Polite delay between providers
        time.sleep(1.0)

    already_count = total_fetched - total_new
    print(f"\nMonitored-only run: {total_fetched} fetched, {total_new} new, "
          f"{already_count} already in DB")
    db.log_run(
        profile_id=profile.id,
        jobs_scraped=total_fetched, jobs_scored=0, jobs_above_threshold=0,
        status="scraped",
        duration_seconds=round(time.monotonic() - t_start, 1),
        run_type="monitored_only",
    )


def _run_broad_scrape(db: JobStorage, profile) -> None:
    """Broad scrape: all enabled scrapers (boards + discovery), no monitoring filter."""
    t_start = time.monotonic()
    job_filter = JobFilter(
        titles=profile.scrape_titles,
        exclude=profile.scrape_exclude,
        remote_or_hybrid=profile.scrape_remote_or_hybrid,
    )

    scraper_classes = discover_scrapers()
    print(f"Scrapers found: {[s.SOURCE_NAME for s in scraper_classes]}")

    seen_urls: set[str] = set()
    total_fetched = 0
    total_new = 0
    total_excluded_date = 0

    for ScraperClass in scraper_classes:
        scraper = ScraperClass(storage=db)
        if not scraper.is_enabled():
            print(f"  ⚠️ [{scraper.SOURCE_NAME}] disabled — skipped")
            continue
        print(f"Fetching from {scraper.SOURCE_NAME}...")
        raw = scraper.fetch(job_filter)
        print(f"  → {len(raw)} jobs fetched")
        filtered, excl_date, _ = JobFilterEngine.apply(raw, job_filter)
        total_excluded_date += excl_date
        total_fetched += len(filtered)
        print(f"  → {len(filtered)} after filter")

        unique_batch = []
        for job in filtered:
            if job.url and job.url in seen_urls:
                continue
            unique_batch.append(job)
            if job.url:
                seen_urls.add(job.url)

        if not unique_batch:
            print(f"  → 0 new (all URL-duplicates of previous scrapers)")
            continue

        unique_batch = dedupe_against_db(unique_batch, db)

        before_count = db.get_stats(profile.id)["total"]
        for job in unique_batch:
            company_id = None
            if job.company and job.company.strip():
                try:
                    company_id = db.upsert_company(job.company.strip())
                except ValueError:
                    pass
            db.save_unscored(job, company_id=company_id)

        after_count = db.get_stats(profile.id)["total"]
        batch_new = after_count - before_count
        total_new += batch_new
        print(f"  → {batch_new} new saved to DB, {len(unique_batch) - batch_new} already in DB")

    already_count = total_fetched - total_new
    print(f"\nScrape complete: {total_fetched} fetched, {total_new} new, {already_count} already in DB")
    if total_excluded_date:
        print(f"📅 {total_excluded_date} jobs excluded (posted > 30 days ago)")

    db.log_run(
        profile_id=profile.id,
        jobs_scraped=total_fetched,
        jobs_scored=0,
        jobs_above_threshold=0,
        status="scraped",
        duration_seconds=round(time.monotonic() - t_start, 1),
    )


if __name__ == "__main__":
    main()
