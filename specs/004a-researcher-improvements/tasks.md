# Tasks 004a — Company Researcher Improvements

## Phase 0 — Fix 1: Trust existing DB data
- [x] Read `company_researcher.py` in full
- [x] Read `storage.py` — locate `update_company_research()`
- [x] Add `existing: dict | None = None` to `research_company()` signature
- [x] Add early-return block: if `existing["ats_provider"]` + `existing["ats_board_slug"]`
      → return `ResearchResult` with `detection_method="existing_data"`, `confidence="high"`
- [x] Update `research_all_pending()`: pass `existing=company` to `research_company()`
- [x] Update `storage.update_company_research()`: skip write if `detection_method == "existing_data"`
      (or only update `researched_at` timestamp)
- [x] Test: run researcher on 21Shares → `existing_data / high`, no HTTP fetch occurs

## Phase 1 — Fix 2: Scan all careers links
- [x] Rewrite `_find_careers_link(html, base_url)`:
  - [x] Collect all href matches, skip `#` and `javascript:` hrefs
  - [x] For each candidate, run ATS signature URL check
  - [x] Return ATS URL immediately if found
  - [x] Else return first valid non-anchor candidate
- [x] Test: company with multiple links — correct careers page selected

## Phase 2 — Fix 3: Iframe + script scanning
- [x] Extend `_detect_ats(html, url)`:
  - [x] Extract `<iframe src="...">` values with regex
  - [x] Extract `<script>` tag bodies (first 2000 chars each) with regex
  - [x] Append both to `combined` string before ATS scan loop
- [x] Test: verify iframe-embedded Greenhouse board is detected
- [x] Ensure script extraction does not cause significant slowdown (cap at 10 scripts)

## Phase 3 — Fix 4: JS-rendered fallback
- [x] Add `CAREERS_URL_GUESSES` list (8 patterns)
- [x] Add `_try_careers_url_guesses(base_url) -> tuple[str | None, str | None]`
- [x] Integrate into `research_company()`:
  - [x] Trigger when `html is None` OR `len(html) < 5000`
  - [x] On hit: run `_detect_ats()`, continue pipeline normally
  - [x] On miss: `scraping_method="none"`, `confidence="high"`, return
- [x] Test: JS-rendered site (e.g. 21.co) → careers URL found via guessing

## Phase 4 — End-to-end retest
- [x] Reset `researched_at` for Tier A companies (or fresh import)
- [x] Run `python company_researcher.py --all`
- [x] Compare to first test results:
  - [x] 21Shares → `existing_data / high` ✅
  - [x] Arrakis Finance → `none / high` (no regression) ✅
  - [x] Any iframe-Greenhouse company → `greenhouse / high` ✅
  - [x] Any JS-rendered site → careers URL found or `none/high` ✅
- [x] No crash on any of the 11 Tier A companies
