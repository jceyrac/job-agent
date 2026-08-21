#!/usr/bin/env python3
"""Le LLM devine-t-il 'on-site' ? — audit sans aucun appel LLM.

Hypothèse testée : les annonces étiquetées 'on-site' ne contiennent en fait
aucun signal explicite de mode de travail, et le LLM a comblé le vide.

Si c'est vrai, la bonne valeur est 'unknown' et ces offres sont éliminées à
tort à l'assemblage du digest.

    python3 scripts/audit_work_mode.py [chemin/vers/jobs.db]

Lecture seule. Coût : zéro appel LLM.
"""
import re
import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else "data/jobs.db"
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

# Signaux explicites de mode de travail, FR / EN / DE
SIGNALS = {
    "remote": r"\b(remote|fully remote|work from home|télétravail|teletravail|"
              r"home[- ]?office|homeoffice|work from anywhere|distanciel)\b",
    "hybrid": r"\b(hybrid|hybride|\d{1,3}\s*%\s*(remote|home|télétravail)|"
              r"\d\s*(days?|jours?|tage)\s*(per|par|pro)\s*(week|semaine|woche)|"
              r"flexible work)\b",
    "onsite": r"\b(on[- ]?site|onsite|sur site|présentiel|presentiel|vor ort|"
              r"in[- ]office|no remote|pas de télétravail|100\s*%\s*(sur site|vor ort))\b",
}
PATTERNS = {k: re.compile(v, re.I) for k, v in SIGNALS.items()}

rows = conn.execute("""
    SELECT id, source, title, company, location, base_location, description
    FROM jobs WHERE work_mode = 'on-site'
""").fetchall()

print(f"DB : {db_path}")
print(f"Offres étiquetées 'on-site' : {len(rows)}\n")
if not rows:
    sys.exit(0)

buckets = {"aucun signal": [], "signal on-site": [], "signal remote/hybrid": []}
per_source = {}

for jid, source, title, company, location, base_loc, desc in rows:
    # IMPORTANT : reproduire exactement les champs que voit l'extracteur
    # (scorer.py ~l.610) — title, company, location, base_location, description.
    # Omettre location fausse complètement le résultat : les sources remote
    # mettent "Remote" dans location et pas dans la description.
    text = f"{title or ''} {company or ''} {location or ''} {base_loc or ''} {desc or ''}"
    has_on = bool(PATTERNS["onsite"].search(text))
    has_rh = bool(PATTERNS["remote"].search(text) or PATTERNS["hybrid"].search(text))

    if has_on and not has_rh:
        k = "signal on-site"
    elif has_rh:
        k = "signal remote/hybrid"
    else:
        k = "aucun signal"
    buckets[k].append((jid, source, title, company, location))

    s = per_source.setdefault(source, {"total": 0, "aucun": 0, "rh": 0})
    s["total"] += 1
    if k == "aucun signal":
        s["aucun"] += 1
    if k == "signal remote/hybrid":
        s["rh"] += 1

n = len(rows)
print("=" * 74)
print("VERDICT")
print("=" * 74)
for k in ("signal on-site", "aucun signal", "signal remote/hybrid"):
    v = len(buckets[k])
    print(f"  {k:<24} {v:>5}  ({100*v/n:5.1f} %)")

mislabeled = len(buckets["aucun signal"]) + len(buckets["signal remote/hybrid"])
print(f"\n  → étiquetage douteux : {mislabeled}/{n} ({100*mislabeled/n:.1f} %)")
print("     'aucun signal'        = l'annonce ne dit rien → devrait être 'unknown'")
print("     'signal remote/hybrid'= l'annonce dit le contraire → erreur franche")

print("\n" + "=" * 74)
print("PAR SOURCE (les sources les plus douteuses en premier)")
print("=" * 74)
print(f"{'source':<20}{'total':>7}{'sans signal':>13}{'contredit':>11}{'douteux %':>11}")
for src, s in sorted(per_source.items(),
                     key=lambda x: -(x[1]['aucun'] + x[1]['rh']) / x[1]['total']):
    pct = 100 * (s["aucun"] + s["rh"]) / s["total"]
    print(f"{src:<20}{s['total']:>7}{s['aucun']:>13}{s['rh']:>11}{pct:>10.0f}%")

print("\n" + "=" * 74)
print("ÉCHANTILLON À VÉRIFIER À LA MAIN (20 offres, signal contradictoire)")
print("=" * 74)
print("Ce sont les erreurs les plus franches : l'annonce mentionne remote ou")
print("hybrid, et le LLM a répondu 'on-site'. Ouvre-en quelques-unes.\n")
for jid, source, title, company, location in buckets["signal remote/hybrid"][:20]:
    print(f"  #{jid:<7} {(title or '')[:44]:<44} {(company or '?')[:18]:<18} {source}")

if not buckets["signal remote/hybrid"]:
    print("  (aucune — le LLM ne contredit jamais franchement l'annonce)")

print("\n" + "=" * 74)
print("DÉCISION")
print("=" * 74)
print("""  > 50 % douteux  → le LLM devine. Ré-extraire le sous-ensemble 'on-site'
                      avec le prompt corrigé est justifié.
  20-50 %         → biais réel mais partiel. Ré-extraire, en snapshotant
                      la DB d'abord.
  < 20 %          → l'étiquetage est correct, le marché est simplement
                      majoritairement on-site. Ne rien ré-extraire.

  Avant toute ré-extraction :  cp data/jobs.db data/jobs.db.bak.$(date +%Y%m%d)
  La ré-extraction ÉCRASE work_mode — sans snapshot, l'ancienne valeur est perdue.""")
