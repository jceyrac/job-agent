# Plan 004a — Company Researcher Improvements

## Phase 0 — Fix 1: Trust existing DB data (5 min)

Read `company_researcher.py` and `storage.py`.

Add `existing: dict | None = None` param to `research_company()`.
Add early-return block at the top: if `existing["ats_provider"]` and
`existing["ats_board_slug"]` both set → return `ResearchResult` from DB data,
`detection_method="existing_data"`, `confidence="high"`.

Update `research_all_pending()` to pass `existing=company`.

Add guard in `storage.update_company_research()`: if
`result.detection_method == "existing_data"`, skip write (or only update
`researched_at`).

Quick test: run researcher on 21Shares → should return existing_data/high, no fetch.

## Phase 1 — Fix 2: Scan all careers links (10 min)

Rewrite `_find_careers_link()`:
- Collect all href matches (skip anchors and javascript: links)
- For each candidate, check against ATS_SIGNATURES URL patterns
- Return ATS-containing URL immediately if found
- Else return first valid candidate

Test with a company that has multiple careers links on homepage.

## Phase 2 — Fix 3: Iframe + script scanning (15 min)

Extend `_detect_ats()`:
- Extract all `<iframe src="...">` values with regex
- Extract `<script>` tag content (first 2000 chars each)
- Append to `combined` before running ATS signature scan

Test with a company known to embed Greenhouse via iframe (e.g. a company with
`<iframe src="https://boards.greenhouse.io/...">` on their careers page).

## Phase 3 — Fix 4: JS-rendered fallback (15 min)

Add `CAREERS_URL_GUESSES` list and `_try_careers_url_guesses(base_url)` function.

Integrate into `research_company()` after homepage fetch:
- If `html is None` OR `len(html) < 5000`: call `_try_careers_url_guesses(url)`
- If hit found: run `_detect_ats()` on result, continue pipeline
- If no hit: mark `none/high`, return

## Phase 4 — End-to-end retest

Reset `researched_at` for the 11 Tier A companies in the DB (or use `--all` on
a fresh import), rerun researcher, compare results to the table from the
first test run. Expected improvements:
- 21Shares → `existing_data / high` (Fix 1)
- Arrakis Finance → `none / high` (no regression)
- Any Greenhouse-iframe site → `greenhouse / high` (Fix 3)
- Any Next.js site → careers URL found via guessing (Fix 4)
