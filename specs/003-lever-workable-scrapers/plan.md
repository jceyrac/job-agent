# Plan 003 — Lever & Workable Scrapers + Greenhouse Board IDs

## Phase 0 — Greenhouse board IDs (5 min, config only)

Read `scraper_greenhouse.py` to locate the board ID list. Add `"dfinity"` and `"amun"`. Run a quick smoke test to confirm jobs are fetched. Done.

## Phase 1 — Lever scraper

1. Read `scraper_greenhouse.py` as the reference pattern for a new scraper.
2. Read `models.py` to confirm `JobPosting` fields and types.
3. Create `scraper_lever.py`:
   - `GET https://api.lever.co/v0/postings/{slug}?mode=json`
   - Loop over `company_slugs`, fetch each, parse JSON array
   - Map each posting to `JobPosting` (see field mapping in spec)
   - Strip HTML from `descriptionPlain` using `html.parser` (stdlib, no new dep)
   - Handle HTTP errors gracefully (log warning, continue to next slug)
4. Register in `scrape.py` / `main.py` — instantiate `LeverScraper(["impossiblecloud"])`
5. Test: run `python scrape.py --mock` or equivalent, verify Impossible Cloud jobs appear in DB

## Phase 2 — Workable scraper

1. Create `scraper_workable.py`:
   - `POST https://apply.workable.com/api/v3/accounts/{slug}/jobs` with empty filter body
   - Handle pagination: if response contains `next_page` token, loop until exhausted
   - Map each job to `JobPosting`
   - Location: concatenate `city` + `country` from `location` object if present
2. Register in `scrape.py` / `main.py` — instantiate `WorkableScraper(["walletconnect", "walletconnect-foundation"])`
3. Test: verify WalletConnect jobs appear in DB

## Phase 3 — Verify end-to-end

Run full `scrape.py` pass. Check DB for new jobs from `lever` and `workable` sources. Confirm no regressions on existing scrapers.
