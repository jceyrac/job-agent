#!/usr/bin/env python3
"""Diagnostic : le pipeline rate-t-il des offres freelance ?

À lancer sur la prod (HPE) :
    python3 scripts/diag_freelance.py [chemin/vers/jobs.db]

Lecture seule. Ne modifie rien.
"""
import sqlite3
import sys
from collections import defaultdict

db_path = sys.argv[1] if len(sys.argv) > 1 else "data/jobs.db"
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
q = conn.execute

print(f"DB : {db_path}")
print(f"Total jobs : {q('select count(*) from jobs').fetchone()[0]}")
print(f"Dernier first_seen : {q('select max(first_seen) from jobs').fetchone()[0]}\n")

# ── 1. Répartition contract_type par source (30 derniers jours) ──────────────
print("=" * 72)
print("1. RÉPARTITION contract_type PAR SOURCE (30 derniers jours)")
print("=" * 72)
rows = q("""
    SELECT j.source,
           COALESCE(NULLIF(TRIM(j.contract_type), ''), 'null') AS ct,
           COUNT(*)
    FROM jobs j
    LEFT JOIN job_scores s ON s.job_id = j.id
    WHERE date(j.first_seen) >= date('now', '-30 days')
    GROUP BY 1, 2
""").fetchall()

by_source = defaultdict(dict)
for src, ct, n in rows:
    by_source[src][ct] = n

types = sorted({ct for d in by_source.values() for ct in d})
print(f"{'source':<20}" + "".join(f"{t:>13}" for t in types) + f"{'TOTAL':>8}")
for src in sorted(by_source, key=lambda s: -sum(by_source[s].values())):
    d = by_source[src]
    print(f"{src:<20}" + "".join(f"{d.get(t, 0):>13}" for t in types)
          + f"{sum(d.values()):>8}")

# ── 2. Le plafond results_wanted mord-il ? ───────────────────────────────────
print("\n" + "=" * 72)
print("2. NOUVEAUX JOBS PAR JOUR (Indeed/LinkedIn)")
print("=" * 72)
print("⚠️ Ce comptage ne mesure PAS le plafond results_wanted : il compte les")
print("   jobs NOUVEAUX (first_seen), pas les jobs RENVOYÉS par le scraper.")
print("   Avec un run quotidien la plupart des jobs renvoyés sont déjà connus.")
print("   Pour tester le plafond, lire les logs du scraper, pas la DB.\n")
for src in ("Indeed", "LinkedIn"):
    rows = q("""
        SELECT date(first_seen), COUNT(*)
        FROM jobs WHERE source = ?
          AND date(first_seen) >= date('now', '-30 days')
        GROUP BY 1 ORDER BY 1 DESC LIMIT 14
    """, (src,)).fetchall()
    print(f"  {src} :")
    if not rows:
        print("    (aucune donnée sur 30 jours)")
    for d, n in rows:
        print(f"    {d}  {n:>4}  {'█' * min(n // 2, 60)}")
    print()

# ── 3. Offres freelance effectivement remontées ──────────────────────────────
print("=" * 72)
print("3. OFFRES NON-PERMANENTES DES 30 DERNIERS JOURS")
print("=" * 72)
rows = q("""
    SELECT j.source, j.title, j.company, j.location, j.contract_type, s.score
    FROM jobs j JOIN job_scores s ON s.job_id = j.id
    WHERE date(j.first_seen) >= date('now', '-30 days')
      AND LOWER(COALESCE(j.contract_type, '')) NOT IN ('permanent', '')
    ORDER BY s.score DESC LIMIT 40
""").fetchall()
if not rows:
    print("AUCUNE. Soit le marché est vide, soit l'inférence contract_type "
          "renvoie 'permanent' par défaut — vérifier le prompt du scorer.")
for src, title, comp, loc, ct, sc in rows:
    print(f"  [{sc}] {ct:<12} {(title or '')[:44]:<44} {(comp or '')[:20]:<20} {src}")

# ── 4. Contrôle du biais d'inférence ─────────────────────────────────────────
print("\n" + "=" * 72)
print("4. BIAIS D'INFÉRENCE — part de 'permanent' par source")
print("=" * 72)
print("Une source à 100% permanent est suspecte : soit elle n'héberge que du")
print("permanent (RemoteOK), soit le LLM répond 'permanent' par défaut.\n")
for src, tot, perm in q("""
    SELECT j.source, COUNT(*),
           SUM(CASE WHEN LOWER(COALESCE(j.contract_type,'')) = 'permanent'
                    THEN 1 ELSE 0 END)
    FROM jobs j JOIN job_scores s ON s.job_id = j.id
    WHERE date(j.first_seen) >= date('now', '-30 days')
    GROUP BY 1 HAVING COUNT(*) > 0 ORDER BY 2 DESC
""").fetchall():
    pct = 100 * perm / tot if tot else 0
    flag = "  ⚠️" if pct == 100 and tot >= 10 else ""
    print(f"  {src:<20} {perm:>4}/{tot:<4} permanent  ({pct:5.1f}%){flag}")
