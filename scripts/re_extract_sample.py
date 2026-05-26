"""Re-extract structured fields from 10 jobs classified as global_remote + unknown country.

Prints before/after for each job to verify the prompt changes improve US detection.
Dry-run: does not write results back to the DB.
"""

import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import JobPosting
from scorer import extract_job_fields

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jobs.db")
SAMPLE_SIZE = 10


def main():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT * FROM jobs
           WHERE geo_zone = 'global_remote' AND company_country = 'unknown'
           AND description IS NOT NULL AND description != ''
           ORDER BY last_seen DESC
           LIMIT ?""",
        (SAMPLE_SIZE,),
    ).fetchall()

    conn.close()

    if not rows:
        print("No matching jobs found (geo_zone='global_remote', company_country='unknown').")
        return

    print(f"Re-extracting {len(rows)} jobs...\n")

    for i, row in enumerate(rows, 1):
        d = dict(row)
        job = JobPosting(
            source=d.get("source", ""),
            title=d.get("title", ""),
            company=d.get("company", ""),
            location=d.get("location", ""),
            url=d.get("url", ""),
            description=d.get("description") or "",
            base_location=d.get("base_location") or "",
            posted_date=d.get("posted_date"),
        )
        # Pre-fill existing extracted fields so the function sees them
        job.geo_zone = d.get("geo_zone") or "unknown"
        job.company_country = d.get("company_country") or "unknown"
        job.work_mode = d.get("work_mode") or "unknown"
        job.company_size = d.get("company_size") or "unknown"
        job.contract_type = d.get("contract_type") or "unknown"
        job.industry_sector = d.get("industry_sector") or "other"
        job.language_required = d.get("language_required") or "unknown"
        job.summary = d.get("summary") or ""

        before_geo = job.geo_zone
        before_country = job.company_country
        desc_snippet = (d.get("description") or "")[:200].replace("\n", " ")

        print(f"── {i}. {d.get('title')} @ {d.get('company')} ──")
        print(f"    Location: {d.get('location')} | Base: {d.get('base_location')}")
        print(f"    Desc:     {desc_snippet}...")
        print(f"    BEFORE:   geo_zone={before_geo}, company_country={before_country}")

        try:
            result = extract_job_fields(job)
            if result is None:
                print(f"    AFTER:    EXHAUSTED (all models depleted)")
            else:
                print(f"    AFTER:    geo_zone={result.geo_zone}, company_country={result.company_country}")
                if result.geo_zone != before_geo or result.company_country != before_country:
                    print(f"    >>> CHANGED")
                else:
                    print(f"    (unchanged)")
        except Exception as e:
            print(f"    ERROR:    {e}")

        print()


if __name__ == "__main__":
    main()
