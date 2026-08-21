# Tasks: Onglet Reports — exports CSV téléchargeables (registre de presets)

**Input**: Design documents from `specs/025-reports-tab/` — `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/export-contract.md`, `quickstart.md`

**Prerequisites**: `plan.md` (technique), `spec.md` (user stories + priorités), `contracts/export-contract.md` (contrats `build_export_rows` / `rows_to_csv_bytes` / registre), `data-model.md` (entités), `quickstart.md` (validation).

**Tests**: **Aucun test unitaire demandé** (la spec et le plan ne requièrent pas de test unitaire ; aucun fichier NEVER n'est touché). La non-régression est vérifiée par **comparaison byte-identique** de la CLI (voir US3 / `quickstart.md` §1).

**Note de renommage déjà appliquée** : `export_orp.py` → `export_jobs.py` (via `git mv`) et les identifiants de branding (`orp_` → `jobs_`, libellé « Candidatures ») ont déjà été appliqués pendant la phase de planification. Seul l'en-tête de colonne CSV `Assignation ORP` est conservé (contrat de données du formulaire 716.007). Les tâches ci-dessous portent sur le **refactor** et la **page UI**, pas sur le renommage.

## Format: `[ID] [P?] [Story] Description`

- **[P]** : exécutable en parallèle (fichier différent, aucune dépendance)
- **[Story]** : story associée (`US1`–`US4`). Phases Setup/Foundational/Polish : pas de label.

## Path Conventions

- Script refactoré à la racine : `export_jobs.py`
- Nouvelle page : `tracker_views/reports.py`
- Enregistrement : `tracker.py` (1 ligne)
- Pattern page existant : imports depuis `tracker_views.shared` (`is_active_page`), fonction `render()`, garde `if is_active_page(__file__): render()`.

---

## Phase 1: Setup (Infrastructure partagée)

**Purpose** : Vérifier l'état initial (renommage appliqué, aucune nouvelle dépendance) et capturer la référence de non-régression **avant** le refactor.

- [X] T001 Confirmer l'état initial : `export_jobs.py` (renommé depuis `export_orp.py`) est présent, compile (`python -m py_compile export_jobs.py`) et `--help` fonctionne ; confirmer qu'aucune dépendance nouvelle n'est requise (stdlib + Streamlit uniquement, cf. plan.md Constraints).
- [X] T002 Capturer la référence de non-régression **avant refactor** : `python export_jobs.py --month 2026-05` puis `cp data/jobs_2026-05.csv /tmp/jobs_2026-05.reference.csv` (et de même pour `--statuses applied rejected archived` si disponible). Cette référence sert à US3.

---

## Phase 2: Foundational (Refactor de `export_jobs.py`)

**Purpose** : Extraire les fonctions importables qui servent **à la fois** la CLI (US3) et le preset UI (US1). Bloquant pour toutes les stories UI.

**⚠️ CRITICAL** : Aucune story UI ne peut commencer tant que cette phase n'est pas terminée.

- [X] T003 Dans `export_jobs.py`, extraire `build_export_rows(db_path, date_from, date_to, statuses) -> list[dict]` : fusionner `fetch_jobs` (jointure `jobs JOIN job_tracking`, filtre `status IN` + `date(jt.changed_at) BETWEEN ? AND ?`, `ORDER BY jt.changed_at ASC`) et `format_for_export` (mapping 10 colonnes, statut→résultat, entreprise + location, suffixe `work_mode`, motif négatif). Remplacer le `sys.exit(1)` sur DB absente par `raise FileNotFoundError(db_path)`. Conserver les 10 colonnes, leur ordre, le mapping et l'encodage **à l'octet près**.
- [X] T004 Dans `export_jobs.py`, extraire `rows_to_csv_bytes(rows) -> bytes` : `csv.DictWriter` vers `io.StringIO(newline="")`, en-tête = `rows[0].keys()`, encodage `utf-8-sig` (BOM) ; retourner `b""` si `rows` est vide. Doit produire exactement les mêmes octets que l'actuel `write_csv`.
- [X] T005 Dans `export_jobs.py`, réécrire `main()` en wrapper mince : parser les flags inchangés (`--month`/`--from`/`--to`/`--statuses`), résoudre la plage (priorité `--from/--to` > `--month` > mois courant + `[WARN]` inchangé), appeler `build_export_rows(DB_PATH, date_from, date_to, statuses)`, attraper `FileNotFoundError` → `print("[ERROR] DB introuvable : …", file=sys.stderr)` + `sys.exit(1)`, sinon sérialiser via `rows_to_csv_bytes`, écrire `data/jobs_{date_from[:7]}.csv` (`mkdir parents`, sauter si vide, `print("[OK] N entrée(s) exportée(s)")`), puis afficher le résumé/aperçu terminal inchangés. Supprimer `fetch_jobs`/`format_for_export`/`write_csv` si entièrement absorbés.

**Checkpoint** : Le refactor produit un CSV dont le **contenu** est byte-identique à la référence T002 (seul le préfixe du nom de fichier `jobs_` diffère de l'historique `orp_`).

---

## Phase 3: User Story 1 — Télécharger un export depuis le navigateur (Priority: P1) 🎯 MVP

**Goal** : Ouvrir Reports dans le tracker, choisir type d'export + période + statuts, générer et télécharger un CSV sans SSH/scp ni écriture disque.

**Independent Test** : Sur la DB live, ouvrir Reports, sélectionner « Candidatures », Mois = 2026-08, Statuts = [applied], générer, télécharger `jobs_2026-08.csv` — contenu identique à celui de la CLI pour les mêmes paramètres.

- [X] T006 [US1] Créer `tracker_views/reports.py` avec le registre de presets (structure associative `label -> preset`) et le premier preset : `label = "Candidatures"`, `generate(date_from, date_to, statuses, mode) -> (rows, filename)` qui appelle `build_export_rows(DB_PATH, date_from, date_to, statuses)` et dérive `filename` = `jobs_{date_from[:7]}.csv` (mode mois) ou `jobs_{date_from}_{date_to}.csv` (mode plage).
- [X] T007 [P] [US1] Enregistrer la page dans `tracker.py` : ajouter `st.Page("tracker_views/reports.py", title="Reports", icon="📤")` à la liste existante `st.navigation([...])`.
- [X] T008 [US1] Dans `tracker_views/reports.py`, ajouter `from tracker_views.shared import is_active_page` et `from export_jobs import build_export_rows, rows_to_csv_bytes, DB_PATH, STATUSES_ALL, STATUSES_DEFAULT` ; implémenter `render()` avec la garde `if is_active_page(__file__): render()`. Construire les contrôles : `st.selectbox` « Type d'export » (labels du registre), `st.radio` « Période » (Mois / Plage de dates), champ mois `YYYY-MM` (défaut = mois courant) ou deux `st.date_input`, `st.multiselect` « Statuts » (options `STATUSES_ALL`, défaut `STATUSES_DEFAULT`), et `st.button` « Générer l'aperçu ». Au clic : résoudre la période en `date_from`/`date_to` (mois → premier/dernier jour du mois), appeler `preset.generate(...)`, stocker `(rows, filename)` en session-state (`reports_rows`, `reports_filename`, `reports_generated`). Clés préfixées `reports_*`.
- [X] T009 [US1] Dans `tracker_views/reports.py`, ajouter `st.download_button` « Télécharger … » alimenté par `rows_to_csv_bytes(reports_rows)` avec `file_name=reports_filename` et `mime="text/csv"` ; désactivé quand aucun `rows` n'est généré (jamais de CSV vide).

**Checkpoint** : US1 fonctionnel et testable seul — téléchargement bout-en-bout.

---

## Phase 4: User Story 2 — Aperçu et vérification avant téléchargement (Priority: P2)

**Goal** : Avant de télécharger, voir combien d'entrées, leur répartition par résultat, et le détail des lignes (date, entreprise, poste, résultat).

**Independent Test** : Période avec 6 « en suspens » + 3 « négatif » → Entrées = 9, En suspens = 6, Négatif = 3, tableau de 9 lignes.

- [X] T010 [US2] Dans `tracker_views/reports.py`, ajouter 3 `st.metric` dans `st.columns(3)` : « Entrées » (= `len(rows)`) + décompte par résultat (« En suspens », « Négatif ») via `collections.Counter` sur le champ « Résultat » des rows.
- [X] T011 [US2] Dans `tracker_views/reports.py`, ajouter `st.dataframe` d'aperçu de `reports_rows` projeté sur les colonnes Date, Entreprise, Poste, Résultat (dérivées de `Jour`/`Mois`, `Entreprise / Adresse`, `Description du poste`, `Résultat`).
- [X] T012 [US2] Dans `tracker_views/reports.py`, ajouter `st.caption` signalant que la colonne « Personne contactée / Tél. » (et toute autre colonne non déductible) est à compléter à la main.

**Checkpoint** : US1 **et** US2 fonctionnent indépendamment (l'aperçu s'affiche au-dessus du bouton de téléchargement).

---

## Phase 5: User Story 3 — La CLI d'export reste identique (non-régression) (Priority: P2)

**Goal** : Prouver que le refactor n'a rien cassé au script CLI : même contenu CSV, mêmes flags, même comportement d'erreur.

**Independent Test** : `python export_jobs.py --month 2026-05` → contenu byte-identique à la référence T002, résumé terminal inchangé.

- [X] T013 [US3] Exécuter la validation de non-régression (`quickstart.md` §1) : `python export_jobs.py --month 2026-05` puis `cmp data/jobs_2026-05.csv /tmp/jobs_2026-05.reference.csv` → **identique** ; vérifier aussi `--statuses applied rejected archived`, `--from … --to …`, le `[WARN]` de coexistence `--month`+`--from/--to`, et le cas DB absente (renommer temporairement → `[ERROR] DB introuvable : …` + exit 1).

**Checkpoint** : La CLI est prouvée sans régression.

---

## Phase 6: User Story 4 — Registre de presets extensible (Priority: P3)

**Goal** : Confirmer qu'ajouter un futur preset = une fonction `generate(...)` + une entrée au registre, sans toucher au reste de l'UI.

**Independent Test** : Ajouter un preset factice (label + `generate`) le fait apparaître dans le selectbox « Type d'export » sans autre modification.

- [X] T014 [US4] Vérifier l'extensibilité (`quickstart.md` §4) : ajouter temporairement un preset factice au registre, confirmer qu'il apparaît dans le `st.selectbox` sans autre changement, puis le retirer (non livré). Confirmer que la page ne contient aucune logique spécifique à un preset (elle ne connaît que `label` + `generate`).

**Checkpoint** : Le registre est prouvé extensible ; le selectbox est piloté par `list(registry)`.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose** : Validation finale et conformité.

- [X] T015 Exécuter la validation bout-en-bout de `quickstart.md` (§2 UI : conformité mockup, §3 cas limites : période vide → metrics 0 + download désactivé, `work_mode` NULL/`unknown`, `location` NULL, plage inversée).
- [X] T016 [P] Vérifier la conformité Constitution : aucun changement à `storage.py`, `models.py`, `profiles.py`, `scrape.py`, `scorer.py`, `main.py`, ni aucun scraper ; le diff se limite à `export_jobs.py`, `tracker_views/reports.py`, `tracker.py` (+ docs `specs/025-reports-tab/`, `README.md`).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** : aucune dépendance — démarre immédiatement.
- **Foundational (Phase 2)** : dépend de la Setup (la référence T002 doit exister avant le refactor). **BLOQUE** toutes les stories UI.
- **US1 (Phase 3)** : dépend du refactor (Phase 2) — le preset appelle `build_export_rows`.
- **US2 (Phase 4)** : dépend de US1 (mêmes `reports_rows` générés) ; additif dans le même fichier.
- **US3 (Phase 5)** : dépend du refactor (Phase 2) + référence T002 ; indépendant de US1/US2.
- **US4 (Phase 6)** : dépend de US1 (le registre existe) ; validation pure.
- **Polish (Phase 7)** : dépend de toutes les stories souhaitées.

### User Story Dependencies

- **US1 (P1)** : après Foundational. Aucune dépendance aux autres stories.
- **US2 (P2)** : après US1 (partage le fichier `reports.py` et l'état généré).
- **US3 (P2)** : après Foundational (indépendant de l'UI).
- **US4 (P3)** : après US1 (validation de l'architecture du registre).

### Within Each Story

- Foundational : `build_export_rows` → `rows_to_csv_bytes` → `main()` (même fichier, séquentiel).
- US1 : registre + preset → enregistrement `tracker.py` (parallèle) → contrôles + génération → download.
- US2 : metrics → dataframe → caption (additifs au même fichier).

### Parallel Opportunities

- **T007** (enregistrer dans `tracker.py`) est parallélisable avec **T006/T008/T009** (fichier `reports.py`) — fichiers différents.
- **T016** (vérif Constitution) est parallélisable avec **T015** (validation quickstart).
- Toutes les autres tâches d'un même fichier (`export_jobs.py`, `reports.py`) sont **séquentielles**.

---

## Parallel Example: US1

```bash
# Fichiers différents — exécutables en parallèle :
Task: "T006 [US1] Créer tracker_views/reports.py avec le registre + premier preset"
Task: "T007 [P] [US1] Enregistrer la page dans tracker.py"

# Puis, séquentiellement dans reports.py :
Task: "T008 [US1] Contrôles + génération (session-state reports_*)"
Task: "T009 [US1] Bouton télécharger (rows_to_csv_bytes)"
```

---

## Implementation Strategy

### MVP First (US1 uniquement)

1. Phase 1 : Setup + capture référence (T001–T002).
2. Phase 2 : Refactor `export_jobs.py` (T003–T005) — **critique, bloque tout**.
3. Phase 3 : US1 (T006–T009).
4. **STOP & VALIDER** : tester US1 indépendamment (téléchargement bout-en-bout, contenu identique CLI).
5. Démo si prêt.

### Incremental Delivery

1. Setup + Foundational → base prête (refactor prouvé sans régression via US3).
2. US1 → test indépendant → MVP (téléchargement).
3. US2 → aperçu (metrics + tableau + note) → test indépendant.
4. US3 → validation non-régression formelle.
5. US4 → preuve d'extensibilité.
6. Polish → validation quickstart complète + conformité Constitution.
7. Chaque story ajoute de la valeur sans casser les précédentes.

### Séquence recommandée (projet solo)

T001 → T002 → T003 → T004 → T005 → **T013 (valider la CLI tout de suite, US3)** → T006 → T007 → T008 → T009 → T010 → T011 → T012 → T014 → T015 → T016.

> Note : exécuter **T013 (US3)** immédiatement après le refactor (T005), *avant* de construire l'UI, pour isoler toute régression du refactor du reste.

---

## Notes

- [P] = fichier différent, aucune dépendance.
- [Story] relie la tâche à sa story pour la traçabilité.
- Le renommage `export_orp.py` → `export_jobs.py` + branding (`jobs_`, « Candidatures ») est **déjà appliqué** ; les tâches ne le refont pas.
- L'en-tête `Assignation ORP` est **conservé** (contrat de données 716.007) — ne pas le renommer.
- Commit après chaque tâche ou groupe logique ; s'arrêter à chaque checkpoint pour valider.
- Éviter : tâches vagues, conflits sur un même fichier, dépendances croisées qui cassent l'indépendance des stories.
