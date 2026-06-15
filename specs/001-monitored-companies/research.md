# Research: Monitored Companies

**Feature**: 001-monitored-companies
**Date**: 2026-06-15

All research decisions were provided in the technical plan (Partie 2) and clarifications (Partie 1). No open unknowns.

## Decision 1: Monitoring state storage

- **Decision**: Single `monitored` boolean on the `companies` table. No join table, no per-profile dimension.
- **Rationale**: Single-user-per-installation deployment model (each user clones the repo with their own DB). Multi-user is handled by separate installations, not multi-tenant. The existing `profile_id` seam on jobs/scores is preserved but not extended to monitoring.
- **Alternatives considered**: Per-profile monitoring table (rejected — adds complexity for a multi-user scenario that doesn't exist).

## Decision 2: Pause mechanism

- **Decision**: Pause = `monitored = false`. Scraping configuration (`ats_provider`, `ats_identifier`, `scraper_id`) persists and is never deleted for a simple pause.
- **Rationale**: Simplest possible model. No separate `monitoring_paused` column needed — the monitored flag IS the toggle.
- **Alternatives considered**: Separate `monitoring_paused` boolean (rejected — redundant; a company is either being monitored or it isn't).

## Decision 3: Title gate placement

- **Decision**: Deterministic skip just before the LLM call in `score.py`, not a scraper-level filter. Jobs are written to DB with a `filtered_non_product` disposition.
- **Rationale**: Respects the constitution's Principle I (scraper = broad, scorer = all filtering) while pragmatically protecting the Groq quota (1000 req/day). The ATS returns all openings in a single call — there's no per-job fetch to avoid, so filtering at scrape time provides no latency benefit. Writing the disposition to DB preserves auditability.
- **Alternatives considered**: Filter at scraper level (rejected — violates Principle I, loses audit trail). Filter at extraction time (rejected — extraction is profile-independent, the title gate is also profile-independent, but placement before LLM call is the correct choke point).

## Decision 4: Title matching algorithm

- **Decision**: Case-insensitive substring matching against an inclusion list. Never exact equality. Errs on inclusion — false positives cost one LLM call, false negatives lose a target.
- **Rationale**: The inclusion list covers all PM title variants observed in the wild. Substring matching handles compound titles ("Senior Tech Product Owner", "Product Manager, Crypto"). The safety rule ensures the FELFEL regression case always passes.
- **Alternatives considered**: Regex-based matching (rejected — overengineered for a simple inclusion list). LLM-based classification (rejected — defeats the purpose of quota protection).

## Decision 5: Scoring signal mechanism

- **Decision**: Monitoring provenance is injected into the LLM `scoring_context` as prose, not as a hard-coded arithmetic bonus. The context is applied via the existing `--apply-context` pathway (prose path, not code).
- **Rationale**: Consistent with Principle IV (LLM produces prose, structured decisions are deterministic) and Principle II (scoring context = prose path, not code). The LLM can weigh monitoring against all other signals holistically.
- **Alternatives considered**: Hard-coded +2 point bonus (rejected — violates Principle IV, can't adapt to context). Separate scoring dimension (rejected — overengineered).

## Decision 6: Scraper taxonomy

- **Decision**: Three sub-packages under `scrapers/`: `boards/` (aggregation, query-driven), `ats/` (company-keyed, identifier-driven), `company_sites/` (bespoke, dev-contributed). BaseScraper ABC preserved at `scrapers/base.py`.
- **Rationale**: Directly implements the constitution's Principle VIII taxonomy. Separates concerns: boards evolve with search terms, ATS adapters evolve with API changes, company sites are rare one-offs. Each can be invoked independently.
- **Alternatives considered**: Flat directory with naming convention (rejected — doesn't scale, doesn't match constitution). Plugin system with entry points (rejected — overengineered for a solo project).

## Decision 7: Greenhouse migration strategy

- **Decision**: The hard-coded `CRYPTO_WEB3_BOARDS` list in `greenhouse.py` becomes rows in the `companies` table (`ats_provider='greenhouse'`, `ats_identifier=<token>`). The scraper reads companies where `monitored = true AND ats_provider = 'greenhouse'`.
- **Rationale**: Moves configuration from code to data (Principle II — prose path). The ~30 boards serve as seed data. Users add their own targets via the UI. The adapter becomes a pure function: iterate monitored Greenhouse companies → fetch all jobs.
- **Alternatives considered**: Keep constant + add DB override (rejected — two sources of truth). Config file (rejected — adds a third configuration surface beyond code and DB).

## Decision 8: HTTP stack

- **Decision**: Reuse the existing stack (`httpx` / `curl_cffi` impersonation, proxy `gluetun-scrape`). No new dependencies.
- **Rationale**: Principle V (surgical edits). The existing stack already handles rate-limiting, impersonation, and proxy routing for all current scrapers. Adding new ATS adapters on the same stack is zero-cost.
- **Alternatives considered**: `aiohttp` for async (rejected — new dependency, no pressing need at hundreds-of-companies scale). `playwright`/`selenium` (rejected — headless browser = new dependency, explicitly out of scope).

## Decision 9: ATS providers (MVP scope)

- **Decision**: Priority 1: Greenhouse, Lever, Ashby. Priority 2: Workday (JSON endpoint, no browser needed), SmartRecruiters (public API), Workable. Oracle HCM and SAP SuccessFactors deferred (JS-heavy, require headless browser).
- **Rationale**: Priority 1 covers the crypto/Web3 cluster (Fireblocks=Greenhouse, Kraken=Ashby, etc.). Priority 2 covers Swiss finance (Swissquote=SmartRecruiters, Lombard Odier=Workday). Deferred providers would require a new dependency (headless browser), violating Principle V.
- **Alternatives considered**: All providers at once (rejected — inflates scope, introduces headless browser dependency).

## Decision 10: Migration pattern

- **Decision**: Dedicated `migrations` table tracking applied migrations, replacing the `COUNT(*) = 0` guard pattern.
- **Rationale**: The current pattern (check if a column exists by counting rows) is fragile. A dedicated migrations table is explicit, idempotent, and already used in projects of this complexity. Follow the pattern used for `comp_flag`.
- **Alternatives considered**: Keep `COUNT(*) = 0` guard (rejected — fragile, already identified as suboptimal). Alembic/SQLAlchemy migrations (rejected — new dependency, overkill for SQLite).
