"""
export_jobs.py — Export CSV des candidatures (formulaire 716.007)

Usage:
    python export_jobs.py                          # mois courant, statut=applied
    python export_jobs.py --month 2026-05          # mois spécifique
    python export_jobs.py --from 2026-05-01 --to 2026-05-31
    python export_jobs.py --statuses applied rejected archived

Output: CSV dans data/jobs_YYYY-MM.csv (utf-8-sig, compatible Excel/LibreOffice)
"""

import argparse
import csv
import io
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

# Mapping statut DB → résultat du formulaire 716.007
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
        description="Export CSV des candidatures (formulaire 716.007)"
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


def build_export_rows(db_path, date_from: str, date_to: str, statuses: list[str]) -> list[dict]:
    """Construit les lignes d'export (7 colonnes) à partir de la DB.

    Joint jobs + job_tracking, filtre `status IN statuses` et
    `date(jt.changed_at) BETWEEN date_from AND date_to`, puis formate chaque
    ligne (mapping statut→résultat, entreprise+lieu, suffixe work_mode).

    Lève FileNotFoundError si db_path n'existe pas (jamais sys.exit — l'appelant
    décide du comportement CLI vs UI).
    """
    if not Path(db_path).exists():
        raise FileNotFoundError(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    placeholders = ",".join("?" * len(statuses))
    query = f"""
        SELECT
            j.id,
            j.title,
            j.company,
            j.location,
            j.url,
            j.source,
            j.work_mode,
            jt.status,
            date(jt.changed_at) AS status_date
        FROM jobs j
        JOIN job_tracking jt ON j.id = jt.job_id
        WHERE jt.status IN ({placeholders})
          AND date(jt.changed_at) BETWEEN ? AND ?
        ORDER BY jt.changed_at ASC
    """
    raw_rows = conn.execute(query, [*statuses, date_from, date_to]).fetchall()
    conn.close()

    out = []
    for job in (dict(r) for r in raw_rows):
        # Date au format JJ/MM/AAAA
        raw_date = job.get("status_date") or ""
        try:
            d = date.fromisoformat(raw_date)
            date_str = d.strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            date_str = ""

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

        # Statut brut du job
        status = job.get("status") or ""

        out.append({
            "Date": date_str,
            "Entreprise / Adresse": entreprise_adresse,
            "Description du poste": description,
            "Status": status,
            "URL": job.get("url") or "",
            "ID": job.get("id"),
        })
    return out


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


def print_summary(rows: list[dict]):
    """Affiche le résumé par statut et l'aperçu dans le terminal."""
    if not rows:
        print("[INFO] Aucune entrée trouvée pour cette période et ces statuts.")
        return

    # Résumé par statut DB
    status_counts = Counter(r.get("Status", "?") for r in rows)
    print(f"\n--- Résumé par statut ---")
    for label, count in status_counts.items():
        print(f"  {label}: {count}")

    # Aperçu
    print(f"\n--- Aperçu ---")
    for r in rows:
        date_str = r["Date"]
        ent = r["Entreprise / Adresse"][:40]
        res = r["Status"]
        print(f"  {date_str:<10}  {ent:<40}  [{res}]")


def main():
    args = parse_args()
    date_from, date_to = resolve_date_range(args)

    print(f"Période : {date_from} → {date_to}")
    print(f"Statuts  : {args.statuses}")

    try:
        rows = build_export_rows(DB_PATH, date_from, date_to, args.statuses)
    except FileNotFoundError:
        print(f"[ERROR] DB introuvable : {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    month_label = date_from[:7]  # YYYY-MM
    output_path = OUTPUT_DIR / f"jobs_{month_label}.csv"

    if rows:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(rows_to_csv_bytes(rows))
        print(f"[OK] {len(rows)} entrée(s) exportée(s) → {output_path}")

    print_summary(rows)


if __name__ == "__main__":
    main()
