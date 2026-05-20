"""job_agent — automated PM job search (single-profile mode)."""

import subprocess
import sys

from dotenv import load_dotenv
load_dotenv()

from profiles import get_active_profile


def main():
    active_profile_id = get_active_profile().id

    print("=== Step 1: Scraping ===")
    subprocess.run([sys.executable, "scrape.py"], check=True)

    print("\n=== Step 2: Extraction ===")
    subprocess.run([sys.executable, "score.py", "--extract"], check=True)

    print(f"\n=== Step 3: Scoring [{active_profile_id}] ===")
    subprocess.run([sys.executable, "score.py", "--profile", active_profile_id], check=True)


if __name__ == "__main__":
    main()
