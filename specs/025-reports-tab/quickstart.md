# Quickstart — Validation: Onglet Reports

Guide de validation de bout en bout, sur le Mac de dev uniquement (jamais verva pendant ce cycle).

## Prérequis

- `.venv` actif, dépendances installées (Streamlit + stdlib).
- `data/jobs.db` présent avec des jobs suivis (au moins quelques `applied`, `rejected`, `archived`).
- Une version de référence du CSV pour le test de non-régression (voir §1).

## 1. Test de non-régression CLI (byte-identique)

1. **Avant refactor**, produire la référence :
   ```bash
   python export_jobs.py --month 2026-05
   cp data/jobs_2026-05.csv /tmp/jobs_2026-05.reference.csv
   ```
2. **Après refactor**, re-générer et comparer :
   ```bash
   python export_jobs.py --month 2026-05
   cmp data/jobs_2026-05.csv /tmp/jobs_2026-05.reference.csv && echo "IDENTIQUE"
   ```
3. Vérifier les autres chemins CLI :
   ```bash
   python export_jobs.py --statuses applied rejected archived
   python export_jobs.py --from 2026-05-01 --to 2026-06-30
   python export_jobs.py --month 2026-05 --from 2026-05-01 --to 2026-06-30   # doit afficher le [WARN]
   ```
   - `cmp` doit valider `IDENTIQUE` ; le résumé terminal doit être inchangé.
   - DB absente (renommer temporairement) → `[ERROR] DB introuvable : …` + exit 1.

## 2. Validation de l'onglet Reports (UI)

1. Lancer l'app : `streamlit run tracker.py`.
2. Dans la navigation latérale, ouvrir **Reports**.
3. Vérifier la conformité au mockup (éléments, libellés, ordre) :
   - Selectbox « Type d'export » → `Candidatures`.
   - Radio « Période » (Mois / Plage de dates) ; « Mois » pré-rempli au mois courant.
   - Multiselect « Statuts » (défaut `applied`).
   - Bouton « Générer l'aperçu ».
4. Générer l'aperçu et vérifier :
   - 3 metrics : Entrées + décompte par résultat, égaux au décompte attendu.
   - Tableau d'aperçu (Date, Entreprise, Poste, Résultat).
   - Caption « colonne à compléter à la main ».
5. Télécharger et vérifier :
   - Fichier nommé `jobs_YYYY-MM.csv` (mois) ou `jobs_YYYY-MM-DD_YYYY-MM-DD.csv` (plage).
   - `cmp` avec le CSV CLI pour les mêmes paramètres → **byte-identique**.
   - Ouverture sans corruption dans LibreOffice (BOM `utf-8-sig`).

## 3. Cas limites à vérifier

- Période sans résultat → metrics 0, tableau vide, bouton télécharger désactivé.
- `work_mode` NULL/`unknown` → poste sans suffixe.
- `location` NULL → entreprise seule.
- Mode « Plage de dates » → nom de fichier à deux dates.

## 4. Extension du registre (preuve de non-trivialité)

Ajouter un preset factice (label + `generate`) au registre → il apparaît dans le selectbox sans autre changement. Retirer ensuite ce preset factice (non livré).
