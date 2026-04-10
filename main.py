import argparse
import importlib
import json
import os
import pkgutil
import time
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from filters import JobFilterEngine
from models import JobFilter, JobPosting
from profiles import ALL_PROFILES, DEFAULT_PROFILE_ID
from scorer import score_job
from storage import JobStorage


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


def build_job_filter(profile) -> JobFilter:
    """Construit un JobFilter adapté au profil de recherche."""
    return JobFilter(
        keywords=["product manager", "PM"] + profile.boost_keywords,
        titles=[
            "product manager", "head of product", "CPO", "VP product",
            "product owner", "product engineer", "project manager",
        ],
        exclude=["junior", "intern", "stage", "apprentice"],
        remote_only=False,
        remote_or_hybrid=profile.remote_or_hybrid,
        locations=profile.location_keywords,
        allowed_geo_zones=profile.allowed_geo_zones,
    )


def main():
    # ── CLI ───────────────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="job_agent — automated PM job search")
    parser.add_argument(
        "--profile",
        default=None,
        choices=list(ALL_PROFILES.keys()),
        help=f"Search profile to run (default: active profile from DB or {DEFAULT_PROFILE_ID})",
    )
    args = parser.parse_args()

    _db = JobStorage("data/jobs.db")
    _active = args.profile or _db.get_config("active_profile_id", default=DEFAULT_PROFILE_ID)
    profile = ALL_PROFILES.get(_active, ALL_PROFILES[DEFAULT_PROFILE_ID])
    print(f"🔍 Profile: {profile.name} ({profile.id})")

    # ── Filter ────────────────────────────────────────────────────────────────
    job_filter = build_job_filter(profile)

    # ── Scraping ──────────────────────────────────────────────────────────────
    scraper_classes = discover_scrapers()
    print(f"Scrapers found: {[s.SOURCE_NAME for s in scraper_classes]}")

    all_jobs: list[JobPosting] = []
    total_excluded_date = 0
    total_excluded_geo = 0

    for ScraperClass in scraper_classes:
        scraper = ScraperClass()
        print(f"Fetching from {scraper.SOURCE_NAME}...")
        raw = scraper.fetch(job_filter)
        print(f"  → {len(raw)} jobs fetched")
        filtered, excl_date, excl_geo = JobFilterEngine.apply(raw, job_filter)
        total_excluded_date += excl_date
        total_excluded_geo += excl_geo
        print(f"  → {len(filtered)} after filter")
        all_jobs.extend(filtered)

    all_jobs = deduplicate(all_jobs)
    print(f"\nTotal unique jobs after dedup: {len(all_jobs)}")
    print(f"📅 {total_excluded_date} jobs exclus (postés il y a > 30 jours)")
    if total_excluded_geo:
        print(f"🌍 {total_excluded_geo} jobs exclus (geo pre-scoring)")

    # ── Storage + cache ───────────────────────────────────────────────────────
    db = JobStorage("data/jobs.db")
    db.upsert_profile(profile)

    new_jobs, cached_jobs = db.split_new_cached(all_jobs, profile.id)

    # Cached jobs: apply post-scoring filters (geo + work_mode + threshold)
    scored_jobs = []
    for job_dict in cached_jobs:
        geo_zone  = job_dict.get("geo_zone", "unknown")
        work_mode = job_dict.get("work_mode", "unknown")

        if profile.allowed_geo_zones and geo_zone and geo_zone not in profile.allowed_geo_zones:
            total_excluded_geo += 1
            continue
        if profile.allowed_work_modes and work_mode and work_mode not in profile.allowed_work_modes:
            continue
        if job_dict.get("score", 0) >= profile.score_threshold:
            scored_jobs.append(job_dict)

    # ── Scoring ───────────────────────────────────────────────────────────────
    if new_jobs:
        print(f"\n⏱  Scoring {len(new_jobs)} nouveaux jobs "
              f"(~{len(new_jobs) * 2.5 / 60:.1f} min avec délai anti-rate-limit)")

        for i, job in enumerate(new_jobs, 1):
            print(f"  Scoring {i}/{len(new_jobs)} — {job.title[:50]}")
            job_dict_for_scorer = {
                "title":         job.title,
                "company":       job.company,
                "location":      job.location,
                "base_location": job.base_location or "",
                "description":   job.description or "",
            }
            result = score_job(job_dict_for_scorer)

            if result is None:
                # Scoring échoué (Groq + Gemini) — tracé sans score, retenté au prochain run
                db.save_unscored(job)
                continue

            # Hydrate JobPosting with scorer output
            job.summary       = result["summary"]
            job.work_mode     = result["work_mode"]
            job.company_size  = result["company_size"]
            job.contract_type = result["contract_type"]
            job.geo_zone      = result["geo_zone"]

            db.save_scored(job, result, profile.id)

            geo_zone  = result["geo_zone"]
            work_mode = result["work_mode"]

            if profile.allowed_geo_zones and geo_zone and geo_zone not in profile.allowed_geo_zones:
                total_excluded_geo += 1
            elif profile.allowed_work_modes and work_mode and work_mode not in profile.allowed_work_modes:
                pass  # excluded by work_mode filter
            elif result["score"] >= profile.score_threshold:
                scored_jobs.append({**job.to_json(), **result})

            if i < len(new_jobs):
                time.sleep(4)

        db.touch_many([j["id"] for j in cached_jobs])
    else:
        print("\n✅ Tous les jobs sont déjà scorés en cache — aucun appel LLM nécessaire")
        db.touch_many([j["id"] for j in cached_jobs])

    # ── Stats ─────────────────────────────────────────────────────────────────
    scored_jobs.sort(key=lambda x: x["score"], reverse=True)
    hot = [j for j in scored_jobs if j["score"] >= 8]
    mid = [j for j in scored_jobs if 5 <= j["score"] <= 7]

    stats = db.get_stats(profile.id)
    print(f"\n--- Statistiques [{profile.name}] ---")
    print(f"📅 {total_excluded_date} jobs exclus (> 30 jours)")
    print(f"🌍 {total_excluded_geo} jobs exclus (geo/work_mode)")
    print(f"✅ {len(scored_jobs)} jobs dans le digest  (🔥 {len(hot)} hot  ⭐ {len(mid)} solid)")
    print(f"📊 DB : {stats['total']} jobs total · {stats['hot']} 🔥 hot · {stats['solid']} ⭐ solid")

    # ── Output ────────────────────────────────────────────────────────────────
    today = date.today().isoformat()
    output_dir = os.path.join(os.path.dirname(__file__), "outputs")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"jobs_{profile.id}_{today}.json")

    with open(output_path, "w") as f:
        json.dump(scored_jobs, f, indent=2, ensure_ascii=False)
    print(f"Saved to {output_path}")

    # ── Notify ────────────────────────────────────────────────────────────────
    from notifier import send_email_digest, export_joplin
    send_email_digest(scored_jobs)
    export_joplin(scored_jobs)

    # ── Top 5 ────────────────────────────────────────────────────────────────
    def emoji(score: int) -> str:
        return "🔥" if score >= 8 else "⭐"

    print("\n--- Top 5 Jobs ---")
    for i, job in enumerate(scored_jobs[:5], 1):
        score = job["score"]
        scored_by = job.get("scored_by", "?")
        print(f"\n{emoji(score)} #{i} [{score}/10] {job['title']} @ {job['company']}  [{scored_by}]")
        print(f"  Source   : {job.get('source', '')}")
        print(f"  Location : {job.get('location', '')}")
        print(f"  URL      : {job.get('url', '')}")
        print(f"  Reason   : {job.get('reason', '')}")

    borderline = [j for j in scored_jobs if 5 <= j["score"] <= 7][:3]
    if borderline:
        print("\n--- ⭐ Score 5-7 ---")
        for job in borderline:
            print(f"\n  [{job['score']}/10] {job['title']} @ {job['company']} ({job.get('source', '')})")
            print(f"  Reason : {job.get('reason', '')}")


if __name__ == "__main__":
    main()
