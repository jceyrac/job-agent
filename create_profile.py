"""create_profile.py — Interactive CLI to create, list, or delete search profiles."""

import argparse
import json
import re
import sys

from dotenv import load_dotenv
load_dotenv()

from profiles import SearchProfile
from storage import JobStorage

DB_PATH = "data/jobs.db"

WORK_MODES   = ["remote", "hybrid", "on-site", "unknown"]
GEO_ZONES    = ["europe", "global_remote", "us_only", "apac", "latam", "unknown"]
COMPANY_SIZES = ["startup", "scaleup", "sme", "large", "unknown"]


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val if val else default


def _multiselect(label: str, options: list[str], default_all: bool = True) -> list[str]:
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    default_hint = "all" if default_all else "none"
    raw = input(f"{label} (comma-separated numbers, Enter={default_hint}): ").strip()
    if not raw:
        return list(options) if default_all else []
    selected = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(options):
                selected.append(options[idx])
    return selected


def _multiline(label: str) -> str:
    print(f"{label} (end with a line containing only END):")
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def _cmd_list(db: JobStorage) -> None:
    profiles = db.get_all_profiles()
    if not profiles:
        print("No profiles found.")
        return
    print(f"\n{'ID':<20} {'Name':<30} {'Boost keywords'}")
    print("-" * 70)
    for p in profiles:
        criteria = json.loads(p.get("criteria") or "{}")
        boosts = ", ".join(criteria.get("boost_keywords", [])) or "—"
        print(f"{p['id']:<20} {p['name']:<30} {boosts}")
    print()


def _cmd_delete(db: JobStorage, profile_id: str) -> None:
    profiles = {p["id"] for p in db.get_all_profiles()}
    if profile_id not in profiles:
        print(f"Profile '{profile_id}' not found. Use --list to see existing profiles.")
        sys.exit(1)
    confirm = input(f"Delete profile '{profile_id}' and all its scores? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)
    scores, _ = db.delete_profile(profile_id)
    print(f"Deleted profile '{profile_id}' and {scores} score rows.")


def main():
    parser = argparse.ArgumentParser(
        description="create_profile.py — create, list, or delete search profiles"
    )
    parser.add_argument("--list", action="store_true", help="List all profiles in the DB")
    parser.add_argument("--delete", metavar="PROFILE_ID", help="Delete a profile and its scores")
    args = parser.parse_args()

    db = JobStorage(DB_PATH)

    if args.list:
        _cmd_list(db)
        return

    if args.delete:
        _cmd_delete(db, args.delete)
        return

    print("\n=== Create new search profile ===\n")

    # 1 — Profile ID
    while True:
        profile_id = input("Profile ID (slug, e.g. ch_senior_pm): ").strip()
        if re.fullmatch(r"[a-z0-9_]+", profile_id):
            break
        print("  ✗ Lowercase letters, digits, and underscores only. No spaces.")

    # 2 — Profile name
    profile_name = _prompt("Profile name (display name, e.g. Switzerland Senior PM)")
    if not profile_name:
        profile_name = profile_id

    # 3 — Work modes
    print("\nAllowed work modes:")
    allowed_work_modes = _multiselect("Select", WORK_MODES, default_all=True)

    # 4 — Geo zones
    print("\nAllowed geo zones:")
    allowed_geo_zones = _multiselect("Select", GEO_ZONES, default_all=True)

    # 5 — Company sizes
    print("\nAllowed company sizes (empty = no filter):")
    company_sizes = _multiselect("Select", COMPANY_SIZES, default_all=False)

    # 6 — Score threshold
    while True:
        raw = _prompt("Score threshold (1–10)", default="5")
        if raw.isdigit() and 1 <= int(raw) <= 10:
            score_threshold = int(raw)
            break
        print("  ✗ Enter a number between 1 and 10.")

    # 7 — Scoring context
    print("\nScoring context — profile-specific instructions for the LLM scorer.")
    scoring_context = _multiline("Scoring context")

    # 8 — Location keywords (pre-filter)
    print("\nLocation keywords for pre-filtering (comma-separated, e.g. switzerland,zürich,remote):")
    raw_loc = input("Location keywords (Enter to skip): ").strip()
    location_keywords = [k.strip() for k in raw_loc.split(",") if k.strip()] if raw_loc else []

    # Summary
    print("\n─── Summary ────────────────────────────────")
    print(f"  ID              : {profile_id}")
    print(f"  Name            : {profile_name}")
    print(f"  Work modes      : {', '.join(allowed_work_modes) or '(all)'}")
    print(f"  Geo zones       : {', '.join(allowed_geo_zones) or '(all)'}")
    print(f"  Company sizes   : {', '.join(company_sizes) or '(no filter)'}")
    print(f"  Score threshold : {score_threshold}")
    print(f"  Location kw     : {', '.join(location_keywords) or '(none)'}")
    print(f"  Scoring context : {scoring_context[:120]}{'…' if len(scoring_context) > 120 else ''}")
    print("────────────────────────────────────────────\n")

    confirm = input("Save? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted — nothing saved.")
        sys.exit(0)

    profile = SearchProfile(
        id=profile_id,
        name=profile_name,
        allowed_geo_zones=allowed_geo_zones,
        allowed_work_modes=allowed_work_modes,
        location_keywords=location_keywords,
        boost_keywords=[],
        company_sizes=company_sizes,
        score_threshold=score_threshold,
        scoring_context=scoring_context,
    )

    db.upsert_profile(profile)
    print(f"\n✅ Profile '{profile_id}' saved.")
    print(f"   Run: python score.py --profile {profile_id}")


if __name__ == "__main__":
    main()
