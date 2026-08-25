# Implementation Plan: Pagination de la liste Companies

**Branch**: `026-companies-pagination` | **Date**: 2026-08-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/026-companies-pagination/spec.md`

## Summary

Plafonner le nombre de cartes d'entreprises rendues par rerun dans `tracker_views/companies.py`, avec navigation Prev / Next / « Go to », en reproduisant à l'identique le mécanisme de pagination déjà éprouvé dans `tracker_views/jobs.py`. Changement purement au niveau de la vue : aucune modification de la couche SQL/données (`load_companies()`, `get_companies()`, `storage.py`), aucun changement de la disposition des cartes ni des liens `/company_detail`, aucune nouvelle dépendance.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: Streamlit (widgets natifs uniquement — `st.selectbox`, `st.button`, `st.caption`, `st.number_input`, `st.columns`, `st.session_state`)

**Storage**: N/A — aucun changement de stockage. Lecture seule via `load_companies()` / `get_db()` existants (déjà passés par `JobStorage`).

**Testing**: Aucun test automatisé nouveau ni modifié (feature view-layer ; `tests/test_storage.py` n'est pas concerné). Vérification manuelle de la pagination + non-régression de l'UI.

**Target Platform**: Streamlit multi-page tracker (dev Mac M5 Pro ; prod Docker/verva)

**Project Type**: web-app (Streamlit)

**Performance Goals**: rendu responsive avec plusieurs centaines d'entreprises ; la saisie « Search by name » reste fluide (pas de re-rendu de toutes les cartes à chaque caractère).

**Constraints**: aucune nouvelle dépendance ; aucune modification de `storage.py`, `models.py`, `load_companies()`, `get_companies()`, ni d'aucune autre vue ; diff chirurgical limité à `tracker_views/companies.py`.

**Scale/Scope**: un seul fichier (`tracker_views/companies.py`), un seul mécanisme de pagination, trois nouvelles clés de session-state.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principe | Applicable ? | Verdict |
|----------|--------------|---------|
| I. Filet large, point de filtrage unique | Non (vue, pas de scraping/jugement de fit) | N/A |
| II. Deux chemins d'amélioration | Oui | PASS — chemin **Code** (dev → git → deploy) |
| III. Profil unifié unique | Non | N/A |
| IV. Structure déterministe, prose LLM uniquement | Non (aucun LLM) | N/A |
| V. Modification chirurgicale | Oui | PASS — un seul fichier ; non-goals explicites dans la spec ; pas de refactor opportuniste |
| VI. Validation empirique avant livraison | Oui | PASS — vérification manuelle de la navigation + non-régression des cartes/liens |
| VII. Sécurité d'abord | Non (pas de secrets, pas d'egress réseau) | N/A |
| VIII. Scrapers par modèle d'acquisition | Non | N/A |
| IX. Le scoring est une couche optionnelle | Non | N/A |
| Contraintes archi : DB via `JobStorage` | Oui | PASS — aucun accès DB direct ajouté |
| Contraintes archi : No new dependencies | Oui | PASS — Streamlit natif uniquement |
| Contraintes archi : Stable core untouched | Oui | PASS — aucun fichier protégé touché |

**Verdict**: aucune violation. `Complexity Tracking` non requis.

## Project Structure

### Documentation (this feature)

```text
specs/026-companies-pagination/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
tracker_views/
└── companies.py         # SEUL fichier modifié — ajout de la pagination
```

**Structure Decision**: Changement unique, dans un seul fichier (`tracker_views/companies.py`), qui est déjà classé « Safe to modify » dans `CLAUDE.md`. Aucun nouveau fichier, module, ni répertoire de code source.

## Complexity Tracking

> Aucune violation de constitution — non requis.
