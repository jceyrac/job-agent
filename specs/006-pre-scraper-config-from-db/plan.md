# Plan 006-pre — Scraper Config from DB

## Phase 0 — Read before touching anything

Read these files in full before writing a single line:
- `storage.py` — find existing company methods (near `get_watching_companies_by_method`
  or similar; it may already exist with a different name)
- `scrape.py` — understand how scrapers are instantiated and where `db` is available
- `scrapers/greenhouse.py` — locate `GREENHOUSE_BOARDS` and scraper `__init__`
- `scrapers/ats/lever.py` — locate slug config and `__init__`
- `scrapers/ats/workable.py` — locate slug config and `__init__`

This phase produces no code.

## Phase 1 — storage.py

Add `get_watching_companies_by_method(scraping_method: str) -> list[dict]`.
Run a quick sanity check: call it manually (python -c "...") against the dev DB
to confirm it returns the expected companies. At this point there should be zero
rows (all are `watch_ready`, not `watching`) — the seed fallback will kick in,
which is correct.

## Phase 2 — scrapers (one at a time)

For each of the three scrapers:
1. Add `SEED` constant with the existing hardcoded values (preserve them as fallback)
2. Add `get_{provider}_slugs(db)` helper
3. Update `__init__` to accept `db: JobStorage` and call the helper
4. Update `scrape.py` to pass `db` at instantiation
5. Test: run `python scrape.py --mock` (or equivalent dry-run) — confirm the scraper
   still loads the correct slugs from seed (since no companies are `watching` yet)

Order: Greenhouse first (largest impact), then Lever, then Workable.

## Phase 3 — Integration test

1. In Dev DB: set one company to `watching` (e.g. IOHK/Cardano, greenhouse/iohk-cardano)
2. Run scraper in isolation — confirm it picks up the company from DB, not just seed
3. Reset company back to `watch_ready`
4. Run scraper again — confirm it falls back to seed list
5. No regressions: existing `watching` companies (Aave, Alchemy, etc.) still scraped
