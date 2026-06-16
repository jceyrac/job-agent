# Tasks 003 — Lever & Workable Scrapers + Greenhouse Board IDs

## Phase 0 — Greenhouse board IDs
- [x] Read `scraper_greenhouse.py`, locate board ID config
- [x] Add `"dfinity"` and `"amun"` to the list
- [x] Smoke test: confirm Dfinity and 21Shares jobs are fetched and stored

## Phase 1 — Lever scraper
- [x] Create `scraper_lever.py` implementing `BaseScraper`
- [x] Fetch `https://api.lever.co/v0/postings/{slug}?mode=json` for each slug
- [x] Map Lever posting → `JobPosting` (all required fields)
- [x] Strip HTML from description using stdlib `html.parser`
- [x] Handle HTTP errors gracefully per slug
- [x] Register `LeverScraper(["impossiblecloud"])` in `scrape.py`
- [x] Test: Impossible Cloud jobs appear in DB with `source="lever"`

## Phase 2 — Workable scraper
- [x] Create `scraper_workable.py` implementing `BaseScraper`
- [x] POST to `https://apply.workable.com/api/v3/accounts/{slug}/jobs`
- [x] Handle `next_page` pagination token if present
- [x] Map Workable job → `JobPosting` (all required fields)
- [x] Handle HTTP errors gracefully per slug
- [x] Register `WorkableScraper(["walletconnect", "walletconnect-foundation"])` in `scrape.py`
- [x] Test: WalletConnect jobs appear in DB with `source="workable"`

## Phase 3 — End-to-end verification
- [x] Full `scrape.py` run — no regressions on existing scrapers
- [x] DB contains jobs from `lever` and `workable` sources
- [x] Scoring runs normally on new jobs
