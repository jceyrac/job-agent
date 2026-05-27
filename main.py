"""job_agent — automated PM job search (single-profile mode)."""

import subprocess
import sys

from dotenv import load_dotenv
load_dotenv()

from profiles import get_active_profile
from storage import JobStorage

DB_PATH = "data/jobs.db"


def main():
    profile = get_active_profile()
    db = JobStorage(DB_PATH)

    try:
        print("=== Step 1: Scraping ===")
        subprocess.run([sys.executable, "scrape.py"], check=True)

        print("\n=== Step 2: Extraction ===")
        subprocess.run([sys.executable, "score.py", "--extract"], check=True)

        print(f"\n=== Step 3: Scoring [{profile.id}] ===")
        subprocess.run([sys.executable, "score.py", "--profile", profile.id], check=True)

    except Exception as e:
        db.log_run(
            profile_id=profile.id if profile else "unknown",
            jobs_scraped=0,
            jobs_scored=0,
            jobs_above_threshold=0,
            status="error",
            error_msg=str(e),
        )
        raise


if __name__ == "__main__":
    main()
