"""
export_jobs.py — Export CSV des candidatures / activité (spec 030)

Usage:
    python export_jobs.py                        # mois courant, Applications
    python export_jobs.py --activity             # rapport Activité
    python export_jobs.py --month 2026-05
    python export_jobs.py --from 2026-05-01 --to 2026-05-31
    python export_jobs.py --stage Interviewing   # filtre « Current stage »

Output: CSV dans data/applications_YYYY-MM.csv ou data/activity_YYYY-MM.csv
(utf-8-sig, compatible Excel/LibreOffice)
"""

import argparse
import csv
import io
import sys
from calendar import monthrange
from collections import Counter
from datetime import date
from pathlib import Path

from storage import JobStorage
from paths import DATA_DIR, DB_PATH

OUTPUT_DIR = Path(DATA_DIR)

# Coarse derived stages (FR-017 « Current stage » filter).
CURRENT_STAGES = ["Applied", "Interviewing", "Offer", "Rejected", "Withdrawn"]


def _require_db() -> JobStorage:
    """Return a JobStorage for the live DB, raising FileNotFoundError if absent."""
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(DB_PATH)
    return JobStorage(DB_PATH)


def build_application_rows(date_from: str, date_to: str, stage: str | None = None) -> list[dict]:
    """Applications whose application date falls in [date_from, date_to] (FR-016)."""
    return _require_db().build_application_rows(date_from, date_to, current_stage=stage)


def build_activity_rows(date_from: str, date_to: str) -> list[dict]:
    """Every lifecycle event dated in [date_from, date_to] (FR-020)."""
    return _require_db().build_activity_rows(date_from, date_to)


def application_date_missing() -> list[dict]:
    """Lifecycle jobs with no application date (FR-021)."""
    return _require_db().application_date_missing()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export CSV des candidatures / activité (formulaire 716.007)"
    )
    parser.add_argument("--month", help="Mois au format YYYY-MM (ex: 2026-05)")
    parser.add_argument("--from", dest="date_from", help="Date de début YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="Date de fin YYYY-MM-DD")
    parser.add_argument(
        "--stage",
        choices=CURRENT_STAGES,
        default=None,
        help="Filtre « Current stage » (défaut: tous les stages)",
    )
    parser.add_argument(
        "--activity",
        action="store_true",
        help="Rapport Activité au lieu d'Applications",
    )
    return parser.parse_args()


def resolve_date_range(args):
    """Détermine la plage de dates à partir des arguments.

    Priorité : --from/--to > --month > mois courant.
    Affiche un avertissement si --month et --from/--to sont tous deux fournis.
    """
    has_from_to = bool(args.date_from and args.date_to)
    has_month = bool(args.month)

    if has_from_to:
        if has_month:
            print("[WARN] --from/--to prend priorité sur --month", file=sys.stderr)
        return args.date_from, args.date_to

    if has_month:
        year, month = map(int, args.month.split("-"))
    else:
        today = date.today()
        year, month = today.year, today.month

    last_day = monthrange(year, month)[1]
    date_from = f"{year:04d}-{month:02d}-01"
    date_to = f"{year:04d}-{month:02d}-{last_day:02d}"
    return date_from, date_to


def rows_to_csv_bytes(rows: list[dict]) -> bytes:
    """Sérialise les lignes en octets CSV encodés utf-8-sig (BOM inclus).

    Retourne b"" si rows est vide (jamais de CSV vide).
    """
    if not rows:
        return b""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def print_summary(rows: list[dict], *, by: str = "Current stage"):
    """Affiche le résumé par colonne clé et un aperçu dans le terminal."""
    if not rows:
        print("[INFO] Aucune entrée trouvée pour cette période.")
        return

    counts = Counter(r.get(by, "?") for r in rows)
    print(f"\n--- Résumé par {by} ---")
    for label, count in counts.items():
        print(f"  {label}: {count}")

    print(f"\n--- Aperçu ---")
    for r in rows:
        date_str = r.get("Application date") or r.get("Event date") or ""
        stage = r.get("Current stage") or r.get("Event") or ""
        company = (r.get("Company") or r.get("Company / location") or "")[:40]
        print(f"  {date_str:<10}  {company:<40}  [{stage}]")


def main():
    args = parse_args()
    date_from, date_to = resolve_date_range(args)

    print(f"Période : {date_from} → {date_to}")
    if args.activity:
        print("Rapport : Activité")
    else:
        print(f"Rapport : Applications (stage: {args.stage or 'tous'})")

    try:
        if args.activity:
            rows = build_activity_rows(date_from, date_to)
            by = "Event"
        else:
            rows = build_application_rows(date_from, date_to, stage=args.stage)
            by = "Current stage"
    except FileNotFoundError:
        print(f"[ERROR] DB introuvable : {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    month_label = date_from[:7]  # YYYY-MM
    prefix = "activity" if args.activity else "applications"
    output_path = OUTPUT_DIR / f"{prefix}_{month_label}.csv"

    if rows:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(rows_to_csv_bytes(rows))
        print(f"[OK] {len(rows)} entrée(s) exportée(s) → {output_path}")

    print_summary(rows, by=by)


if __name__ == "__main__":
    main()
