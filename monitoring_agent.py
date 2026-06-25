"""
monitoring_agent.py — Semi-autonomous agent that acts on researcher results.

Routes watch_pending companies (with researched_at set) through a deterministic
decision model driven by scraping_method.  Applies simple config changes directly
to main; generates SpecKit specs + git branches for cases requiring new code.

See spec 006 for the full decision model.

Updated for spec 006-pre: Actions A and B no longer edit scraper config files.
The scrapers now read target slugs from the DB via get_watching_companies_by_method().
The agent just transitions companies to watch_ready.
"""

import argparse
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from paths import DB_PATH
from storage import JobStorage

# ---------------------------------------------------------------------------
# Config file locations (Phase 1 knowledge)
# ---------------------------------------------------------------------------

GREENHOUSE_CONFIG_PATH = "scrapers/greenhouse.py"
GREENHOUSE_LIST_NAME = "GREENHOUSE_BOARDS_SEED"

ATS_CONFIG_PATHS = {
    "lever":    ("scrapers/ats/lever.py",    "LEVER_SLUGS_SEED"),
    "workable": ("scrapers/ats/workable.py", "WORKABLE_SLUGS_SEED"),
}

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_pending_researched(db: JobStorage) -> list[dict]:
    """Companies with monitoring_status='watch_pending' AND researched_at IS NOT NULL."""
    return [
        c for c in db.get_watch_pending_companies()
        if c.get("researched_at")
    ]


def _decide_action(company: dict) -> str:
    method = (company.get("scraping_method") or "").strip().lower()
    action_map = {
        "greenhouse": "A",
        "lever":      "B",
        "workable":   "B",
        "ashby":      "C",
        "teamtailor": "C",
        "recruitee":  "C",
        "bamboohr":   "C",
        "smartrecruiters": "C",
        "custom_html": "D",
        "jobspy":     "E",
        "none":       "F",
        "manual":     "F",
    }
    return action_map.get(method, "F")


def _slug_from_company(company: dict) -> str:
    slug = (company.get("ats_board_slug") or
            company.get("ats_identifier") or
            (company.get("name") or "").lower().replace(" ", "-"))
    return slug


def _run_export_seed(dry_run: bool) -> None:
    if dry_run:
        print("  [dry-run] Would run export_seed.py")
        return
    subprocess.run([sys.executable, "export_seed.py"], check=True, cwd=ROOT)


# ---------------------------------------------------------------------------
# Action A — Greenhouse
# ---------------------------------------------------------------------------

def _action_a_greenhouse(company: dict, db: JobStorage, dry_run: bool) -> str:
    """After spec 006-pre: scrapers read from DB via get_greenhouse_boards().
    No file editing needed — just transition to watch_ready."""
    slug = _slug_from_company(company)
    name = company["name"]

    if dry_run:
        return f"✅ would set watch_ready ({slug})"

    db.set_monitoring_status(company["id"], "watch_ready")
    return f"✅ watch_ready ({slug})"


# ---------------------------------------------------------------------------
# Action B — Lever / Workable
# ---------------------------------------------------------------------------

def _action_b_ats(company: dict, provider: str, db: JobStorage,
                   dry_run: bool) -> str:
    """After spec 006-pre: scrapers read from DB via get_lever_slugs() /
    get_workable_slugs(). No file editing needed — just transition to watch_ready."""
    slug = _slug_from_company(company)
    name = company["name"]

    if dry_run:
        return f"✅ would set watch_ready ({slug})"

    db.set_monitoring_status(company["id"], "watch_ready")
    return f"✅ watch_ready ({slug})"


# ---------------------------------------------------------------------------
# Action C — New ATS scraper
# ---------------------------------------------------------------------------

def _next_spec_number() -> str:
    existing = []
    specs_dir = ROOT / "specs"
    if specs_dir.exists():
        for d in specs_dir.iterdir():
            m = re.match(r"^(\d+)", d.name)
            if m:
                existing.append(int(m.group(1)))
    return f"{max(existing) + 1:03d}" if existing else "007"


def _action_c_new_ats(company: dict, db: JobStorage, dry_run: bool) -> str:
    provider = company.get("ats_provider") or "unknown"
    slug = _slug_from_company(company)
    name = company["name"]
    spec_num = _next_spec_number()
    branch = f"scraper/{provider}-{slug}"

    if dry_run:
        return f"📋 branch: {branch} (spec {spec_num})"

    # Check if a spec for this ATS already exists
    specs_dir = ROOT / "specs"
    existing_spec = None
    if specs_dir.exists():
        for d in specs_dir.iterdir():
            if d.name.endswith(f"-{provider}-scraper"):
                existing_spec = d.name
                break

    if existing_spec:
        # Add slug to existing spec's board list
        spec_path = specs_dir / existing_spec / "spec.md"
        if spec_path.exists():
            content = spec_path.read_text()
            if slug not in content:
                content += f"\n- `{slug}` — {name}\n"
                spec_path.write_text(content)
    else:
        spec_dir = specs_dir / f"{spec_num}-{provider}-scraper"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (_write_ats_spec(provider, name, slug, spec_num, spec_dir))

    # Create branch
    subprocess.run(["git", "checkout", "-b", branch], check=True, cwd=ROOT)

    # Write stub
    _write_scraper_stub(provider, f"scrapers/ats/{provider}.py")

    subprocess.run(["git", "add", "."], check=True, cwd=ROOT)
    subprocess.run(
        ["git", "commit", "-m",
         f"scraper: scaffold {provider} ATS scraper for {name} ({slug})"],
        check=True, cwd=ROOT,
    )
    db.set_monitoring_status(company["id"], "watch_pending")
    return f"📋 branch: {branch} (spec {spec_num})"


def _write_ats_spec(provider: str, name: str, slug: str,
                     spec_num: str, spec_dir: Path) -> None:
    spec_md = textwrap.dedent(f"""\
    # Spec {spec_num} — {provider.title()} Scraper

    ## Goal
    Build `scrapers/ats/{provider}.py` — a BaseScraper for {provider.title()} ATS.

    Initial board: **{name}** (`{slug}`).

    ## API
    TODO — document the {provider} jobs API endpoint, auth requirements,
    response format, and pagination.

    ## Files to create
    - `scrapers/ats/{provider}.py`
    """)
    (spec_dir / "spec.md").write_text(spec_md)
    (spec_dir / "plan.md").write_text(f"# Plan {spec_num} — TODO\n")
    (spec_dir / "tasks.md").write_text(f"# Tasks {spec_num} — TODO\n")


def _write_scraper_stub(provider: str, rel_path: str) -> None:
    path = ROOT / rel_path
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    provider_title = provider.title()
    stub = (
        f'"""{provider_title} ATS scraper — TODO: implement fetch logic.\n'
        f'\n'
        f'Reference: scrapers/ats/lever.py for the pattern.\n'
        f'"""\n'
        f'\n'
        f'import time\n'
        f'from datetime import datetime\n'
        f'\n'
        f'import httpx\n'
        f'\n'
        f'from scrapers.base import BaseScraper\n'
        f'from models import JobPosting\n'
        f'\n'
        f'\n'
        f'class {provider_title}Scraper(BaseScraper):\n'
        f'    SOURCE_NAME = "{provider_title}"\n'
        f'    ENABLED = True\n'
        f'\n'
        f'    # TODO: define DEFAULT_SLUGS with company board identifiers\n'
        f'    DEFAULT_SLUGS: list[str] = []\n'
        f'\n'
        f'    def fetch(self, job_filter=None):\n'
        f'        # TODO: HTTP fetch → parse → map to JobPosting\n'
        f'        raise NotImplementedError("fetch() not yet implemented")\n'
    )
    path.write_text(stub)


# ---------------------------------------------------------------------------
# Action D — Custom HTML scraper
# ---------------------------------------------------------------------------

def _action_d_custom(company: dict, db: JobStorage, dry_run: bool) -> str:
    name = company["name"]
    slug = _slug_from_company(company)
    spec_num = _next_spec_number()
    branch = f"scraper/custom-{slug}"

    if dry_run:
        return f"📋 branch: {branch} (spec {spec_num})"

    spec_dir = ROOT / "specs" / f"{spec_num}-custom-{slug}"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_md = textwrap.dedent(f"""\
    # Spec {spec_num} — Custom Scraper for {name}

    ## Goal
    Build a bespoke scraper for {name} career page.

    URL: {company.get('careers_url', 'TODO')}
    ATS: {company.get('ats_provider', 'unknown')}
    """)
    (spec_dir / "spec.md").write_text(spec_md)
    (spec_dir / "plan.md").write_text(f"# Plan {spec_num} — TODO\n")
    (spec_dir / "tasks.md").write_text(f"# Tasks {spec_num} — TODO\n")

    subprocess.run(["git", "checkout", "-b", branch], check=True, cwd=ROOT)
    _write_scraper_stub("custom", f"scrapers/company_sites/{slug}.py")

    subprocess.run(["git", "add", "."], check=True, cwd=ROOT)
    subprocess.run(
        ["git", "commit", "-m",
         f"scraper: scaffold custom scraper for {name} ({slug})"],
        check=True, cwd=ROOT,
    )
    db.set_monitoring_status(company["id"], "watch_pending")
    return f"📋 branch: {branch} (spec {spec_num})"


# ---------------------------------------------------------------------------
# Action E — JobSpy coverage
# ---------------------------------------------------------------------------

def _action_e_jobspy(company: dict, db: JobStorage, dry_run: bool) -> str:
    if dry_run:
        return "✅ would mark watch_ready (JobSpy)"

    db.set_monitoring_status(company["id"], "watch_ready")
    return "✅ watch_ready (JobSpy)"


# ---------------------------------------------------------------------------
# Action F — No viable monitoring path
# ---------------------------------------------------------------------------

def _action_f_manual(company: dict, db: JobStorage, dry_run: bool) -> str:
    """No viable monitoring path — mark as watch_unsuitable so it doesn't
    clutter the watch_pending queue on future runs."""
    if dry_run:
        return "⚠️ no solution → watch_unsuitable"
    db.set_monitoring_status(company["id"], "watch_unsuitable")
    return "⚠️ watch_unsuitable"


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

ACTION_LABELS = {
    "A": "A — add slug (Greenhouse)",
    "B": "B — add slug (Lever/Workable)",
    "C": "C — new ATS scraper",
    "D": "D — custom scraper",
    "E": "E — JobSpy coverage",
    "F": "F — watch_unsuitable",
}


def run(db: JobStorage, *, apply_all: bool = False,
        apply_simple: bool = False, dry_run: bool = True,
        company_filter: str | None = None) -> None:

    companies = _load_pending_researched(db)
    if company_filter:
        companies = [c for c in companies
                     if company_filter.lower() in c["name"].lower()]
        if not companies:
            print(f"No pending researched company matching '{company_filter}'.")
            return

    if not companies:
        print("No pending researched companies — nothing to do.")
        return

    header = f"{'Company':<25} {'Method':<14} {'Action':<28} {'Result'}"
    print(header)
    print("-" * len(header))

    simple_ok = 0
    branches = 0
    manual = 0

    for company in companies:
        method = company.get("scraping_method") or "none"
        if method == "existing_data":
            print(f"{company['name']:<25} {method:<14} {'—':<28} "
                  f"✅ already watch_ready")
            continue

        action = _decide_action(company)
        label = ACTION_LABELS.get(action, f"? — {action}")

        if action == "A":
            result = _action_a_greenhouse(company, db, dry_run)
            simple_ok += 1 if "✅" in result else 0
        elif action == "B":
            provider = company.get("ats_provider", "lever")
            result = _action_b_ats(company, provider, db, dry_run)
            simple_ok += 1 if "✅" in result else 0
        elif action == "C":
            if apply_all:
                result = _action_c_new_ats(company, db, dry_run)
            else:
                result = "📋 needs --apply-all"
            branches += 1 if "📋" in result else 0
        elif action == "D":
            if apply_all:
                result = _action_d_custom(company, db, dry_run)
            else:
                result = "📋 needs --apply-all"
            branches += 1 if "📋" in result else 0
        elif action == "E":
            if apply_simple or apply_all:
                result = _action_e_jobspy(company, db, dry_run)
            else:
                result = "✅ needs --apply-simple"
            simple_ok += 1 if "✅" in result else 0
        else:  # F
            if apply_simple or apply_all:
                result = _action_f_manual(company, db, dry_run)
            else:
                result = "⚠️ needs --apply-simple"
            manual += 1

        print(f"{company['name']:<25} {method:<14} {label:<28} {result}")

    print()
    print(f"Summary: {simple_ok} committed, {branches} branches, {manual} manual")

    if apply_simple or apply_all:
        if not dry_run:
            _run_export_seed(dry_run=False)
    elif not dry_run:
        print("[dry-run mode] export_seed.py skipped — use --apply-simple/--apply-all")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="monitoring_agent — act on researcher results")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Show what would happen (default)")
    parser.add_argument("--apply-simple", action="store_true",
                        help="Apply Actions A, B, E, F only (no new code)")
    parser.add_argument("--apply-all", action="store_true",
                        help="Apply all actions including spec + branch generation")
    parser.add_argument("--company", default=None,
                        help="Act on a single company (name substring match)")
    args = parser.parse_args()

    if args.apply_simple or args.apply_all:
        args.dry_run = False

    if args.apply_simple and args.apply_all:
        print("--apply-simple and --apply-all are mutually exclusive.")
        sys.exit(1)

    db = JobStorage(DB_PATH)
    run(db, apply_all=args.apply_all, apply_simple=args.apply_simple,
        dry_run=args.dry_run, company_filter=args.company)


if __name__ == "__main__":
    main()
