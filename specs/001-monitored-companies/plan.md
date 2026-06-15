# Implementation Plan: Monitored Companies

**Branch**: `001-monitored-companies` | **Date**: 2026-06-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-monitored-companies/spec.md`

## Summary

Add targeted company monitoring to the job agent: a `monitored` boolean on companies, a `--monitored-only` scrape mode that collects all openings from monitored companies via ATS adapters, a deterministic PM-family title gate protecting the Groq quota, a provenance badge in the UI, and a monitoring signal injected into the LLM scoring context. The scraper taxonomy refactors from a flat `scrapers/` directory into `boards/`, `ats/`, and `company_sites/` sub-packages.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: Streamlit (UI), httpx / curl_cffi (HTTP, existing stack), SQLite via JobStorage (persistence). No new dependencies.

**Storage**: SQLite `data/jobs.db` (WAL mode). Extend `companies` table; add `filtered_non_product` disposition on jobs; add `migrations` tracking table. All access through `JobStorage`.

**Testing**: `pytest tests/test_storage.py` (162 unit tests, in-memory DB). Extend for new columns and migration logic. Integration tests per ATS adapter against known companies.

**Target Platform**: macOS dev + Ubuntu server (Docker Compose).

**Project Type**: CLI pipeline + Streamlit UI (single-user per installation).

**Performance Goals**: `--monitored-only` run completes in <60s for 100 monitored companies.

**Constraints**: Groq free tier 1000 req/day — title gate protects quota. No headless browser dependency. Surgical diffs only (Principle V).

**Scale/Scope**: Hundreds of monitored companies, not thousands. 6 ATS adapters (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable) + broad scraping unchanged.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Filet large, point de filtrage unique | ✅ PASS | Scrapers remain broad (pure functions: company → all openings). PM-family gate is post-scrape, pre-LLM — consistent with the company-keyed exception for coarse family filtering. |
| II. Deux chemins d'amélioration | ✅ PASS | Monitoring toggle, ATS provider, and company list are prose/config (DB rows). Scraper adapters, title gate, and UI are code. |
| III. Profil unifié unique | ✅ PASS | Monitoring is profile-independent (simple boolean on companies, not per-profile_id). The profile_id seam on jobs/scores is untouched. |
| IV. Structure déterministe, prose LLM uniquement | ✅ PASS | Title gate is deterministic (substring match). Monitoring signal is injected as LLM prose (scoring context), not hard-coded score bonus. `filtered_non_product` disposition is deterministic. |
| V. Modification chirurgicale | ✅ PASS | Phased (A→E), each independently validated. No opportunistic refactors. No new dependencies. |
| VI. Validation empirique | ✅ PASS | Each phase validated against known companies (Coinbase, Kraken, Fireblocks). FELFEL regression check preserved. |
| VII. Sécurité d'abord | ✅ PASS | No new secrets. Reuses existing HTTP stack. Monitoring config is DB data, not environment variables. |
| VIII. Scrapers organisés par modèle d'acquisition | ✅ PASS | This feature explicitly implements the monitoring path of the discovery/monitoring split defined in Principle VIII. `boards/` = query-driven, `ats/` = company-keyed. `--monitored-only` guarantees autonomous monitoring. |
| IX. Scoring est une couche optionnelle | ✅ PASS | Monitoring runs write jobs to DB. Title gate runs at scoring time. Provenance badge visible even without scoring. |

## Project Structure

### Documentation (this feature)

```text
specs/001-monitored-companies/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 research decisions
├── data-model.md        # Phase 1 data model
├── quickstart.md        # Phase 1 validation guide
└── contracts/           # Phase 1 CLI contracts
    └── cli.md
```

### Source Code (repository root)

```text
scrapers/
├── base.py              # BaseScraper ABC (unchanged)
├── boards/              # Aggregation boards (unchanged query-driven scrapers)
│   ├── linkedin.py
│   ├── indeed.py
│   ├── _jobspy_helpers.py
│   ├── web3career.py
│   ├── remoteok.py
│   ├── weworkremotely.py
│   ├── cryptojobslist.py
│   ├── cryptojobs_com.py
│   ├── defi_jobs.py
│   ├── tietalent.py
│   ├── jobup.py
│   └── wellfound.py
├── ats/                 # Company-keyed ATS adapters (new sub-package)
│   ├── greenhouse.py    # Refactored: reads from companies table, not constant
│   ├── lever.py
│   ├── ashby.py
│   ├── workday.py
│   ├── smartrecruiters.py
│   └── workable.py
└── company_sites/       # Bespoke scrapers (new sub-package, initially empty)
    └── __init__.py

scrape.py                # Gains --monitored-only flag + company-keyed dispatcher
score.py                 # Gains title gate (pre-LLM skip) + monitoring signal injection
storage.py               # Extended: companies columns, filtered_non_product, migrations table
tracker.py               # Global CSS for provenance badge
tracker_views/
├── jobs.py              # Provenance badge rendering
├── job_helpers.py       # Badge in job cards
├── settings.py          # Monitored companies management panel
└── shared.py            # Monitoring helpers (if needed)
```

**Structure Decision**: Single project structure as existing. New sub-packages under `scrapers/` follow the constitution's taxonomy (Principle VIII). Existing `boards/` scrapers are moved verbatim — no logic changes.

## Complexity Tracking

> No violations. All constitution principles pass.

## Phase Breakdown

| Phase | Scope | Validation |
|-------|-------|------------|
| **A** — Data model | Extend `companies` table, add `filtered_non_product` on jobs, add `migrations` table, backfill existing companies from historical jobs | 2-3 seed companies resolve correctly |
| **B** — Scraper taxonomy + Greenhouse + dispatcher | Refactor `scrapers/` into sub-packages, migrate Greenhouse from constant to DB-driven, build company-keyed dispatcher, add `--monitored-only` flag | Parity: the 30 Greenhouse boards produce identical results to current code |
| **C** — ATS adapters | Lever, Ashby, Workday, SmartRecruiters, Workable | Each validated against a known company (Kraken/Ashby, Swissquote/SmartRecruiters, Lombard Odier/Workday) |
| **D** — ATS detection + company addition UI | URL-based ATS detection (hostname match + vanity fetch + manual fallback), add-company form in Settings | Enter a known Greenhouse URL → resolves correctly |
| **E** — Scoring + gate + badge + management UI | Title gate, monitoring signal injection in scoring context, provenance badge `🎯 Monitored · {company}`, Settings management panel | FELFEL regression passes; badge renders independent of score; pause skips in next run |
