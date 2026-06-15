"""job_agent — automated PM job search (single-profile mode)."""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from paths import DB_PATH
from profiles import DEFAULT_PROFILE_ID
from storage import JobStorage


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def main():
    parser = argparse.ArgumentParser(description="job_agent full pipeline")
    parser.add_argument("--monitored-only", action="store_true",
                        help="Run only the monitored-company scrape step, then extract+score")
    parser.add_argument("--no-monitoring", action="store_true",
                        help="Skip the monitored-company scrape step even if enabled in config")
    parser.add_argument("--no-scrape", action="store_true",
                        help="Skip the broad scrape step even if enabled in config")
    args = parser.parse_args()

    if args.monitored_only and args.no_monitoring:
        print("--monitored-only and --no-monitoring are mutually exclusive.")
        sys.exit(1)

    db = JobStorage(DB_PATH)
    active_id = db.get_config("active_profile_id", DEFAULT_PROFILE_ID)
    t0 = time.monotonic()

    # ── Decision logic ──────────────────────────────────────────────────────
    # Broad scrape: off if --monitored-only or --no-scrape, else config-controlled
    if args.monitored_only or args.no_scrape:
        run_broad = False
    else:
        cfg = db.get_config("scrape.enabled_in_pipeline")
        run_broad = cfg is None or cfg.lower() == "true"

    # Monitoring: forced on by --monitored-only, off by --no-monitoring,
    # else config-controlled (and only runs if ≥1 monitored company exists)
    if args.monitored_only:
        run_monitoring = True
    elif args.no_monitoring:
        run_monitoring = False
    else:
        cfg = db.get_config("monitoring.enabled_in_pipeline")
        enabled_in_config = cfg is None or cfg.lower() == "true"
        if enabled_in_config:
            run_monitoring = bool(db.get_monitored_companies())
        else:
            run_monitoring = False

    try:
        step_num = 1
        t_prev = t0

        # ── Step: Monitored-company scrape (conditional) ────────────────────
        if run_monitoring:
            print(f"\n[{_ts()}] === Step {step_num}: Monitored scrape ===")
            subprocess.run([sys.executable, "scrape.py", "--monitored-only"],
                           check=True)
            t_now = time.monotonic()
            print(f"[{_ts()}] Monitored scrape done"
                  f" — {t_now - t_prev:.0f}s elapsed"
                  + (f", {t_now - t0:.0f}s total" if step_num > 1 else ""))
            step_num += 1
            t_prev = t_now

        # ── Step: Broad scrape (conditional) ────────────────────────────────
        if run_broad:
            print(f"\n[{_ts()}] === Step {step_num}: Broad scrape ===")
            subprocess.run([sys.executable, "scrape.py"], check=True)
            t_now = time.monotonic()
            print(f"[{_ts()}] Broad scrape done"
                  f" — {t_now - t_prev:.0f}s elapsed"
                  + (f", {t_now - t0:.0f}s total" if step_num > 1 else ""))
            step_num += 1
            t_prev = t_now

        # ── Step: Extraction ────────────────────────────────────────────────
        print(f"\n[{_ts()}] === Step {step_num}: Extraction ===")
        subprocess.run([sys.executable, "score.py", "--extract"], check=True)
        t_now = time.monotonic()
        print(f"[{_ts()}] Extraction done"
              f" — {t_now - t_prev:.0f}s elapsed, {t_now - t0:.0f}s total")
        step_num += 1
        t_prev = t_now

        # ── Step: Scoring ───────────────────────────────────────────────────
        print(f"\n[{_ts()}] === Step {step_num}: Scoring [{active_id}] ===")
        subprocess.run([sys.executable, "score.py", "--profile", active_id],
                       check=True)
        t_now = time.monotonic()
        total = t_now - t0
        print(f"[{_ts()}] Scoring done"
              f" — {t_now - t_prev:.0f}s elapsed, {total:.0f}s total")

        db.update_last_run(duration_seconds=round(total, 1))

    except Exception as e:
        total = time.monotonic() - t0
        db.log_run(
            profile_id=active_id,
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
