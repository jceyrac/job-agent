import importlib
import json
import os
import pkgutil
import sys
from datetime import date, datetime

from dotenv import load_dotenv

load_dotenv()

from filters import JobFilterEngine
from models import JobFilter, JobPosting
from scorer import score_job

SCORE_THRESHOLD = 5

def discover_scrapers():
    import scrapers
    scraper_classes = []
    package_path = os.path.join(os.path.dirname(__file__), "scrapers")

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
    job_filter = JobFilter(
        keywords=["product manager", "PM", "web3", "defi",
                  "blockchain", "fintech", "AI", "crypto"],
        titles=["product manager", "head of product", "CPO", "VP product", "product owner", "product engineer", "project manager"],
        exclude=["junior", "intern", "stage", "apprentice"],
        remote_only=False,
        remote_or_hybrid=True,
    )

    scraper_classes = discover_scrapers()
    print(f"Scrapers found: {[s.SOURCE_NAME for s in scraper_classes]}")

    all_jobs: list[JobPosting] = []
    for ScraperClass in scraper_classes:
        scraper = ScraperClass()
        print(f"Fetching from {scraper.SOURCE_NAME}...")
        raw = scraper.fetch(job_filter)
        print(f"  → {len(raw)} jobs fetched")
        filtered = JobFilterEngine.apply(raw, job_filter)
        print(f"  → {len(filtered)} after filter")
        all_jobs.extend(filtered)

    all_jobs = deduplicate(all_jobs)
    print(f"\nTotal unique jobs after dedup: {len(all_jobs)}")

    # Score each job
    scored = []
    for job in all_jobs:
        try:
            score, reason, summary, work_mode, company_size, contract_type = score_job({
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "description": job.description or "",
            })
        except Exception as e:
            print(f"  [scorer] Error on '{job.title}': {e}")
            score, reason, summary, work_mode, company_size, contract_type = 0, "scoring error", "", "unknown", "unknown", "unknown"

        job.summary = summary
        job.work_mode = work_mode
        job.company_size = company_size
        job.contract_type = contract_type
        if score >= SCORE_THRESHOLD:
            scored.append((score, reason, job))

    scored.sort(key=lambda x: x[0], reverse=True)
    hot = [s for s in scored if s[0] >= 8]
    mid = [s for s in scored if 5 <= s[0] <= 7]
    print(f"Jobs with score >= {SCORE_THRESHOLD}: {len(scored)}  (🔥 {len(hot)} hot  ⭐ {len(mid)} solid)")

    # Save output
    today = date.today().isoformat()
    output_dir = os.path.join(os.path.dirname(__file__), "outputs")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"jobs_{today}.json")

    output_data = [
        {**job.to_json(), "score": score, "reason": reason}
        for score, reason, job in scored
    ]
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)
    print(f"Saved to {output_path}")

    from notifier import send_email_digest, export_joplin
    send_email_digest(output_data)
    export_joplin(output_data)

    def emoji(score: int) -> str:
        return "🔥" if score >= 8 else "⭐"

    # Print top 5
    print("\n--- Top 5 Jobs ---")
    for i, (score, reason, job) in enumerate(scored[:5], 1):
        print(f"\n{emoji(score)} #{i} [{score}/10] {job.title} @ {job.company}")
        print(f"  Source   : {job.source}")
        print(f"  Location : {job.location}")
        print(f"  URL      : {job.url}")
        print(f"  Reason   : {reason}")

    # Show sample 5-7 jobs that would have been filtered before
    borderline = [s for s in scored if 5 <= s[0] <= 7][:3]
    if borderline:
        print("\n--- ⭐ Score 5-7 (would have been filtered before) ---")
        for score, reason, job in borderline:
            print(f"\n  [{score}/10] {job.title} @ {job.company} ({job.source})")
            print(f"  Reason : {reason}")


if __name__ == "__main__":
    main()
