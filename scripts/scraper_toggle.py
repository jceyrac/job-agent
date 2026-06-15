import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import JobStorage

ALL_SCRAPERS = [
    "cryptojobs.com", "cryptojobslist", "defi_jobs", "greenhouse",
    "jobup", "linkedin", "remoteok", "tietalent", "web3career", "weworkremotely",
]

import sys
cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

db = JobStorage("/app/data/jobs.db")

if cmd == "indeed-only":
    for name in ALL_SCRAPERS:
        db.set_config(f"scraper.{name}.enabled", "false")
        print(f"  disabled {name}")
    db.set_config("scraper.indeed.enabled", "true")
    print("  enabled indeed")
    print("\nOnly Indeed is now enabled.")

elif cmd == "all-on":
    for name in ALL_SCRAPERS + ["indeed"]:
        db.set_config(f"scraper.{name}.enabled", "true")
        print(f"  enabled {name}")
    print("\nAll scrapers re-enabled.")

elif cmd == "status":
    for name in ALL_SCRAPERS + ["indeed"]:
        val = db.get_config(f"scraper.{name}.enabled")
        state = val if val else "default (class ENABLED)"
        print(f"  {name}: {state}")
