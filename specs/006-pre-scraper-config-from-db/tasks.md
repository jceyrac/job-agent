# Tasks 006-pre — Scraper Config from DB

## Phase 0 — Read (no code)
- [ ] Read `storage.py` — confirm `get_watching_companies_by_method` does not exist yet
- [ ] Read `scrape.py` — note where scrapers are instantiated, where `db` is available
- [ ] Read `scrapers/greenhouse.py` — note full `GREENHOUSE_BOARDS` list + `__init__` signature
- [ ] Read `scrapers/ats/lever.py` — note slug config + `__init__` signature
- [ ] Read `scrapers/ats/workable.py` — note slug config + `__init__` signature
- [ ] Confirm: 30 legacy `watching` companies have `scraping_method = NULL` in DB
      (`SELECT name, scraping_method FROM companies WHERE monitoring_status='watching'`)

## Phase 1 — storage.py
- [ ] Add `get_watching_companies_by_method(self, scraping_method: str) -> list[dict]`
      — filters `monitoring_status = 'watching' AND scraping_method = ? AND ats_identifier IS NOT NULL`
- [ ] Sanity check: call it for 'greenhouse' → should return 0 rows (all Swiss companies
      still at `watch_pending`), confirming legacy companies with NULL are excluded as expected

## Phase 2a — Greenhouse scraper
- [ ] Rename `GREENHOUSE_BOARDS` → `GREENHOUSE_BOARDS_SEED` (keep ALL existing slugs intact)
- [ ] Add `get_greenhouse_boards(db) -> list[str]`:
      — query DB via `get_watching_companies_by_method("greenhouse")`
      — merge: `list(dict.fromkeys(GREENHOUSE_BOARDS_SEED + db_slugs))`
      — seed first so legacy companies are always covered
- [ ] Update `__init__` to accept `db: JobStorage`, call `get_greenhouse_boards(db)`
- [ ] Update `scrape.py` — pass `db` to Greenhouse scraper instantiation
- [ ] Test: run scraper in isolation — verify same slugs as before (seed covers legacy)

## Phase 2b — Lever scraper
- [ ] Rename slug constant → `LEVER_SLUGS_SEED` (keep `["impossiblecloud"]`)
- [ ] Add `get_lever_slugs(db) -> list[str]` with merge pattern
- [ ] Update `__init__` to accept `db`
- [ ] Update `scrape.py` — pass `db` to Lever scraper instantiation
- [ ] Test: run scraper — no regression

## Phase 2c — Workable scraper
- [ ] Rename slug constant → `WORKABLE_SLUGS_SEED` (keep `["walletconnect", "walletconnect-foundation"]`)
- [ ] Add `get_workable_slugs(db) -> list[str]` with merge pattern
- [ ] Update `__init__` to accept `db`
- [ ] Update `scrape.py` — pass `db` to Workable scraper instantiation
- [ ] Test: run scraper — no regression

## Phase 3 — Integration test
- [ ] Set IOHK/Cardano to `watch_ready` then `watching` in Dev DB
- [ ] Run Greenhouse scraper — verify `iohk-cardano` appears in slug list (from DB)
- [ ] Verify legacy slugs (aave, circle, etc.) still present (from seed)
- [ ] Reset IOHK/Cardano back to `watch_pending`
- [ ] Full `scrape.py` run — no regressions on existing `watching` companies
- [ ] Log output shows warning for any `watching` company with NULL `ats_identifier`
