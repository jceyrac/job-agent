import argparse
import subprocess
import sys

from dotenv import load_dotenv
load_dotenv()

from profiles import ALL_PROFILES, DEFAULT_PROFILE_ID
from storage import JobStorage

DB_PATH = "data/jobs.db"


def main():
    parser = argparse.ArgumentParser(description="job_agent — automated PM job search")
    parser.add_argument(
        "--profile",
        default=None,
        choices=list(ALL_PROFILES.keys()),
        help=f"Profile to score (default: active profile from DB or {DEFAULT_PROFILE_ID})",
    )
    args = parser.parse_args()

    db = JobStorage(DB_PATH)
    active_profile_id = args.profile or db.get_config("active_profile_id", default=DEFAULT_PROFILE_ID)

    print("=== Step 1: Scraping ===")
    subprocess.run([sys.executable, "scrape.py"], check=True)

    print(f"\n=== Step 2: Scoring [{active_profile_id}] ===")
    subprocess.run([sys.executable, "score.py", "--profile", active_profile_id], check=True)


if __name__ == "__main__":
    main()
