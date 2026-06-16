# Tasks 003 — Lever & Workable Scrapers + Greenhouse Board IDs

## Phase 0 — Greenhouse board IDs
- [ ] Read `scraper_greenhouse.py`, locate board ID config
- [ ] Add `"dfinity"` and `"amun"` to the list
- [ ] Smoke test: confirm Dfinity and 21Shares jobs are fetched and stored

## Phase 1 — Lever scraper
- [ ] Create `scraper_lever.py` implementing `BaseScraper`
- [ ] Fetch `https://api.lever.co/v0/postings/{slug}?mode=json` for each slug
- [ ] Map Lever posting → `JobPosting` (all required fields)
- [ ] Strip HTML from description using stdlib `html.parser`
- [ ] Handle HTTP errors gracefully per slug
- [ ] Register `LeverScraper(["impossiblecloud"])` in `scrape.py`
- [ ] Test: Impossible Cloud jobs appear in DB with `source="lever"`

## Phase 2 — Workable scraper
- [ ] Create `scraper_workable.py` implementing `BaseScraper`
- [ ] POST to `https://apply.workable.com/api/v3/accounts/{slug}/jobs`
- [ ] Handle `next_page` pagination token if present
- [ ] Map Workable job → `JobPosting` (all required fields)
- [ ] Handle HTTP errors gracefully per slug
- [ ] Register `WorkableScraper(["walletconnect", "walletconnect-foundation"])` in `scrape.py`
- [ ] Test: WalletConnect jobs appear in DB with `source="workable"`

## Phase 3 — End-to-end verification
- [ ] Full `scrape.py` run — no regressions on existing scrapers
- [ ] DB contains jobs from `lever` and `workable` sources
- [ ] Scoring runs normally on new jobs
