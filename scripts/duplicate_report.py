#!/usr/bin/env python3
"""
duplicate_report.py — Analyse les doublons dans la table jobs.

Un doublon = même entreprise + même titre (ou titre très similaire),
mais ID différent (URL différentes ou sources différentes).

Le script produit un rapport texte + CSV pour identifier les patterns
de duplication et orienter les améliorations du scraping/scoring.

Usage:
    python scripts/duplicate_report.py              # rapport complet
    python scripts/duplicate_report.py --json       # sortie JSON
    python scripts/duplicate_report.py --csv OUTDIR # export CSV
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "jobs.db"


# ── Normalisation des titres ──────────────────────────────────────────────

# Mots vides dans les titres de poste — on les supprime avant comparaison
_TITLE_STOP_WORDS = frozenset({
    "senior", "junior", "lead", "staff", "principal", "associate",
    "director", "manager", "head", "vp", "chief",
    "remote", "hybrid", "onsite", "on-site",
    "fulltime", "full-time", "part-time", "contract", "intern",
    "the", "a", "an", "&", "and", "or",
    "i", "ii", "iii", "iv", "v",
    "new", "experienced",
    # Comp / salary hints
    "bonus", "equity", "usd", "eur",
    # Unused but common
    "global", "emea", "apac", "us", "eu", "uk", "amer", "latam",
})

_TITLE_RE = re.compile(r"[^a-z0-9\s]", re.IGNORECASE)


def _norm_title(title: str) -> str:
    """Normalise un titre pour comparaison fuzzy.

    → lowercase
    → retire les parenthèses et crochets
    → retire les mots vides
    → trie les mots pour rendre les titres comparables même si l'ordre varie
    """
    if not title:
        return ""
    t = title.lower()
    # Retire le contenu entre parenthèses / crochets
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"\[[^]]*\]", " ", t)
    # Retire ponctuation
    t = _TITLE_RE.sub(" ", t)
    words = [w for w in t.split() if w not in _TITLE_STOP_WORDS and len(w) > 1]
    return " ".join(sorted(words))


def _sim(a: str, b: str) -> float:
    """SequenceMatcher ratio entre deux chaînes."""
    return SequenceMatcher(None, a, b).ratio()


# ── Analyse ───────────────────────────────────────────────────────────────

def analyze(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    total_companies = conn.execute(
        "SELECT COUNT(DISTINCT company) FROM jobs WHERE company IS NOT NULL"
    ).fetchone()[0]
    total_sources = conn.execute(
        "SELECT COUNT(DISTINCT source) FROM jobs"
    ).fetchone()[0]

    # 1. Récupère tous les jobs avec company + title non-null
    rows = conn.execute("""
        SELECT id, title, company, url, source, location, first_seen, last_seen
        FROM jobs
        WHERE company IS NOT NULL AND title IS NOT NULL AND company != ''
        ORDER BY company, title
    """).fetchall()

    # Index: company → list of jobs
    by_company = defaultdict(list)
    for r in rows:
        by_company[r["company"]].append(dict(r))

    print(f"Total jobs with company:   {len(rows)}")
    print(f"Distinct companies:        {len(by_company)}")
    print(f"Distinct sources:          {total_sources}")
    print()

    # ── Analyse par entreprise ──────────────────────────────────────

    # Niveau 1 : titre EXACT (insensible à la casse)
    exact_dup_pairs = []  # (job_a, job_b, company)
    # Niveau 2 : titre normalisé identique
    norm_dup_pairs = []
    # Niveau 3 : titre similaire (> 85%)
    fuzzy_dup_pairs = []

    companies_with_dups = set()
    company_dup_stats = defaultdict(lambda: {"total": 0, "exact": 0, "norm": 0, "fuzzy": 0, "unique": 0})
    source_pair_counter = Counter()  # (source_a, source_b) pour cross-source

    company_total_dups = Counter()  # company → total duplicate groups

    for company, jobs in by_company.items():
        if len(jobs) < 2:
            company_dup_stats[company]["total"] = len(jobs)
            company_dup_stats[company]["unique"] = len(jobs)
            continue

        # Regroupe par titre normalisé d'abord
        by_norm = defaultdict(list)
        for j in jobs:
            by_norm[_norm_title(j["title"])].append(j)

        # Compte les groupes avec > 1 élément
        dup_groups = {k: v for k, v in by_norm.items() if len(v) > 1 and k}
        company_dup_stats[company]["total"] = len(jobs)
        # unique ≈ titres normalisés distincts
        company_dup_stats[company]["unique"] = sum(
            1 for v in by_norm.values() if len(v) == 1
        ) + sum(len(v) for v in dup_groups.values())

        if not dup_groups:
            continue

        companies_with_dups.add(company)

        for norm_title, group in dup_groups.items():
            # Vérifie si c'est un exact match (case-insensitive)
            by_exact = defaultdict(list)
            for j in group:
                by_exact[j["title"].lower().strip()].append(j)

            for exact_title, exact_group in by_exact.items():
                if len(exact_group) >= 2:
                    company_dup_stats[company]["exact"] += len(exact_group)
                    for i in range(len(exact_group)):
                        for k in range(i + 1, len(exact_group)):
                            exact_dup_pairs.append((exact_group[i], exact_group[k], company))
                            src_pair = tuple(sorted([exact_group[i]["source"], exact_group[k]["source"]]))
                            source_pair_counter[src_pair] += 1

            # Si le groupe normalisé a plus de 1, mais pas d'exact match
            if len(group) >= 2 and not any(len(g) >= 2 for g in by_exact.values()):
                company_dup_stats[company]["norm"] += len(group)
                for i in range(len(group)):
                    for k in range(i + 1, len(group)):
                        norm_dup_pairs.append((group[i], group[k], company))
                        src_pair = tuple(sorted([group[i]["source"], group[k]["source"]]))
                        source_pair_counter[src_pair] += 1

        # Fuzzy : cherche des similarités entre titres normalisés DIFFÉRENTS
        # (ne le fait que pour les entreprises avec bcp de jobs pour éviter O(n²))
        if len(jobs) <= 200:
            norm_keys = list(by_norm.keys())
            for i in range(len(norm_keys)):
                for k in range(i + 1, len(norm_keys)):
                    if not norm_keys[i] or not norm_keys[k]:
                        continue
                    if _sim(norm_keys[i], norm_keys[k]) >= 0.85:
                        # Prend un représentant de chaque groupe
                        a, b = by_norm[norm_keys[i]][0], by_norm[norm_keys[k]][0]
                        fuzzy_dup_pairs.append((a, b, company))
                        company_dup_stats[company]["fuzzy"] += 1
                        src_pair = tuple(sorted([a["source"], b["source"]]))
                        source_pair_counter[src_pair] += 1

        # Compte les groupes dupliqués pour cette entreprise
        company_total_dups[company] = len(dup_groups)

    conn.close()

    return {
        "total_jobs": total_jobs,
        "total_companies": total_companies,
        "total_sources": total_sources,
        "exact_dup_pairs": exact_dup_pairs,
        "norm_dup_pairs": norm_dup_pairs,
        "fuzzy_dup_pairs": fuzzy_dup_pairs,
        "companies_with_dups": companies_with_dups,
        "company_dup_stats": dict(company_dup_stats),
        "company_total_dups": dict(company_total_dups),
        "source_pair_counter": dict(source_pair_counter),
        "by_company": {c: len(jobs) for c, jobs in by_company.items()},
    }


# ── Rapport ───────────────────────────────────────────────────────────────

def print_report(data: dict) -> None:
    exact = data["exact_dup_pairs"]
    norm = data["norm_dup_pairs"]
    fuzzy = data["fuzzy_dup_pairs"]

    print("=" * 72)
    print("  RAPPORT DE DUPLICATION — jobs.db")
    print("=" * 72)
    print(f"  Total jobs:               {data['total_jobs']:>6}")
    print(f"  Companies with jobs:      {data['total_companies']:>6}")
    print(f"  Distinct sources:         {data['total_sources']:>6}")
    print()

    # ── Résumé global ──
    total_duplicate_groups = sum(data["company_total_dups"].values())
    total_exact_pairs = len(exact)
    total_norm_pairs = len(norm)
    total_fuzzy_pairs = len(fuzzy)
    total_dup_jobs = total_exact_pairs * 2 + total_norm_pairs * 2 + total_fuzzy_pairs * 2  # rough

    print("─" * 72)
    print("  RÉSUMÉ GLOBAL")
    print("─" * 72)
    print(f"  Groupes de duplication:               {total_duplicate_groups:>6}")
    print(f"  Paires exact-title:                    {total_exact_pairs:>6}")
    print(f"  Paires norm-title (hors exact):        {total_norm_pairs:>6}")
    print(f"  Paires fuzzy (≥85% sim, hors norm):    {total_fuzzy_pairs:>6}")
    print()

    # ── Top 20 entreprises avec le plus de doublons ──
    print("─" * 72)
    print("  TOP 20 ENTREPRISES — groupes de doublons")
    print("─" * 72)
    ranked = sorted(data["company_total_dups"].items(), key=lambda x: -x[1])
    for rank, (company, n_dup_groups) in enumerate(ranked[:20], 1):
        stats = data["company_dup_stats"].get(company, {})
        total = stats.get("total", 0)
        unique = stats.get("unique", 0)
        print(f"  {rank:>2}. {company[:45]:<45}  {n_dup_groups:>4} groupes  "
              f"{total:>4} jobs → ~{unique:>4} uniques estimés")
    print()

    # ── Cross-source duplication ──
    print("─" * 72)
    print("  PAIRES DE SOURCES générant le plus de doublons exact-title")
    print("─" * 72)
    top_sources = sorted(data["source_pair_counter"].items(), key=lambda x: -x[1])[:20]
    for (s1, s2), count in top_sources:
        print(f"  {s1:<25} ↔ {s2:<25}  {count:>5} doublons")
    print()

    # ── Exemples de doublons ──
    print("─" * 72)
    print("  EXEMPLES DE DOUBLONS EXACT-TITLE (max 15)")
    print("─" * 72)
    for i, (a, b, company) in enumerate(exact[:15], 1):
        print(f"  [{i}] {company}")
        print(f"      {a['title'][:70]}")
        print(f"      src={a['source']}  url={a['url'][:60]}")
        print(f"      src={b['source']}  url={b['url'][:60]}")
        print()

    if norm:
        print("─" * 72)
        print("  EXEMPLES DE DOUBLONS NORM-TITLE (max 10)")
        print("─" * 72)
        for i, (a, b, company) in enumerate(norm[:10], 1):
            print(f"  [{i}] {company}")
            print(f"      A: {a['title'][:70]}")
            print(f"      B: {b['title'][:70]}")
            print()

    if fuzzy:
        print("─" * 72)
        print("  EXEMPLES DE DOUBLONS FUZZY (max 10)")
        print("─" * 72)
        for i, (a, b, company) in enumerate(fuzzy[:10], 1):
            sim = _sim(_norm_title(a["title"]), _norm_title(b["title"]))
            print(f"  [{i}] {company}  (sim={sim:.0%})")
            print(f"      A: {a['title'][:70]}")
            print(f"      B: {b['title'][:70]}")
            print()

    # ── Distribution par entreprise ──
    print("─" * 72)
    print("  DISTRIBUTION — Nombre d'entreprises par # de doublons")
    print("─" * 72)
    dist = Counter(data["company_total_dups"].values())
    for n_dups in sorted(dist):
        label = f"{n_dups} doublon(s)"
        print(f"  {label:<20} {dist[n_dups]:>5} entreprises")
    print()

    no_dups = data["total_companies"] - len(data["companies_with_dups"])
    print(f"  Entreprises sans doublon:  {no_dups}")
    print()


def export_csv(data: dict, outdir: str):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    # CSV 1 : exact duplicates
    with open(out / "duplicates_exact.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company", "title", "url_a", "source_a", "url_b", "source_b", "id_a", "id_b"])
        for a, b, company in data["exact_dup_pairs"]:
            w.writerow([company, a["title"], a["url"], a["source"], b["url"], b["source"], a["id"], b["id"]])

    # CSV 2 : norm duplicates
    with open(out / "duplicates_norm.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company", "title_a", "title_b", "url_a", "source_a", "url_b", "source_b"])
        for a, b, company in data["norm_dup_pairs"]:
            w.writerow([company, a["title"], b["title"], a["url"], a["source"], b["url"], b["source"]])

    # CSV 3 : company summary
    with open(out / "duplicates_by_company.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["company", "total_jobs", "duplicate_groups", "exact_dup_jobs", "norm_dup_jobs",
                     "fuzzy_dup_jobs", "est_unique_jobs"])
        for company in sorted(data["company_dup_stats"]):
            stats = data["company_dup_stats"][company]
            n_dup_groups = data["company_total_dups"].get(company, 0)
            w.writerow([
                company, stats["total"], n_dup_groups,
                stats["exact"], stats["norm"], stats["fuzzy"],
                stats["unique"],
            ])

    # CSV 4 : cross-source pairs
    with open(out / "duplicates_cross_source.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_a", "source_b", "duplicate_count"])
        for (s1, s2), count in sorted(data["source_pair_counter"].items(), key=lambda x: -x[1]):
            w.writerow([s1, s2, count])

    print(f"CSV exportés dans {out}/")
    for fn in sorted(out.iterdir()):
        print(f"  {fn.name}  ({fn.stat().st_size:,} bytes)")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Duplicate job report")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to jobs.db")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--csv", metavar="OUTDIR", help="Export CSVs to directory")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: DB not found at {args.db}", file=sys.stderr)
        sys.exit(1)

    data = analyze(args.db)

    if args.json:
        # Clean up non-serializable stuff
        out = {
            "total_jobs": data["total_jobs"],
            "total_companies": data["total_companies"],
            "total_sources": data["total_sources"],
            "companies_with_dups": len(data["companies_with_dups"]),
            "exact_dup_pairs": len(data["exact_dup_pairs"]),
            "norm_dup_pairs": len(data["norm_dup_pairs"]),
            "fuzzy_dup_pairs": len(data["fuzzy_dup_pairs"]),
            "top_companies": sorted(data["company_total_dups"].items(), key=lambda x: -x[1])[:30],
            "top_source_pairs": sorted(data["source_pair_counter"].items(), key=lambda x: -x[1])[:20],
            "exact_examples": [
                {"company": c, "title": a["title"], "sources": [a["source"], b["source"]],
                 "urls": [a["url"], b["url"]]}
                for a, b, c in data["exact_dup_pairs"][:20]
            ],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    elif args.csv:
        export_csv(data, args.csv)
        print_report(data)
    else:
        print_report(data)


if __name__ == "__main__":
    main()
