import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date

from dotenv import load_dotenv
load_dotenv()

from notifier import send_email_digest, export_joplin
from paths import DB_PATH
from profiles import SearchProfile
from job_actions import extract_one, score_one, _dict_to_posting, _discover_contacts
from scorer import extract_job_fields, evaluate_for_profile
from title_gate import is_product_management_title
from storage import JobStorage

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
    {
        "title": "Senior Product Manager — Digital Assets",
        "company": "SwissNeo",
        "location": "Geneva, CH (Hybrid)",
        "base_location": "Geneva, Switzerland",
        "description": (
            "SwissNeo is a Swiss neobank launching tokenized custody for digital assets. "
            "We are seeking a Senior Product Manager to lead our crypto custody and "
            "tokenization product. Series B, 120 employees, flexible hybrid with 2 days "
            "in our Geneva office."
        ),
    },
    {
        "title": "Senior Product Manager — RWA Protocol",
        "company": "TokenBridge Labs",
        "location": "Remote (Europe)",
        "base_location": "Remote, EU",
        "description": (
            "TokenBridge Labs is a Web3 protocol for real-world asset tokenization on "
            "Ethereum. We are seeking a Senior Product Manager to drive the tokenization "
            "platform roadmap. Fully remote, EU-based team, 45 employees. "
            "The role is open to candidates across Europe."
        ),
    },
    {
        "title": "Senior Product Manager — Payments",
        "company": "Istanbul Fintech",
        "location": "Remote (Turkey)",
        "base_location": "Istanbul, Türkiye",
        "description": (
            "Istanbul Fintech is a leading Turkish payments platform serving 5M+ users "
            "across Europe and the Middle East. We are seeking a Senior Product Manager "
            "to own our cross-border payments roadmap. Fully remote, English-speaking team, "
            "Series C, 200 employees."
        ),
    },
    {
        "title": "Senior Product Manager",
        "company": "WarsawSoft",
        "location": "Warsaw, Poland (On-site)",
        "base_location": "Warsaw, Poland",
        "description": (
            "WarsawSoft is a Polish B2B SaaS company. On-site role in our Warsaw "
            "office, 5 days a week. Series A, 60 employees."
        ),
    },
    {
        "title": "Senior Product Manager — Payments",
        "company": "ParisPay",
        "location": "Paris, France (Hybrid)",
        "base_location": "Paris, France",
        "description": (
            "ParisPay is a French fintech. Hybrid role, 3 days in our Paris office. "
            "Series B payments platform, 180 employees."
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
    """Score test jobs using the real production path: extract → evaluate. No DB writes."""
    from models import JobPosting

    expectations = [
        (5, 7, "FELFEL — food-tech hybrid, CH employer → tier 4–5"),
        (6, 8, "Consensys MetaMask — pure Web3 remote → CV stretch"),
        (1, 3, "SIX Group — large corporate → hard-exclusion cap"),
        (8, 9, "SwissNeo — tokenized custody, crypto-fintech bridge, CH hybrid → tier 2"),
        (6, 8, "TokenBridge Labs — Web3 RWA, EU remote, salary unstated → tier 3 + comp flag"),
        (4, 5, "Istanbul Fintech — TR fintech remote → tier 6, must NOT be Tier-0 filtered"),
        (1, 2, "WarsawSoft — on-site outside CH → Tier-0 per-mode geography reject (score 2)"),
        (1, 2, "ParisPay — hybrid outside CH → Tier-0 per-mode geography reject (score 2)"),
    ]

    print(f"\n=== MOCK TEST: {profile.name} ({profile.id}) ===")
    print(f"Testing {len(MOCK_JOBS)} jobs with production path (extract → evaluate)...\n")

    all_pass = True
    for i, job_dict in enumerate(MOCK_JOBS):
        job = JobPosting(
            source="mock",
            title=job_dict["title"],
            company=job_dict["company"],
            location=job_dict["location"],
            url=f"mock://{i}",
            description=job_dict.get("description"),
            base_location=job_dict.get("base_location"),
        )

        # Phase 1: extraction
        job = extract_job_fields(job)
        if job is None:
            print(f"  ❌ [FAIL] {job_dict['title']} @ {job_dict['company']} — extraction failed")
            all_pass = False
            continue

        # Phase 2: evaluation
        result = evaluate_for_profile(job, profile)
        if result is None:
            print(f"  ❌ [FAIL] {job_dict['title']} @ {job_dict['company']} — evaluation failed")
            all_pass = False
            continue

        score = result["score"]
        scored_by = result.get("scored_by", "?")
        reason = result.get("reason", "")

        lo, hi, desc = expectations[i]
        passed = lo <= score <= hi
        if not passed:
            all_pass = False

        emoji = "✅" if passed else "❌"
        print(f"  {emoji} [{score}/10] {job_dict['title']} @ {job_dict['company']}")
        print(f"    Expected: {lo}–{hi} ({desc})")
        print(f"    Mode: {result.get('work_mode')} | Geo: {result.get('geo_zone')} | "
              f"Country: {result.get('company_country')} | Sector: {result.get('industry_sector')}")
        print(f"    Scored by: {scored_by}")
        print(f"    Reason: {reason}")
        print()

        time.sleep(4)  # avoid rate-limit between mock jobs

    if all_pass:
        print(f"✅ All {len(MOCK_JOBS)} cases in expected bands.")
    else:
        print("❌ Some cases out of band — review scoring_context or Tier-0 rules.")




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

        result = extract_one(job_dict["id"])
        if result is None or result.get("status") == "error":
            error_count += 1
            continue
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




def main():
    parser = argparse.ArgumentParser(description="score.py — score jobs for a profile / extract job fields")
    parser.add_argument("--profile", default=None,
                        help="Profile ID to score for (optional; defaults to the active profile)")
    parser.add_argument("--extract", action="store_true",
                        help="Run profile-independent field extraction (no evaluation)")
    parser.add_argument("--rescore", action="store_true",
                        help="Delete existing scores for this profile and re-score all jobs")
    parser.add_argument("--mock", action="store_true",
                        help="Score 3 hardcoded test jobs to verify profile context; no DB writes")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Cap the number of jobs processed this run")
    args = parser.parse_args()

    db = JobStorage(DB_PATH)

    if not args.extract and not args.profile:
        from profiles import DEFAULT_PROFILE_ID
        args.profile = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)

    if args.extract and args.profile:
        print("--extract and --profile are mutually exclusive. Run them separately.")
        sys.exit(1)

    if args.mock and args.rescore:
        print("--mock and --rescore are mutually exclusive.")
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print("DB not found — run scrape.py first.")
        sys.exit(1)

    # ── Resolve profile: DB first, code object as fallback/seed ──────────
    from profiles import load_active_profile, ALL_PROFILES as _ALL, DEFAULT_PROFILE_ID as _DEF

    def _resolve_profile(pid: str):
        row = db.get_profile(pid)
        if row:
            return SearchProfile.from_criteria(row["id"], row["name"], row["criteria"])
        if pid in _ALL:
            p = _ALL[pid]
            db.upsert_profile(p)
            return p
        print(f"Unknown profile '{pid}'. Valid in code: {list(_ALL.keys())}")
        sys.exit(1)

    if args.mock:
        if not args.profile:
            print("--mock requires --profile.")
            sys.exit(1)
        _run_mock(_resolve_profile(args.profile))
        return

    if args.extract:
        _run_extraction(args.limit)
        return

    profile = _resolve_profile(args.profile)
    t0 = time.monotonic()
    db.upsert_profile(profile)
    print(f"Profile: {profile.name} ({profile.id})")
    denylist = getattr(profile, "denylisted_companies", []) or []
    if denylist:
        print(f"  📋 Company denylist (active profile): {len(denylist)} entries")

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
    bl_jobs = db.count_blacklisted_jobs()
    print(f"Pre-filter applied: {total_in_db} jobs → {len(jobs_to_score)} after SQL filter "
          f"({skipped} skipped, including jobs older than 30 days)")
    if bl_jobs:
        print(f"  🚫 {bl_jobs} jobs excluded by company blacklist")

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
            result = extract_one(job_dict["id"])
            if result is None or result.get("status") == "error":
                error_count += 1
                continue
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
        gate_filtered = 0
        for i, job_dict in enumerate(jobs_to_score, 1):
            title   = job_dict.get("title", "")
            company = job_dict.get("company", "")
            print(f"  Evaluating {i}/{len(jobs_to_score)}: {title[:50]} @ {company[:30]}")

            # ── Title gate: skip non-PM jobs from monitored companies ──
            if job_dict.get("monitored_company_id"):
                if not is_product_management_title(title):
                    db.set_job_filtered_non_product(job_dict["id"])
                    gate_filtered += 1
                    print(f"    → filtered (non-PM title from monitored company)")
                    continue

            result = score_one(job_dict["id"], profile.id)

            if result is None or result.get("status") == "error":
                error_count += 1
                continue

            scored_count += 1

            if result.get("scored_by") == "tier_0":
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

            if i < len(jobs_to_score):
                time.sleep(4)

        print(f"\nScoring complete: {scored_count} scored, {error_count} errors"
              + (f", {gate_filtered} filtered (non-PM title)" if gate_filtered else ""))
        if tier0_count:
            details = ", ".join(f"{k}: {v}" for k, v in sorted(tier0_details.items()))
            print(f"  Tier 0 (deterministic): {tier0_count} filtered  ({details})")
        if tier1_count:
            print(f"  Tier 1 (LLM):           {tier1_count} evaluated")

    # ── Build digest ─────────────────────────────────────────────────────────
    all_scored = db.get_digest(profile.id, min_score=profile.score_threshold)

    # Post-scoring filters — applied at digest assembly, not at scoring
    excl_work_mode = excl_sector = excl_language = 0
    digest_jobs = []
    for job_dict in all_scored:
        work_mode        = job_dict.get("work_mode", "unknown")
        industry_sector  = job_dict.get("industry_sector", "other")
        language_required = job_dict.get("language_required", "unknown")

        if profile.allowed_work_modes and work_mode and work_mode not in profile.allowed_work_modes:
            excl_work_mode += 1
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
    total_excl = excl_work_mode + excl_sector + excl_language
    print(f"\n--- Stats [{profile.name}] ---")
    if total_excl:
        print(f"🌍 {total_excl} jobs excluded "
              f"(work_mode: {excl_work_mode}, "
              f"sector: {excl_sector}, language: {excl_language})")
    print(f"✅ {len(digest_jobs)} jobs in digest  (🔥 {len(hot)} hot  ⭐ {len(mid)} solid)")
    print(f"📊 DB: {stats['total']} jobs total · {stats['hot']} 🔥 hot · {stats['solid']} ⭐ solid")

    db.update_last_run(
        jobs_scored=scored_count,
        jobs_above_threshold=len(digest_jobs),
        status="success" if error_count == 0 else "partial",
        duration_seconds=round(time.monotonic() - t0, 1),
    )

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
