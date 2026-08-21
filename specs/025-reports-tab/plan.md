# Implementation Plan: Onglet Reports — exports CSV téléchargeables (registre de presets)

**Branch**: `025-reports-tab` | **Date**: 2026-08-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/025-reports-tab/spec.md`

## Summary

Ajouter un onglet « Reports » au tracker (nouvelle page `tracker_views/reports.py` enregistrée dans `tracker.py`), qui génère des exports CSV téléchargeables depuis le navigateur. L'export est piloté par un **registre de presets** extensible ; le premier (et unique) preset réutilise la logique validée du script `export_jobs.py` (renommé depuis `export_orp.py`). Ce script est refactoré en fonctions importables (`build_export_rows`, `rows_to_csv_bytes`) **sans changement de comportement CLI ni de format de colonnes** (seul le préfixe du nom de fichier change : `orp_` → `jobs_`).

## Technical Context

**Language/Version**: Python 3.11 (f-strings, `X | Y` unions, dataclasses)

**Primary Dependencies**: Streamlit (UI) + stdlib (`sqlite3`, `csv`, `io`, `argparse`, `calendar`, `datetime`, `pathlib`, `sys`). **Aucune nouvelle dépendance.**

**Storage**: SQLite `data/jobs.db` (WAL). Lecture seule. L'export lit `jobs` + `job_tracking` via raw `sqlite3` dans `export_jobs.py` (pattern stdlib existant, sanctionné par spec 008 FR-016). Aucune nouvelle table, aucun accès `JobStorage` nécessaire pour l'export lui-même.

**Testing**: Non-régression CLI par comparaison byte-identique (avant/après refactor) sur un mois connu ; vérification manuelle de l'UI via `streamlit run tracker.py`. Pas de nouveau test unitaire requis (aucun fichier de la liste NEVER n'est touché).

**Target Platform**: macOS (dev) + conteneur Docker `tracker` (Ubuntu, `/app`, `streamlit run tracker.py`).

**Project Type**: Web app Streamlit multi-page (`st.navigation` / `st.Page`) + CLI script standalone.

**Performance Goals**: Export < 2s sur 4000+ jobs (requête SQL unique). Génération du CSV en mémoire, aucun I/O disque dans l'UI.

**Constraints**: Aucune nouvelle dépendance. Aucun CSS custom / HTML injecté (composants Streamlit natifs). Aucune écriture disque côté UI. Ne pas toucher `storage.py`, `models.py`, `profiles.py`, `scrape.py`, `scorer.py`, `main.py`, ni aucun scraper. Le script d'export reste à la racine.

**Scale/Scope**: 1 fichier refactoré et renommé (`export_jobs.py`, ex-`export_orp.py`), 1 nouveau fichier (`tracker_views/reports.py`), 1 ligne ajoutée dans `tracker.py`. Registre à 1 preset.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Filet large, point de filtrage unique | ✅ N/A | Export en lecture seule ; aucun jugement de fit. |
| II. Deux chemins d'amélioration | ✅ Pass | Chemin **code** — refactor + nouvelle page, git → deploy. |
| III. Profil unifié unique | ✅ Pass | Export couvre tous les jobs, indépendant du profil (comme la CLI actuelle). |
| IV. Structure déterministe, prose LLM uniquement | ✅ Pass | Aucun LLM ; mapping statut→résultat 100 % déterministe. |
| V. Modification chirurgicale | ✅ Pass | Diff minimal : refactor en place d'un script, 1 nouveau fichier, 1 ligne dans `tracker.py`. Non-goals explicites posés dans la spec. |
| VI. Validation empirique avant livraison | ✅ Pass | Test de non-régression byte-identique de la CLI + vérif UI sur dev. |
| VII. Sécurité d'abord | ✅ Pass | Lecture seule, octets en mémoire, aucun nouvel egress réseau, aucun secret. |
| VIII. Scrapers organisés par modèle d'acquisition | ✅ N/A | Pas un scraper. |
| IX. Le scoring est une couche optionnelle | ✅ Pass | Lit uniquement `jobs` + `job_tracking` ; fonctionne sans scores. |

**Verdict**: Aucune violation. Tous les principes applicables passent.

**Note (deviation sanctionnée, pas une violation)** : `export_jobs.py` accède à la DB en raw `sqlite3` hors de `storage.py`. C'est une exception **pré-existante et explicite** (spec 008 FR-016 : script stdlib standalone). Le refactor ne l'élargit pas : la même requête read-only est simplement extraite dans `build_export_rows`, et le preset UI ne fait que l'appeler.

## Project Structure

### Documentation (this feature)

```text
specs/025-reports-tab/
├── plan.md              # This file (/speckit-plan output)
├── spec.md              # Feature specification
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── export-contract.md
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── mockup.html          # Design authority (ne pas déplacer/renommer)
```

### Source Code (repository root)

```text
export_jobs.py            # RENOMMÉ (ex-export_orp.py) + REFACTOR in place — fonctions importables + main() wrapper
tracker.py                # +1 ligne : st.Page("tracker_views/reports.py", title="Reports", …)
tracker_views/
└── reports.py            # NOUVEAU — page Reports (registre de presets + UI)
```

**Structure Decision**: Pas de nouveau dossier `src/`. `export_jobs.py` reste à la racine (importable depuis `tracker.py` et la page, même `WORKDIR /app` en conteneur). La page suit le pattern existant des `tracker_views/*.py` : imports depuis `tracker_views.shared`, fonction `render()`, garde `is_active_page(__file__)`.

## Complexity Tracking

Aucune violation. Table laissée vide intentionnellement.
