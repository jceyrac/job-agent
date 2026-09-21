# Contract: refactor du script d'export + registre de presets

Contrat des interfaces publiques introduites par le refactor, et du premier preset.

## 1. Fonctions importables (nouvelles, dans `export_jobs.py`)

### `build_export_rows(db_path, date_from, date_to, statuses) -> list[dict]`

- **Entrées** : `db_path` (path-like), `date_from` (str `YYYY-MM-DD`), `date_to` (str `YYYY-MM-DD`), `statuses` (list[str]).
- **Sortie** : liste de `ExportRow` (dicts, colonnes du preset), triée par `changed_at ASC`.
- **Comportement** : joint `jobs` + `job_tracking`, filtre `status IN statuses` et `date(changed_at) BETWEEN date_from AND date_to`, puis formate chaque ligne (mapping statut→résultat, description, entreprise+lieu, motif négatif).
- **Erreur** : lève `FileNotFoundError` si `db_path` n'existe pas (ne fait **jamais** `sys.exit`).

### `rows_to_csv_bytes(rows) -> bytes`

- **Entrée** : `rows` (list[dict], format `ExportRow`).
- **Sortie** : octets CSV encodés `utf-8-sig` (BOM inclus), en-tête = `rows[0].keys()`, `newline=""`.
- **Comportement** : si `rows` est vide → retourne `b""` (aucun octet).

## 2. `main()` — wrapper mince (comportement CLI **inchangé**)

- Parse les flags inchangés : `--month YYYY-MM`, `--from YYYY-MM-DD`, `--to YYYY-MM-DD`, `--statuses` (défaut `["applied"]`).
- Résout la plage : priorité `--from/--to` > `--month` > mois courant (avertissement stderr si `--from/--to` et `--month` coexistent).
- Appelle `build_export_rows(DB_PATH, date_from, date_to, statuses)`.
- Attrape `FileNotFoundError` → `print("[ERROR] DB introuvable : {db_path}", file=sys.stderr)` puis `sys.exit(1)`.
- Écrit `data/jobs_{date_from[:7]}.csv` (même contenu ; seul le préfixe du nom est renommé `orp_` → `jobs_`), saute l'écriture si `rows` vide, puis affiche le résumé/aperçu terminal **inchangés**.

## 3. Premier preset (registre)

- `label` = `"Candidatures"` (dé-branding ORP ; le mockup affichait « Candidatures ORP (716.007) »).
- `generate(date_from, date_to, statuses, mode) -> (rows, filename)` :
  - `rows = build_export_rows(DB_PATH, date_from, date_to, statuses)`.
  - `mode == "month"` → `filename = f"jobs_{date_from[:7]}.csv"`.
  - `mode == "range"` → `filename = f"jobs_{date_from}_{date_to}.csv"`.

## 4. Invariants de non-régression

- La CLI produit un CSV dont le **contenu** est byte-identique à la version pré-refactor pour les mêmes arguments (seul le préfixe du nom de fichier change : `orp_` → `jobs_`).
- Les colonnes et leur ordre, le mapping statut→résultat, l'encodage `utf-8-sig`, la résolution de période et les messages terminaux sont **inchangés**.
- Le CSV téléchargé depuis l'UI (mêmes `date_from`/`date_to`/`statuses`) est **byte-identique** à celui de la CLI.

## 5. Contrat générique du registre

Tout futur preset doit exposer la même interface, sans modifier l'UI :

- `label: str` — affiché dans le selectbox.
- `generate(date_from, date_to, statuses, mode) -> (rows: list[dict], filename: str)`.

La page Reports ne connaît que cette interface ; elle n'a aucune logique spécifique à un preset.
