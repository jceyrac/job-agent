#!/usr/bin/env python3
"""Reconstruct per-source pipeline attrition from DB using real predicates.

Usage:
    python3 scripts/filter_funnel.py --source LinkedIn [chemin/vers/jobs.db]
    python3 scripts/filter_funnel.py --source Indeed --db data/jobs_dev_backup.db

Read-only. Imports JobFilterEngine and active profile — does NOT rewrite any
filter logic. Same pattern as scripts/audit_provenance.py.

Spec: specs/024b-contract-regie-diagnostics, FR-007.
"""
import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timezone, timedelta

# ── Bootstrap: allow running from repo root without installing ────────────────
sys.path.insert(0, ".")

from models import JobFilter, JobPosting
from filters import JobFilterEngine
from profiles import load_active_profile


# ── Column list matching the full JobPosting constructor ──────────────────────
_MAIN_COLS = [
    "id", "source", "title", "company", "location", "url",
    "canonical_url", "norm_title", "norm_company", "posted_date",
    "description", "tags", "salary", "summary", "work_mode",
    "base_location", "company_size", "contract_type", "geo_zone",
    "company_country", "industry_sector", "language_required",
    "extracted_at", "extracted_by",
]


def _row_to_jobposting(row: tuple) -> JobPosting:
    """Reconstruct a minimal JobPosting from a DB row for filtering."""
    d = dict(zip(_MAIN_COLS, row))

    # Parse posted_date
    pd_raw = d.get("posted_date")
    if isinstance(pd_raw, str):
        try:
            pd_raw = datetime.strptime(pd_raw, "%Y-%m-%d").date()
        except ValueError:
            pd_raw = None

    # Parse tags (JSON array)
    tags = []
    tags_raw = d.get("tags") or "[]"
    if isinstance(tags_raw, str):
        try:
            tags = json.loads(tags_raw)
        except json.JSONDecodeError:
            tags = []

    return JobPosting(
        source=d.get("source") or "",
        title=d.get("title") or "",
        company=d.get("company") or "",
        location=d.get("location") or "",
        url=d.get("url") or "",
        posted_date=pd_raw,
        description=d.get("description"),
        tags=tags,
        salary=d.get("salary"),
        work_mode=d.get("work_mode"),
        base_location=d.get("base_location"),
        company_size=d.get("company_size"),
        contract_type=d.get("contract_type"),
        geo_zone=d.get("geo_zone"),
        company_country=d.get("company_country"),
        industry_sector=d.get("industry_sector"),
        language_required=d.get("language_required"),
        summary=d.get("summary"),
    )


def _pct(n: int, total: int) -> str:
    """Return a percentage string like '22%'."""
    if total == 0:
        return "  (0%)"
    return f"  ({n * 100 // total}%)"


def main():
    ap = argparse.ArgumentParser(
        description="Reconstruct per-source pipeline attrition from DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python scripts/filter_funnel.py --source LinkedIn\n"
               "  python scripts/filter_funnel.py --source Indeed --db data/prod_snapshot.db",
    )
    ap.add_argument("--source", required=True, help="Source name to analyze")
    ap.add_argument("--db", default="data/jobs.db")
    a = ap.parse_args()

    # ── Connect (read-only) ──────────────────────────────────────────────────
    try:
        conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    except sqlite3.OperationalError as e:
        print(f"❌ Cannot open {a.db}: {e}")
        sys.exit(1)
    q = conn.execute

    # ── Warmth check ─────────────────────────────────────────────────────────
    mx = q(
        "SELECT MAX(posted_date) FROM jobs WHERE source=?", (a.source,)
    ).fetchone()[0]
    if mx:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(mx)).days
        except Exception:
            age = None
        print(f"DB         : {a.db}")
        print(f"Source     : {a.source}")
        print(f"Last seen  : {mx}  ({age}d ago)" if age is not None else f"Last seen  : {mx}")
    else:
        print(f"❌ No jobs found for source '{a.source}'")
        sys.exit(1)
    print()

    # ── Load jobs ────────────────────────────────────────────────────────────
    cols_sql = ", ".join(f"j.{c}" for c in _MAIN_COLS if c not in ("id",))
    rows = q(
        f"SELECT j.id, {cols_sql} FROM jobs j WHERE j.source = ?",
        (a.source,),
    ).fetchall()

    jobs = [_row_to_jobposting(r) for r in rows]
    total = len(jobs)
    print(f"Collectés bruts : {total}")
    if not jobs:
        return

    # ── Load active profile + build JobFilter ─────────────────────────────────
    try:
        profile = load_active_profile(conn)
    except Exception as e:
        print(f"⚠️  Could not load active profile: {e}")
        print("   Running with default (empty) profile filters.")
        profile = None

    jf = JobFilter()
    if profile:
        jf.titles = profile.job_titles or []
        jf.locations = profile.job_locations or []
        jf.exclude = getattr(profile, "title_exclude", []) or []
        # remote_only / remote_or_hybrid are derived, not stored on profile
        allowed_wm = profile.allowed_work_modes or []
        if allowed_wm == ["remote"]:
            jf.remote_only = True
        elif "remote" in allowed_wm and "hybrid" in allowed_wm:
            jf.remote_or_hybrid = True

    # ── Step-by-step filtering ───────────────────────────────────────────────
    # 1. Date filter (always applied, not configurable)
    cutoff = date.today() - timedelta(days=30)
    after_date = [j for j in jobs if j.posted_date and j.posted_date >= cutoff]
    excl_date = total - len(after_date)
    print(f"  → après filtre date (>30j)        {len(after_date):4d}{_pct(len(after_date), total)}"
          + (f"  [−{excl_date} sautés]" if excl_date else ""))

    # 2. Title filter (substring, case-insensitive — from JobFilterEngine)
    if jf.titles:
        after_title = [
            j for j in after_date
            if any(t.lower() in j.title.lower() for t in jf.titles)
        ]
    else:
        after_title = after_date
    print(f"  → après filtre titre PM/PO        {len(after_title):4d}{_pct(len(after_title), total)}")

    # 3. Date again + exclude words (simulating JobFilterEngine.apply)
    after_jfe, excl_jfe, _ = JobFilterEngine.apply(after_title, jf)
    print(f"  → après JobFilterEngine complet    {len(after_jfe):4d}{_pct(len(after_jfe), total)}")

    # 4. Work mode filter (profile Tier-0)
    allowed_wm = profile.allowed_work_modes if profile else []
    if allowed_wm:
        after_wm = [j for j in after_jfe if (j.work_mode or "unknown") in allowed_wm]
    else:
        after_wm = after_jfe
    print(f"  → après filtre work_mode          {len(after_wm):4d}{_pct(len(after_wm), total)}")

    # 5. Language filter (profile Tier-0)
    spoken = profile.languages_spoken if profile else []
    if spoken:
        after_lang = [
            j for j in after_wm
            if (j.language_required or "unknown") in ("unknown", "multiple")
            or (j.language_required or "unknown") in spoken
        ]
    else:
        after_lang = after_wm
    print(f"  → après filtre langue             {len(after_lang):4d}{_pct(len(after_lang), total)}")

    # 6. Geo zone filter
    allowed_geo = profile.allowed_geo_zones if profile else []
    if allowed_geo:
        after_geo = [j for j in after_lang if (j.geo_zone or "unknown") in allowed_geo]
    else:
        after_geo = after_lang
    print(f"  → après filtre géo                {len(after_geo):4d}{_pct(len(after_geo), total)}")

    # 7. Score threshold (from job_scores, if profile scoring has run)
    if profile:
        rows_scored = q(
            "SELECT job_id FROM job_scores WHERE score >= 5 AND profile_id = ?",
            (profile.id,),
        ).fetchall()
        scored_ids = {r[0] for r in rows_scored}
    else:
        scored_ids = set()
    if scored_ids:
        in_digest = [j for j in after_geo if getattr(j, "id", None) in scored_ids]
        print(f"  → au digest (score ≥ seuil)       {len(in_digest):4d}{_pct(len(in_digest), total)}")
    else:
        print(f"  → au digest (score ≥ seuil)       N/A  (no scores for this profile)")

    print()
    print("Note: les compteurs sont cumulatifs — chaque étape filtre le résultat de la précédente.")


if __name__ == "__main__":
    main()
