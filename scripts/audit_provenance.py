#!/usr/bin/env python3
"""Provenance des champs extraits — qui a écrit quoi, et sur quelle preuve.

    python3 scripts/audit_provenance.py [chemin/vers/jobs.db] [--max-age-days N]

Lecture seule.

REFUSE DE TOURNER sur une base périmée. Les copies locales sur le Mac sont
des snapshots figés ; seule la DB de prod (HPE) reflète l'état réel.
"""
import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone

ap = argparse.ArgumentParser()
ap.add_argument("db", nargs="?", default="data/jobs.db")
ap.add_argument("--max-age-days", type=int, default=3)
ap.add_argument("--force", action="store_true", help="passer outre le garde-fou")
a = ap.parse_args()

conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
q = conn.execute

# ── Garde-fou : la base est-elle vivante ? ───────────────────────────────────
mx = q("SELECT MAX(first_seen) FROM jobs").fetchone()[0]
try:
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(mx)).days
except Exception:
    age = None

print(f"DB      : {a.db}")
print(f"Dernier first_seen : {mx}  ({age} jours)" if age is not None else f"Dernier first_seen : {mx}")

if age is not None and age > a.max_age_days and not a.force:
    print(f"\n❌ ARRÊT — données vieilles de {age} jours (seuil : {a.max_age_days}).")
    print("   Ce fichier est un snapshot, pas la base vivante.")
    print("   Lance ce script sur la prod (HPE), ou passe --force en connaissance de cause.")
    sys.exit(1)
print()

FIELDS = ("coalesce(title,'')||' '||coalesce(company,'')||' '||coalesce(location,'')"
          "||' '||coalesce(base_location,'')||' '||coalesce(description,'')")
SIGNAL = re.compile(
    r"\b(remote|hybrid|hybride|t[ée]l[ée]travail|home.?office|on.?site|sur site|"
    r"pr[ée]sentiel|vor ort|work from home|distanciel|\d{1,3}\s*%\s*(remote|home))\b", re.I)

# ── 1. Qui a écrit les champs ────────────────────────────────────────────────
print("=" * 78)
print("1. PROVENANCE — extracted_by × work_mode")
print("=" * 78)
modes = [r[0] for r in q("SELECT DISTINCT coalesce(work_mode,'?') FROM jobs ORDER BY 1")]
print(f"{'extracted_by':<44}" + "".join(f"{m:>10}" for m in modes) + f"{'TOTAL':>8}")
tier0_onsite = llm_onsite = 0
for (eb,) in q("SELECT DISTINCT coalesce(extracted_by,'<NULL>') FROM jobs ORDER BY 1"):
    d = dict(q("SELECT coalesce(work_mode,'?'),COUNT(*) FROM jobs "
               "WHERE coalesce(extracted_by,'<NULL>')=? GROUP BY 1", (eb,)).fetchall())
    print(f"{eb[:43]:<44}" + "".join(f"{d.get(m,0):>10}" for m in modes) + f"{sum(d.values()):>8}")
    if eb == "tier_0":
        tier0_onsite = d.get("on-site", 0)
    else:
        llm_onsite += d.get("on-site", 0)

print(f"\n  'on-site' issus de tier_0 (aucun appel LLM) : {tier0_onsite}")
print(f"  'on-site' issus d'un LLM                    : {llm_onsite}")
if tier0_onsite + llm_onsite:
    share = 100 * tier0_onsite / (tier0_onsite + llm_onsite)
    print(f"\n  → part hors de portée d'un correctif de prompt : {share:.0f} %")
    print("     Corriger EXTRACTION_PROMPT ne peut agir que sur les lignes LLM.")

# ── 2. Les étiquettes reposent-elles sur une preuve ? ────────────────────────
print("\n" + "=" * 78)
print("2. PREUVE TEXTUELLE — part des étiquettes justifiées par le texte source")
print("=" * 78)
print("Le texte source = title + company + location + base_location + description,")
print("soit exactement ce que scorer.py passe au modèle.\n")
print(f"{'work_mode':<12}{'n':>7}{'avec signal':>13}{'%':>7}   lecture")
for wm in ("remote", "hybrid", "on-site", "unknown"):
    rows = q(f"SELECT {FIELDS} FROM jobs WHERE work_mode=?", (wm,)).fetchall()
    if not rows:
        continue
    hit = sum(1 for (t,) in rows if SIGNAL.search(t))
    pct = 100 * hit / len(rows)
    note = ("étiquette fondée" if pct > 60 else
            "étiquette majoritairement devinée" if pct < 25 else "mitigé")
    print(f"{wm:<12}{len(rows):>7}{hit:>13}{pct:>6.0f}%   {note}")

# ── 3. Détail des 'on-site' par producteur ──────────────────────────────────
print("\n" + "=" * 78)
print("3. 'on-site' — preuve textuelle, par producteur")
print("=" * 78)
print(f"{'extracted_by':<44}{'n':>7}{'avec signal':>13}{'%':>7}")
for (eb,) in q("SELECT DISTINCT coalesce(extracted_by,'<NULL>') FROM jobs ORDER BY 1"):
    rows = q(f"SELECT {FIELDS} FROM jobs WHERE work_mode='on-site' "
             "AND coalesce(extracted_by,'<NULL>')=?", (eb,)).fetchall()
    if not rows:
        continue
    hit = sum(1 for (t,) in rows if SIGNAL.search(t))
    print(f"{eb[:43]:<44}{len(rows):>7}{hit:>13}{100*hit/len(rows):>6.0f}%")

# ── 4. Impact sur le digest ─────────────────────────────────────────────────
print("\n" + "=" * 78)
print("4. IMPACT — offres écartées du digest par le seul filtre work_mode")
print("=" * 78)
n_out = q("SELECT COUNT(*) FROM jobs WHERE work_mode='on-site'").fetchone()[0]
tot = q("SELECT COUNT(*) FROM jobs").fetchone()[0]
print(f"  {n_out} / {tot} offres ({100*n_out/tot:.0f} %) portent work_mode='on-site'")
print("  et sont donc éliminées à l'assemblage du digest (score.py ~l.527),")
print("  sans que leur note soit consultée.")

print("\n" + "=" * 78)
print("CE QU'IL FAUT EN CONCLURE")
print("=" * 78)
print("""  - Section 2, ligne 'remote' : c'est le témoin. Si elle est au-dessus de
    60 %, la regex est calibrée et les autres lignes sont interprétables.
    Si elle est basse, la regex est en cause, pas les étiquettes.
  - Section 3 : si tier_0 domine, un correctif de prompt ne suffira pas.
    Il faudra comprendre d'où tier_0 tire son work_mode — valeur héritée du
    scraper, défaut en dur, ou écrasement d'une extraction correcte par la
    passe de scoring.
  - Vérifier dans le code que la passe de scoring ne réécrit pas par-dessus
    une ré-extraction fraîche. Sinon la ré-extraction sera annulée au run
    suivant.""")
