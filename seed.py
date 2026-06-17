#!/usr/bin/env python3
"""seed.py — Upsert companies from data/companies.json into the DB.

Reads the versioned seed file and inserts/updates companies in the local DB.
Match key: name (case-insensitive via name_normalized).
Never touches jobs, scores, or run_logs tables.  Idempotent.
"""

import json
import sys

from paths import DB_PATH
from storage import JobStorage


def main():
    try:
        with open("data/companies.json") as f:
            companies = json.load(f)
    except FileNotFoundError:
        print("data/companies.json not found — nothing to seed.", file=sys.stderr)
        sys.exit(0)
    except json.JSONDecodeError as e:
        print(f"data/companies.json is invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not companies:
        print("data/companies.json is empty — nothing to seed.")
        return

    db = JobStorage(DB_PATH)
    upserted = 0
    up_to_date = 0

    for c in companies:
        name = c.get("name", "").strip()
        if not name:
            continue
        # Map JSON field names back to DB columns
        row = {
            "name": name,
            "website": c.get("website"),
            "careers_url": c.get("careers_url"),
            "ats_provider": c.get("ats_provider"),
            "ats_identifier": c.get("ats_board_slug") or c.get("ats_identifier"),
            "scraping_method": c.get("scraping_method"),
            "monitoring_status": c.get("monitoring_status", "watch_pending"),
            "research_notes": c.get("research_notes"),
            "research_confidence": c.get("research_confidence"),
            "x_handle": c.get("x_handle"),
            "industry_sector": c.get("sector") or c.get("industry_sector"),
            "company_country": c.get("hq_location") or c.get("company_country"),
            "notes": c.get("notes"),
        }
        changed = db.upsert_company_from_seed(row)
        if changed:
            upserted += 1
        else:
            up_to_date += 1

    print(f"{upserted} companies upserted, {up_to_date} already up to date")


if __name__ == "__main__":
    main()
