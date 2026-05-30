"""job_agent — automated PM job search (single-profile mode)."""

import subprocess
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from profiles import get_active_profile
from storage import JobStorage

DB_PATH = "data/jobs.db"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def main():
    profile = get_active_profile()
    db = JobStorage(DB_PATH)
    t0 = time.monotonic()

    try:
        print(f"[{_ts()}] === Step 1: Scraping ===")
        subprocess.run([sys.executable, "scrape.py"], check=True)
        t1 = time.monotonic()
        print(f"[{_ts()}] Scraping done — {t1 - t0:.0f}s elapsed")

        print(f"\n[{_ts()}] === Step 2: Extraction ===")
        subprocess.run([sys.executable, "score.py", "--extract"], check=True)
        t2 = time.monotonic()
        print(f"[{_ts()}] Extraction done — {t2 - t1:.0f}s elapsed, {t2 - t0:.0f}s total")

        print(f"\n[{_ts()}] === Step 3: Scoring [{profile.id}] ===")
        subprocess.run([sys.executable, "score.py", "--profile", profile.id], check=True)
        t3 = time.monotonic()
        total = t3 - t0
        print(f"[{_ts()}] Scoring done — {t3 - t2:.0f}s elapsed, {total:.0f}s total")

        db.update_last_run(duration_seconds=round(total, 1))

    except Exception as e:
        total = time.monotonic() - t0
        db.log_run(
            profile_id=profile.id if profile else "unknown",
            jobs_scraped=0,
            jobs_scored=0,
            jobs_above_threshold=0,
            status="error",
            error_msg=str(e),
            duration_seconds=round(total, 1),
        )
        raise


if __name__ == "__main__":
    main()
