# Phase 0 — Research: Onglet Reports

Résolution des inconnues du Technical Context et des choix de design, alignés sur le code réel.

## 1. Montage de l'onglet : `st.tabs` vs `st.navigation`

**Décision**: « Reports » est une **nouvelle page de navigation latérale** (`st.Page("tracker_views/reports.py", title="Reports", …)`), ajoutée à la liste `pages` de `tracker.py` — **pas** un `st.tabs`.

**Rationale**: `tracker.py` n'utilise `st.tabs` nulle part ; la navigation est `st.navigation` + `st.Page` (6 pages top-level). La barre du mockup est du HTML illustratif. Monter Reports en page latérale est le choix minimal, cohérent avec l'architecture existante (Constitution V).

**Alternatives considérées**: (a) introduire un `st.tabs` quelque part — rejeté, aucune page n'utilise ce pattern et cela romprait la navigation ; (b) intégrer Reports dans `settings.py` — rejeté, la spec impose un onglet dédié.

## 2. Colonne de filtre temporel : `updated_at` vs `changed_at`

**Décision**: filtre sur `job_tracking.changed_at`.

**Rationale**: `job_tracking` n'a **pas** de colonne `updated_at` ; la colonne réelle est `changed_at` (denormalisée, Phase 2.2). Le script d'export utilise déjà `changed_at`.

**Alternatives**: `updated_at` (n'existe pas → SQL error), `status_history.changed_at` (plus précis historiquement mais requiert un JOIN supplémentaire et ne correspond pas au comportement CLI validé) — rejetés.

## 3. Import du script d'export depuis le conteneur `tracker`

**Décision**: importable tel quel, aucun changement de chemin.

**Rationale**: `Dockerfile` → `WORKDIR /app` puis `COPY . .` ; le service `tracker` lance `streamlit run tracker.py` depuis `/app`. Le script d'export est à la racine du repo donc à `/app/export_jobs.py`, dans le même `sys.path` que `tracker.py`. Confirmé par lecture du `docker-compose.yml` + `Dockerfile`.

## 4. Sécurité de `build_export_rows` quand la DB est absente (UI vs CLI)

**Décision**: `build_export_rows` lève une exception (ex. `FileNotFoundError`) si `db_path` n'existe pas ; `main()` l'attrape, imprime l'erreur sur stderr et `sys.exit(1)` (comportement CLI inchangé) ; la page UI l'attrape et affiche `st.error`.

**Rationale**: le code actuel fait `sys.exit(1)` sur DB absente. Si l'UI appelait directement ce chemin, `sys.exit` tuerait le process Streamlit. Extraire l'erreur en exception découple proprement CLI (exit 1) et UI (message), tout en préservant le comportement CLI byte-identique.

## 5. `rows_to_csv_bytes` et la sémantique « pas de CSV vide »

**Décision**: `rows_to_csv_bytes(rows)` sérialise avec `csv.DictWriter` (en-tête = `rows[0].keys()`), encodé `utf-8-sig`, `newline=""`. Si `rows` est vide → retourne `b""` (aucun octet), miroir exact du garde actuel. La page UI désactive le bouton « Télécharger » quand `rows` est vide.

**Rationale**: conserver la garantie « pas de CSV vide » de la CLI, tout en fournissant un buffer en mémoire pour `st.download_button`.

## 6. Nom de fichier du preset (mois vs plage)

**Décision**: le preset dérive le nom du mode actif — mois → `{base}_YYYY-MM.csv` ; plage → `{base}_YYYY-MM-DD_YYYY-MM-DD.csv`. La CLI, elle, garde son nom historique inchangé.

**Rationale**: le brief impose cette convention pour le preset (FR-009) ; la CLI ne doit pas changer (non-régression). Les deux logiques de nommage sont donc distinctes : celle du preset est paramétrée par le mode, celle de `main()` reste le mois du `date_from`.

## 7. Convention de session-state

**Décision**: clés préfixées `reports_*` (ex. `reports_preset`, `reports_mode`, `reports_month`, `reports_from`, `reports_to`, `reports_statuses`, `reports_rows`, `reports_generated`), conformément à la convention `jobs_*`/`bg_*` de CLAUDE.md.

**Rationale**: éviter toute collision entre pages ; la spec impose session-state uniquement (aucune persistance DB).

## 8. Statuts exposés dans l'UI

**Décision**: le `st.multiselect` expose les statuts valides de la CLI : `new, queued, ready, applied, rejected, archived, expired` (défaut `["applied"]`).

**Rationale**: aligné sur les constantes `STATUSES_ALL` / `STATUSES_DEFAULT` du script d'export ; le mapping vers « en suspens »/« négatif » reste celui de `STATUS_TO_RESULTAT` (inchangé).

## 9. Nommage générique des fonctions extraites

**Décision**: la fonction de construction des lignes extraite du script d'export est nommée `build_export_rows` (nom générique, au lieu du nom ORP-spécifique du brief) ; la sérialisation `rows_to_csv_bytes` est déjà générique.

**Rationale**: le registre sert tout futur preset ; la fonction extraite est le *row-builder du premier preset*, pas un composant ORP. Un nom générique reflète qu'elle est une implémentation parmi d'autres derrière l'interface `generate(...)`. Le fichier source est renommé `export_orp.py` → `export_jobs.py` (dé-branding ORP : le script ne sert plus exclusivement l'ORP mais tout futur preset) ; seul l'en-tête de colonne « Assignation ORP » reste (contrat de données).
