# Prompt — pilote de ré-extraction `work_mode`

À coller dans Claude Code, à la racine de `~/AI-Suite/job_agent/`.

---

```
Contexte

Le champ jobs.work_mode est mal renseigné. Audit fait, sans appel LLM
(scripts/audit_work_mode.py) :

| work_mode | n   | signal explicite dans le texte source |
|-----------|-----|---------------------------------------|
| remote    | 425 | 92 %                                  |
| hybrid    | 280 | 77 %                                  |
| on-site   | 654 | 0,9 %  ← 6 offres sur 654             |

Le texte source = title + company + location + base_location + description,
soit exactement les champs que scorer.py passe au LLM (~l.567 et ~l.610).

Conclusion : 'on-site' n'est pas une lecture de l'annonce, c'est la valeur
par défaut du LLM quand rien n'est indiqué. Or allowed_work_modes vaut
["remote","hybrid","unknown"], donc ces 654 offres sont éliminées à
l'assemblage du digest (score.py ~l.527) sans que leur note soit consultée.

Le scoring_context du profil unified_jc a été mis à jour avec une section
"# Field extraction" qui impose "unknown" en l'absence d'information.
Texte de référence : prompts/scoring_context_unified_jc.FINAL.txt

Objectif : vérifier que le nouveau prompt est effectivement suivi, sur un
échantillon, AVANT de ré-extraire les 654.

## Étape 1 — Sécuriser (obligatoire avant tout écrit)

- cp data/jobs.db data/jobs.db.bak.$(date +%Y%m%d-%H%M)
- La ré-extraction ÉCRASE work_mode, contract_type, language_required,
  geo_zone et summary. Sans snapshot ces valeurs sont définitivement perdues.

## Étape 2 — Figer l'échantillon et l'état AVANT

- Sélectionne 50 jobs avec work_mode = 'on-site', de façon DÉTERMINISTE
  et reproductible (pas de random sans seed) — par exemple ORDER BY id
  LIMIT 50. L'échantillon doit pouvoir être resélectionné à l'identique.
- Répartis-le sur plusieurs sources si possible (LinkedIn, Indeed,
  Greenhouse, Jobup en ont le plus). Un échantillon 100 % LinkedIn ne
  dirait rien du reste.
- Écris l'état AVANT dans un CSV versionné, hors DB :
  data/pilot_work_mode_before.csv
  Colonnes : id, source, title, company, location, base_location,
  work_mode, contract_type, language_required, score (profil unified_jc).
- Ce CSV est le point de comparaison. Ne le régénère pas après coup.

## Étape 3 — Lancer

- Ré-extrais / rescores UNIQUEMENT ces 50 ids. Ne lance pas un run complet.
- Dis-moi la commande exacte utilisée, et si tu as dû ajouter un flag ou
  un script ad hoc pour cibler une liste d'ids.

## Étape 4 — Analyse

Ne te contente pas de compter les changements. La question n'est pas
"combien ont bougé" mais "les nouvelles valeurs sont-elles justes".

Produis :

1. Matrice de confusion avant → après sur work_mode.

2. Pour chaque job, croise la nouvelle valeur avec la présence d'un signal
   explicite dans le texte source. Réutilise les regex de
   scripts/audit_work_mode.py — elles sont déjà calibrées (92 % de
   cohérence sur les 'remote', ce qui les valide).

   | Nouvelle valeur | Signal dans le texte | Verdict          |
   |-----------------|----------------------|------------------|
   | unknown         | aucun                | ✅ correct        |
   | unknown         | remote/hybrid présent| ⚠️ trop prudent   |
   | on-site         | on-site présent      | ✅ correct        |
   | on-site         | aucun                | ❌ prompt ignoré  |

   Le taux de ❌ est le résultat qui décide de la suite.

3. Effets collatéraux : contract_type, language_required et score ont-ils
   bougé, et dans quel sens ? Une dérive du score serait un signal que le
   nouveau prompt a déplacé autre chose que ce qu'on visait.

4. Cinq exemples concrets, avec l'extrait du texte source qui justifie (ou
   contredit) la nouvelle valeur. Je veux pouvoir juger à la main.

## Étape 5 — Recommandation

- ❌ < 10 %  → prompt suivi. Proposer la ré-extraction des 654, en
              rappelant que ~600 vont basculer vers 'unknown' et entrer
              dans le digest.
- ❌ 10-40 % → partiellement suivi. Proposer un durcissement de la
              formulation plutôt que de généraliser.
- ❌ > 40 %  → prompt ignoré. Ne rien généraliser. Chercher pourquoi :
              la consigne entre-t-elle en conflit avec une autre partie
              du prompt ? Le modèle utilisé est-il le bon ?

## À savoir pour la suite (ne pas traiter maintenant)

Il existe DEUX prompts qui écrivent jobs.work_mode :
- le scoring_context du profil (données, modifié) — chemin scoring,
  via _update_job_extraction_fields (storage.py ~l.1476)
- EXTRACTION_PROMPT dans scorer.py ~l.172 (code, PAS modifié) — chemin
  extraction, via save_extracted_fields (storage.py ~l.1432)

Le scoring passe après l'extraction et écrase la valeur, donc le profil
domine pour les jobs scorés. Mais un job extrait sans être scoré gardera
un 'on-site' deviné. Si le pilote est concluant, la même section
"# Field extraction" devra être reportée dans EXTRACTION_PROMPT.
CLAUDE.md autorise scorer.py pour les changements de prompt uniquement.

Commence par les étapes 1 et 2, montre-moi le CSV AVANT, et attends ma
validation avant de lancer quoi que ce soit qui écrit en base.
```
