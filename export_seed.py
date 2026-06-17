#!/usr/bin/env python3
"""export_seed.py — Export companies table to data/companies.json for git versioning.

Excludes env-specific fields (id, researched_at) so the file can be safely
synced from Dev to Live via git push/pull.
"""

import json
import sqlite3
import sys

from paths import DB_PATH


def main():
    if not DB_PATH.endswith(".db"):
        print("DB_PATH must point to a .db file.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT name, website, careers_url, ats_provider, ats_identifier,
                  ats_board_url, scraping_method, monitoring_status,
                  research_notes, research_confidence, x_handle,
                  industry_sector, company_country, notes
           FROM companies
           WHERE monitoring_status != 'unmonitored'
              OR ats_provider IS NOT NULL
              OR scraper_id IS NOT NULL
           ORDER BY name"""
    ).fetchall()
    conn.close()

    companies = []
    for row in rows:
        c = dict(row)
        # Rename ats_identifier → ats_board_slug for clarity in JSON
        c["ats_board_slug"] = c.pop("ats_identifier", None)
        # Map DB columns to JSON field names
        c["sector"] = c.pop("industry_sector", None)
        c["hq_location"] = c.pop("company_country", None)
        companies.append(c)

    output_path = "data/companies.json"
    with open(output_path, "w") as f:
        json.dump(companies, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Exported {len(companies)} companies to {output_path}")


if __name__ == "__main__":
    main()
