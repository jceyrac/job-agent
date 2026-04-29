import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime

from dotenv import load_dotenv
load_dotenv()

from models import JobPosting
from notifier import send_email_digest, export_joplin
from profiles import ALL_PROFILES
from scorer import evaluate_for_profile, extract_job_fields, score_job
from storage import JobStorage

DB_PATH = "data/jobs.db"

MOCK_JOBS = [
    {
        "title": "Senior Tech Product Owner",
        "company": "FELFEL AG",
        "location": "Zürich, ZH, CH",
        "base_location": "Zürich, Switzerland",
        "description": (
            "FELFEL is a Swiss food-tech company providing automated fresh food stations "
            "to corporate offices. We are seeking a Senior Tech Product Owner to own the "
            "platform roadmap end-to-end, collaborating with engineering and operations."
        ),
    },
    {
        "title": "Senior Product Manager - MetaMask",
        "company": "Consensys",
        "location": "Remote",
        "base_location": "Worldwide",
        "description": (
            "Consensys is the leading blockchain and web3 software company. MetaMask is "
            "the world's most used crypto wallet with 30M+ users. We are seeking a Senior "
            "PM to drive engagement and growth of the MetaMask web3 ecosystem platform."
        ),
    },
    {
        "title": "Head of Product",
        "company": "SIX Group",
        "location": "Zürich, Switzerland (Hybrid)",
        "base_location": "Zürich, Switzerland",
        "description": (
            "SIX Group operates Switzerland's financial infrastructure including the stock "
            "exchange and payment network. Seeking Head of Product to lead digital "
            "transformation of payment services, working with engineering and business "
            "stakeholders across the DACH region."
        ),
    },
]


def _delete_scores_for_profile(db_path: str, profile_id: str) -> int:
    """Delete all existing scores for this profile. Returns count deleted."""
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM job_scores WHERE profile_id = ?", (profile_id,))
    count = conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    conn.close()
    return count


def _run_mock(profile) -> None:
    """Score 3 hardcoded test jobs with the profile's scoring_context. No DB writes."""
    print(f"\n=== MOCK TEST: {profile.name} ({profile.id}) ===")
    print("Scoring 3 sample jobs (no DB writes, no rate-limit delay)...\n")
    for job in MOCK_JOBS:
        result = score_job(job, scoring_context=profile.scoring_context)
        if result:
            emoji = "🔥" if result["score"] >= 8 else ("⭐" if result["score"] >= 5 else "👀")
            print(f"  {emoji} [{result['score']}/10] {job['title']} @ {job['company']}")
            print(f"    Mode: {result['work_mode']} | Geo: {result['geo_zone']} | Size: {result['company_size']}")
            print(f"    Reason: {result['reason']}")
            print(f"    Scored by: {result['scored_by']}")
        else:
            print(f"  [ERROR] {job['title']} @ {job['company']} — scorer failed")
        print()


def _run_extraction(limit: int | None = None) -> None:
    """Field extraction pass — profile-independent. Fills extracted_at + structured fields."""
    db = JobStorage(DB_PATH)
    jobs_to_extract = db.get_jobs_for_extraction()
    print(f"Extraction: {len(jobs_to_extract)} jobs need extraction")

    if limit and len(jobs_to_extract) > limit:
        print(f"  --limit {limit}: processing {limit}/{len(jobs_to_extract)} "
              f"({len(jobs_to_extract) - limit} deferred to next run)")
        jobs_to_extract = jobs_to_extract[:limit]

    if not jobs_to_extract:
        print("All jobs already extracted — nothing to do.")
        return

    extracted_count = 0
    error_count = 0
    for i, job_dict in enumerate(jobs_to_extract, 1):
        title = job_dict.get("title", "")
        company = job_dict.get("company", "")
        print(f"  Extracting {i}/{len(jobs_to_extract)}: {title[:50]} @ {company[:30]}")

        job = JobPosting(
            source=job_dict.get("source") or "",
            title=title,
            company=company,
            location=job_dict.get("location") or "",
            url=job_dict.get("url") or "",
            posted_date=None,
            description=job_dict.get("description") or "",
            base_location=job_dict.get("base_location") or "",
        )

        result = extract_job_fields(job)
        if result is None:
            error_count += 1
            continue

        db.update_job_extraction(job.id, {
            "company_country":   result.company_country or "unknown",
            "industry_sector":   result.industry_sector or "other",
            "language_required": result.language_required or "unknown",
            "work_mode":         result.work_mode or "unknown",
            "geo_zone":          result.geo_zone or "unknown",
            "company_size":      result.company_size or "unknown",
            "contract_type":     result.contract_type or "unknown",
            "summary":           result.summary or "",
            "extracted_by":      result.extracted_by,
        })
        extracted_count += 1

        if i < len(jobs_to_extract):
            time.sleep(4)

    print(f"\nExtraction complete: {extracted_count} extracted, {error_count} errors")


def _get_jobs_to_score(db_path: str, profile_id: str, rescore: bool) -> list[dict]:
    """Read jobs from DB that need scoring for this profile.
    Read-only query — writes go through storage.py as usual."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if rescore:
        rows = conn.execute("""
            SELECT j.* FROM jobs j
            LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
            WHERE s.status IS NULL OR s.status != 'rejected'
        """, (profile_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT j.* FROM jobs j
            LEFT JOIN job_scores s ON j.id = s.job_id AND s.profile_id = ?
            WHERE (s.job_id IS NULL OR s.score IS NULL)
              AND (s.status IS NULL OR s.status != 'rejected')
        """, (profile_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _dict_to_posting(d: dict) -> JobPosting:
    """Reconstruct a JobPosting from a DB row dict (for storage write calls).
    Includes Phase 1e extraction fields if present."""
    posted_date = None
    raw_date = d.get("posted_date")
    if raw_date:
        try:
            posted_date = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass
    extracted_at = d.get("extracted_at")
    if extracted_at:
        try:
            extracted_at = datetime.strptime(str(extracted_at)[:19], "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            extracted_at = None
    return JobPosting(
        source=d.get("source") or "",
        title=d.get("title") or "",
        company=d.get("company") or "",
        location=d.get("location") or "",
        url=d.get("url") or "",
        posted_date=posted_date,
        description=d.get("description"),
        tags=[],
        salary=None,
        work_mode=d.get("work_mode"),
        base_location=d.get("base_location"),
        summary=d.get("summary"),
        company_size=d.get("company_size"),
        contract_type=d.get("contract_type"),
        geo_zone=d.get("geo_zone"),
        company_country=d.get("company_country"),
        industry_sector=d.get("industry_sector"),
        language_required=d.get("language_required"),
        extracted_at=extracted_at,
        extracted_by=d.get("extracted_by"),
    )


def main():
    parser = argparse.ArgumentParser(description="score.py — score jobs for a profile / extract job fields")
    parser.add_argument("--profile", default=None, help="Profile ID to score for")
    parser.add_argument("--extract", action="store_true",
                        help="Run profile-independent field extraction (no evaluation)")
    parser.add_argument("--rescore", action="store_true",
                        help="Delete existing scores for this profile and re-score all jobs")
    parser.add_argument("--mock", action="store_true",
                        help="Score 3 hardcoded test jobs to verify profile context; no DB writes")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Cap the number of jobs processed this run")
    args = parser.parse_args()

    if not args.extract and not args.profile:
        print("Specify --profile or --extract.")
        sys.exit(1)

    if args.extract and args.profile:
        print("--extract and --profile are mutually exclusive. Run them separately.")
        sys.exit(1)

    if args.mock and args.rescore:
        print("--mock and --rescore are mutually exclusive.")
        sys.exit(1)

    if args.mock:
        if not args.profile:
            print("--mock requires --profile.")
            sys.exit(1)
        if args.profile not in ALL_PROFILES:
            print(f"Unknown profile '{args.profile}'. Valid: {list(ALL_PROFILES.keys())}")
            sys.exit(1)
        _run_mock(ALL_PROFILES[args.profile])
        return

    if not os.path.exists(DB_PATH):
        print("DB not found — run scrape.py first.")
        sys.exit(1)

    if args.extract:
        _run_extraction(args.limit)
        return

    if args.profile not in ALL_PROFILES:
        print(f"Unknown profile '{args.profile}'. Valid: {list(ALL_PROFILES.keys())}")
        sys.exit(1)

    profile = ALL_PROFILES[args.profile]
    db = JobStorage(DB_PATH)
    db.upsert_profile(profile)
    print(f"Profile: {profile.name} ({profile.id})")

    if args.rescore:
        deleted = _delete_scores_for_profile(DB_PATH, profile.id)
        print(f"--rescore: cleared {deleted} existing scores for '{profile.id}'")

    total_in_db = db.get_stats(profile.id)["total"]
    # Merge location_keywords into pre_filter.location_contains (additive, no dupes)
    effective_pre_filter = dict(profile.pre_filter) if profile.pre_filter else {}
    if profile.location_keywords:
        existing = effective_pre_filter.get("location_contains", [])
        merged = list(dict.fromkeys(profile.location_keywords + existing))  # preserve order, dedupe
        effective_pre_filter["location_contains"] = merged
    jobs_to_score = db.get_jobs_for_scoring(
        profile_id=profile.id,
        pre_filter=effective_pre_filter or None,
        rescore=args.rescore,
    )
    skipped = total_in_db - len(jobs_to_score)
    print(f"Pre-filter applied: {total_in_db} jobs → {len(jobs_to_score)} after SQL filter "
          f"({skipped} skipped, including jobs older than 30 days)")

    if args.limit and len(jobs_to_score) > args.limit:
        print(f"--limit {args.limit}: capping run at {args.limit}/{len(jobs_to_score)} jobs "
              f"({len(jobs_to_score) - args.limit} deferred to next run)")
        jobs_to_score = jobs_to_score[:args.limit]

    # ── Phase 1: extraction of unextracted survivors ────────────────────────────
    extracted_count = 0
    error_count = 0
    unextracted = [j for j in jobs_to_score if not j.get("extracted_at")]
    if unextracted:
        print(f"Extraction: {len(unextracted)} of {len(jobs_to_score)} need extraction...")
        for i, job_dict in enumerate(unextracted, 1):
            title   = job_dict.get("title", "")
            company = job_dict.get("company", "")
            print(f"  Extracting {i}/{len(unextracted)}: {title[:50]} @ {company[:30]}")
            job_obj = _dict_to_posting(job_dict)
            result = extract_job_fields(job_obj)
            if result is None:
                db.save_unscored(job_obj)
                error_count += 1
                continue
            db.update_job_extraction(result.id, {
                "company_country":   result.company_country or "unknown",
                "industry_sector":   result.industry_sector or "other",
                "language_required": result.language_required or "unknown",
                "work_mode":         result.work_mode or "unknown",
                "geo_zone":          result.geo_zone or "unknown",
                "company_size":      result.company_size or "unknown",
                "contract_type":     result.contract_type or "unknown",
                "summary":           result.summary or "",
                "extracted_by":      result.extracted_by,
            })
            extracted_count += 1
            if i < len(unextracted):
                time.sleep(4)
        print(f"  Extraction complete: {extracted_count} extracted, {error_count} errors")

    # ── Phase 2: evaluation — evaluate_for_profile on all survivors ─────────────
    tier0_count = 0
    tier0_details: dict[str, int] = {}
    tier1_count = 0
    scored_count = 0

    # Re-read from DB to pick up freshly extracted fields
    jobs_to_score = db.get_jobs_for_scoring(
        profile_id=profile.id,
        pre_filter=effective_pre_filter or None,
        rescore=args.rescore,
    )
    if args.limit and len(jobs_to_score) > args.limit:
        jobs_to_score = jobs_to_score[:args.limit]

    if not jobs_to_score:
        print("All jobs already scored for this profile — nothing to do.")
    else:
        for i, job_dict in enumerate(jobs_to_score, 1):
            title   = job_dict.get("title", "")
            company = job_dict.get("company", "")
            print(f"  Evaluating {i}/{len(jobs_to_score)}: {title[:50]} @ {company[:30]}")

            job_obj = _dict_to_posting(job_dict)
            result = evaluate_for_profile(job_obj, profile)

            if result is None:
                db.save_unscored(job_obj)
                error_count += 1
                continue

            db.save_scored(job_obj, result, profile.id)
            scored_count += 1

            if result["scored_by"] == "tier_0":
                tier0_count += 1
                # Track filter type for detail
                reason = result.get("reason", "")
                for key in ("language", "sector", "country", "work_mode"):
                    if key in reason:
                        tier0_details[key] = tier0_details.get(key, 0) + 1
                        break
                else:
                    tier0_details["other"] = tier0_details.get("other", 0) + 1
            else:
                tier1_count += 1

        print(f"\nScoring complete: {scored_count} scored, {error_count} errors")
        if tier0_count:
            details = ", ".join(f"{k}: {v}" for k, v in sorted(tier0_details.items()))
            print(f"  Tier 0 (deterministic): {tier0_count} filtered  ({details})")
        if tier1_count:
            print(f"  Tier 1 (LLM):           {tier1_count} evaluated")

    # ── Build digest ─────────────────────────────────────────────────────────
    all_scored = db.get_digest(profile.id, min_score=profile.score_threshold, status=None)

    # Post-scoring filters — applied at digest assembly, not at scoring
    excl_geo = excl_work_mode = excl_country = excl_sector = excl_language = 0
    digest_jobs = []
    for job_dict in all_scored:
        geo_zone         = job_dict.get("geo_zone", "unknown")
        work_mode        = job_dict.get("work_mode", "unknown")
        company_country  = job_dict.get("company_country", "unknown")
        industry_sector  = job_dict.get("industry_sector", "other")
        language_required = job_dict.get("language_required", "unknown")

        if profile.allowed_geo_zones and geo_zone and geo_zone not in profile.allowed_geo_zones:
            excl_geo += 1
            continue
        if profile.allowed_work_modes and work_mode and work_mode not in profile.allowed_work_modes:
            excl_work_mode += 1
            continue
        # country allowlist: unknown always passes through
        if (profile.allowed_countries is not None
                and company_country != "unknown"
                and company_country not in profile.allowed_countries):
            excl_country += 1
            continue
        if industry_sector in profile.excluded_sectors:
            excl_sector += 1
            continue
        if language_required in profile.excluded_languages:
            excl_language += 1
            continue
        digest_jobs.append(job_dict)

    digest_jobs.sort(key=lambda x: x["score"], reverse=True)
    hot = [j for j in digest_jobs if j["score"] >= 8]
    mid = [j for j in digest_jobs if 5 <= j["score"] <= 7]

    stats = db.get_stats(profile.id)
    total_excl = excl_geo + excl_work_mode + excl_country + excl_sector + excl_language
    print(f"\n--- Stats [{profile.name}] ---")
    if total_excl:
        print(f"🌍 {total_excl} jobs excluded "
              f"(geo/work_mode: {excl_geo + excl_work_mode}, "
              f"country: {excl_country}, sector: {excl_sector}, language: {excl_language})")
    print(f"✅ {len(digest_jobs)} jobs in digest  (🔥 {len(hot)} hot  ⭐ {len(mid)} solid)")
    print(f"📊 DB: {stats['total']} jobs total · {stats['hot']} 🔥 hot · {stats['solid']} ⭐ solid")

    # ── JSON output ───────────────────────────────────────────────────────────
    today = date.today().isoformat()
    output_dir = os.path.join(os.path.dirname(__file__), "outputs")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"jobs_{profile.id}_{today}.json")
    with open(output_path, "w") as f:
        json.dump(digest_jobs, f, indent=2, ensure_ascii=False)
    print(f"Saved to {output_path}")

    # ── Notify ────────────────────────────────────────────────────────────────
    send_email_digest(digest_jobs)
    export_joplin(digest_jobs)

    # ── Top 5 ─────────────────────────────────────────────────────────────────
    def emoji(score: int) -> str:
        return "🔥" if score >= 8 else "⭐"

    print("\n--- Top 5 ---")
    for i, job in enumerate(digest_jobs[:5], 1):
        score = job["score"]
        print(f"\n{emoji(score)} #{i} [{score}/10] {job['title']} @ {job['company']}  [{job.get('scored_by','?')}]")
        print(f"  Source   : {job.get('source', '')}")
        print(f"  Location : {job.get('location', '')}")
        print(f"  URL      : {job.get('url', '')}")
        print(f"  Reason   : {job.get('reason', '')}")


if __name__ == "__main__":
    main()
