# Implementation Plan: Pre-filter exclusion cleanup (don't store non-PM jobs in DB)

**Branch**: `main` | **Date**: 2026-07-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/014-pre-filter-exclusion-cleanup/spec.md`

## Summary

Add a PM title gate in `scrape.py`'s `_run_broad_scrape()` to discard non-PM jobs before they reach the DB. Uses the existing `is_product_management_title()` from `title_gate.py`. Monitored company jobs (in `_run_monitored_only()`) bypass this gate — they are already filtered by the same function at scoring time in `score.py`. This is a 5-line change with zero new logic.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: None new — `title_gate.py` already imported in `score.py`
**Storage**: SQLite via `JobStorage` — no schema changes
**Testing**: No new tests needed (no storage/model changes); manual verification via pipeline run
**Target Platform**: Linux server (Docker Compose), macOS dev
**Project Type**: CLI pipeline
**Performance Goals**: Negligible — `is_product_management_title()` is a substring check on 12 keywords
**Constraints**: Must not affect monitored company jobs; must not change DB schema
**Scale/Scope**: 1 file changed (`scrape.py`), ~5 lines added

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Filet large, point de filtrage unique | ✅ PASS | The gate is a "filtre grossier de famille" (PM title only), not relevance filtering. The constitution's company-keyed exception explicitly allows this class of filter. For job boards, this is equivalent to the query scoping they already provide via search terms — the spec intentionally avoids location/seniority/sector filtering at scrape time. The scorer remains the sole relevance judge. |
| II. Deux chemins | ✅ PASS | Code path — follows dev → git → deploy. No prose changes. |
| III. Profil unifié | ✅ PASS | Profile-independent gate. No impact on multi-user seam. |
| IV. Structure déterministe | ✅ PASS | Gate is deterministic substring matching, no LLM involved. |
| V. Modification chirurgicale | ✅ PASS | Minimum viable change — one file, one function, one check. |
| VI. Validation empirique | ✅ PASS | Acceptance criteria include pipeline run verification. |
| VII. Sécurité d'abord | ✅ PASS | No new network calls, no secrets, no new dependencies. |
| VIII. Scrapers par modèle | ✅ PASS | Gate applies only to `_run_broad_scrape()`, not `_run_monitored_only()`. Respects the acquisition model separation. |
| IX. Scoring optionnel | ✅ PASS | Scraping still works without LLM/profile — just with fewer irrelevant jobs in DB. |

**Verdict**: All gates pass. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/014-pre-filter-exclusion-cleanup/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
scrape.py               # ← sole code change: add title gate in _run_broad_scrape()
title_gate.py           # existing — is_product_management_title() (unchanged)
score.py                # existing — already imports title_gate (unchanged)
```

**Structure Decision**: Single-file change. No new modules or directories.

## Complexity Tracking

> No violations. Table intentionally left empty.
