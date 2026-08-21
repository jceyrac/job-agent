# Implementation Plan — Free-Work.com Scraper

**Branch**: `main` (spec 025) | **Date**: 2026-08-06 | **Spec**: [025 - free-work-scraper-spec.md](./025%20-%20free-work-scraper-spec.md)

**Input**: Feature specification + `docs/recon/free-work.md` reconnaissance report

---

## Summary

Ajouter un scraper board `free_work` qui interroge l'API JSON publique de Free-Work.com (`api.free-work.com/job_postings`) pour collecter ~563 offres PM/PO/Agile/Data via 11 slugs métier. API-first, pas de scraping HTML, pas de page détail, pas d'auth. ~150 lignes de Python calquées sur `scrapers/boards/hh_network.py`. Le scraper est découvert automatiquement par `scrape.py` via `discover_scrapers()`.

---

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: `requests` (HTTP), `json` (stdlib), `datetime` (stdlib), `re` (HTML stripping). Aucune nouvelle dépendance.

**Storage**: N/A — le scraper ne touche pas à la DB. Il retourne `list[JobPosting]` ; `scrape.py` appelle `db.save_unscored()`.

**Testing**: Manuel + intégration (`python scrape.py`). Pas de tests unitaires isolés (le scraper hit une API live). Vérification empirique sur échantillon (critères d'acceptation §10 de la spec).

**Target Platform**: Linux server (prod) + macOS (dev) — zéro différence (Python stdlib + requests).

**Project Type**: Scraper board (single-file addition à `scrapers/boards/`).

**Performance Goals**: ~20 requêtes API à 1 req/s → ~20-25 secondes par run complet. Acceptable pour un run cron.

**Constraints**: 1 req/s entre slugs, itération séquentielle, pas de parallélisme. Déduplication par `id` API.

**Scale/Scope**: 11 slugs, ~563 offres estimées, 1 fichier (~150 lignes).

---

## Constitution Check

*GATE: Must pass before implementation. Re-check after implementation.*

| Principe | Applicable ? | Conformité |
|----------|-------------|------------|
| **I. Filet large** | ✅ | Le scoping par slugs est un choix "où chercher" (équivalent aux query terms). Aucun filtrage de désirabilité. Le scoring reste dans `scorer.py`. |
| **II. Deux chemins** | ✅ | La liste de slugs est une constante de code (`FREE_WORK_SLUGS`), modifiable sans toucher la logique de fetch. Changement de slugs = changement de code (pas de prose). |
| **III. Profil unifié** | ✅ | Le scraper est profile-agnostic. `fetch()` reçoit un `JobFilter` pour le scoping titre. |
| **IV. Structure déterministe** | ✅ | `work_mode`, `contract_type`, `geo_zone` sont dérivés de façon déterministe (mapping direct API → JobPosting). Aucun LLM dans le scraper. |
| **V. Modification chirurgicale** | ✅ | Un seul fichier ajouté (`scrapers/boards/free_work.py`). Aucune modification de fichier existant. |
| **VI. Validation empirique** | ✅ | Le scraper sera vérifié contre l'échantillon FELFEL (régression) + vérification manuelle des mappings sur 5-10 offres. |
| **VII. Sécurité d'abord** | ✅ | Pas de secrets, pas d'auth. Egress contrôlé : uniquement `api.free-work.com`. User-Agent identifiable. |
| **VIII. Modèle d'acquisition** | ✅ | `ACQUISITION_MODEL = "board"`, `SUPPORTS_DISCOVERY = True`. Le scraper est découvert automatiquement et participe au filet large. |
| **IX. Scoring optionnel** | ✅ | Le scraper produit des `JobPosting` sans score. Fonctionne sans profil ni clé LLM. |

**Gate**: ✅ Tous les principes applicables sont respectés. Aucune violation à justifier.

---

## Project Structure

### Documentation (this feature)

```text
specs/025-free work scraper/
├── 025 - free-work-scraper-spec.md    # Feature specification
├── plan.md                              # This file
├── research.md                          # Phase 0 output
├── data-model.md                        # Phase 1 output
├── quickstart.md                        # Phase 1 output
└── tasks.md                             # Phase 2 output (via /speckit-tasks)
```

### Source Code (repository root)

```text
# Single file addition — no structural changes
scrapers/boards/
├── free_work.py        # NEW — le scraper
├── hh_network.py       # (template de référence inchangé)
└── ...
```

**Structure Decision**: Single-file scraper dans `scrapers/boards/`, découverte automatique par `discover_scrapers()`. Zéro modification hors de ce fichier.

---

## Complexity Tracking

> Aucune violation de la constitution. Section vide.
