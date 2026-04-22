"""backfill_descriptions.py — Fetch and store descriptions for jobs with description=NULL."""

import json
import re
import sqlite3
import time
import sys

DB_PATH = "data/jobs.db"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Referer": "https://google.com",
}

# Source-specific CSS selectors tried after JSON-LD fails
SOURCE_SELECTORS = {
    "Web3Career":    ".main-border-sides-job",
    "CryptoJobs.com": ".details-area",
}


def _clean_text(text: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#*`>\[\]]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _fetch_description(url: str, source: str) -> str | None:
    import httpx
    from bs4 import BeautifulSoup

    if not url:
        return None

    # CryptoJobsList: individual pages live at /jobs/<slug>, not /<slug>
    if source == "CryptoJobsList" and "cryptojobslist.com" in url:
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        url = f"https://cryptojobslist.com/jobs/{slug}"

    try:
        r = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        # 1. JSON-LD structured data (works across most job boards)
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(sc.string or "")
                for node in (d.get("@graph", [d]) if isinstance(d, dict) else [d]):
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        desc = node.get("description", "")
                        if desc and len(desc) > 50:
                            return _clean_text(desc)
            except Exception:
                pass

        # 2. Source-specific CSS selector fallback
        selector = SOURCE_SELECTORS.get(source)
        if selector:
            el = soup.select_one(selector)
            if el:
                return _clean_text(el.get_text(separator=" ", strip=True))

        return None
    except Exception as e:
        return None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id, url, source FROM jobs WHERE description IS NULL ORDER BY source, last_seen DESC"
    ).fetchall()

    if not rows:
        print("No jobs with NULL description — nothing to do.")
        return

    print(f"Backfilling descriptions for {len(rows)} jobs...\n")

    updated = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        job_id = row["id"]
        url    = row["url"]
        source = row["source"]

        print(f"  [{i}/{len(rows)}] {source} — {url[:70]}", end=" ", flush=True)

        description = _fetch_description(url, source)

        if description:
            conn.execute(
                "UPDATE jobs SET description = ? WHERE id = ?",
                (description[:3000], job_id),
            )
            conn.commit()
            print(f"→ {len(description)} chars")
            updated += 1
        else:
            print("→ no description found")
            failed += 1

        if i < len(rows):
            time.sleep(0.4)

    print(f"\nDone: {updated} updated, {failed} could not be fetched.")


if __name__ == "__main__":
    main()
