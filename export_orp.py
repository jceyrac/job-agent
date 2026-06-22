"""
export_orp.py — Export des recherches d'emploi pour le formulaire ORP suisse

Usage:
    python export_orp.py                          # mois courant, statut=applied
    python export_orp.py --month 2026-05          # mois spécifique
    python export_orp.py --from 2026-05-01 --to 2026-05-31
    python export_orp.py --statuses applied rejected archived

Output: CSV dans data/orp_YYYY-MM.csv (utf-8-sig, compatible Excel/LibreOffice)
"""

import argparse
import csv
import sqlite3
import sys
from calendar import monthrange
from collections import Counter
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "jobs.db"
OUTPUT_DIR = Path(__file__).parent / "data"

STATUSES_DEFAULT = ["applied"]
STATUSES_ALL = ["new", "queued", "ready", "applied", "rejected", "archived", "expired"]

# Mapping statut DB → résultat formulaire ORP
STATUS_TO_RESULTAT = {
    "applied":  "en suspens",
    "queued":   "en suspens",
    "ready":    "en suspens",
    "saved":    "en suspens",
    "new":      "en suspens",
    "rejected": "négatif",
    "archived": "négatif",
    "expired":  "négatif",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export recherches d'emploi pour le formulaire ORP suisse"
    )
    parser.add_argument("--month", help="Mois au format YYYY-MM (ex: 2026-05)")
    parser.add_argument("--from", dest="date_from", help="Date de début YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="Date de fin YYYY-MM-DD")
    parser.add_argument(
        "--statuses",
        nargs="+",
        choices=STATUSES_ALL,
        default=STATUSES_DEFAULT,
        help=f"Statuts à inclure (défaut: {STATUSES_DEFAULT})",
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


def fetch_jobs(db_path: Path, date_from: str, date_to: str, statuses: list[str]):
    """Interroge la DB : jobs JOIN job_tracking, filtré par statut et date.

    Utilise job_tracking.changed_at (pas updated_at — corrigé du script existant).
    """
    if not db_path.exists():
        print(f"[ERROR] DB introuvable : {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    placeholders = ",".join("?" * len(statuses))
    query = f"""
        SELECT
            j.title,
            j.company,
            j.location,
            j.url,
            j.source,
            j.work_mode,
            jt.status,
            date(jt.changed_at) AS status_date,
            jt.notes
        FROM jobs j
        JOIN job_tracking jt ON j.id = jt.job_id
        WHERE jt.status IN ({placeholders})
          AND date(jt.changed_at) BETWEEN ? AND ?
        ORDER BY jt.changed_at ASC
    """
    rows = conn.execute(query, [*statuses, date_from, date_to]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_for_orp(jobs: list[dict]) -> list[dict]:
    """Formate les données pour correspondre aux colonnes du formulaire ORP 716.007.

    Colonnes (10) :
      Jour | Mois | Entreprise / Adresse | Personne contactée / Tél. |
      Description du poste | Assignation ORP | Activité | Résultat |
      Motif si négatif | URL
    """
    out = []
    for job in jobs:
        # Date au format jour/mois
        raw_date = job.get("status_date") or ""
        try:
            d = date.fromisoformat(raw_date)
            day = str(d.day)
            month = str(d.month)
        except (ValueError, TypeError):
            day, month = "", ""

        # Entreprise + localisation
        company = job.get("company") or ""
        location = job.get("location") or ""
        entreprise_adresse = f"{company} — {location}" if location else company

        # Titre + work_mode
        title = job.get("title") or ""
        work_mode = job.get("work_mode") or ""
        if work_mode and work_mode != "unknown":
            description = f"{title} ({work_mode})"
        else:
            description = title

        # Résultat mappé depuis le statut
        status = job.get("status") or ""
        resultat = STATUS_TO_RESULTAT.get(status, "en suspens")

        # Motif : notes uniquement pour les résultats négatifs
        is_negatif = resultat == "négatif"
        motif = (job.get("notes") or "") if is_negatif else ""

        out.append({
            "Jour": day,
            "Mois": month,
            "Entreprise / Adresse": entreprise_adresse,
            "Personne contactée / Tél.": "",
            "Description du poste": description,
            "Assignation ORP": "Non",
            "Activité": "par lettre / électronique",
            "Résultat": resultat,
            "Motif si négatif": motif,
            "URL": job.get("url") or "",
        })
    return out


def write_csv(rows: list[dict], output_path: Path):
    """Écrit le CSV en utf-8-sig (BOM pour compatibilité Excel/LibreOffice).

    Ne crée pas de fichier si rows est vide (pas de CSV vide).
    """
    if not rows:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OK] {len(rows)} entrée(s) exportée(s) → {output_path}")


def print_summary(rows: list[dict]):
    """Affiche le résumé par statut et l'aperçu dans le terminal."""
    if not rows:
        print("[INFO] Aucune entrée trouvée pour cette période et ces statuts.")
        return

    # Résumé par statut DB
    status_counts = Counter(r.get("Résultat", "?") for r in rows)
    print(f"\n--- Résumé par statut ---")
    for label, count in status_counts.items():
        print(f"  {label}: {count}")

    # Aperçu
    print(f"\n--- Aperçu ---")
    for r in rows:
        jour = r["Jour"]
        mois = r["Mois"]
        ent = r["Entreprise / Adresse"][:40]
        res = r["Résultat"]
        print(f"  {jour:>2}/{mois:>2}  {ent:<40}  [{res}]")


def main():
    args = parse_args()
    date_from, date_to = resolve_date_range(args)

    print(f"Période : {date_from} → {date_to}")
    print(f"Statuts  : {args.statuses}")

    jobs = fetch_jobs(DB_PATH, date_from, date_to, args.statuses)
    rows = format_for_orp(jobs)

    month_label = date_from[:7]  # YYYY-MM
    output_path = OUTPUT_DIR / f"orp_{month_label}.csv"

    write_csv(rows, output_path)
    print_summary(rows)


if __name__ == "__main__":
    main()
