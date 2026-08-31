# Implementation Plan: Joinup Scraper (Swiss startup board)

**Branch**: `main` | **Date**: 2026-08-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/027-joinup-scraper/spec.md`

## Summary

Add a new aggregation (board) scraper for Joinup (https://joinup.ch). It reads the
server-rendered `__NEXT_DATA__` JSON embedded in `/browse/jobs` — the same
structured-JSON pattern already used by TieTalent — and paginates the SSR'd
1-indexed `?page=N` query param. Zero relevance filtering (wide net); the only
early stop is the `JobFilter.date_from` freshness bound. No new dependencies
(httpx already present). Primary parse is `json`, not BeautifulSoup.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: `httpx` (already in requirements.txt); stdlib `json`,
`re`, `ast`, `datetime`. No new dependencies.

**Storage**: N/A for the scraper itself — returns `list[JobPosting]`; the caller
(`scrape.py`) persists via `JobStorage`. `normalize_url` is passthrough for this
source (only `linkedin`/`indeed` are special-cased), so Joinup URLs dedup as-is
and the derived `JobPosting.id` is stable.

**Testing**: pytest — one new offline test `tests/test_joinup.py` running against
captured fixtures (never the network), plus a `JoinupScraper` entry in
`tests/scraper_checks.py` (live, optional-fields).

**Target Platform**: DEV-only (Mac) via Claude Code; verva is deploy-only.

**Project Type**: Python pipeline scraper (existing `BaseScraper` subclass).

**Performance Goals**: 10 hits/page × up to 120 pages; 0.5s courtesy delay per
page. Real bound is the date-cutoff early stop (usually a handful of pages).

**Constraints**: Wide net — no relevance filtering in the scraper. No
scrape-time classification of `geo_zone`/`company_size`/`contract_type`/
`company_country`/`comp_*`. Surgical: one scraper file + one test + one
`scraper_checks` entry; touch no other module.

**Scale/Scope**: ~1115 jobs, 112 pages of 10. Pagination is credential-free and
SSR'd — no Typesense key, no login.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Wide net, single filter point | ✅ PASS | `fetch()` collects all parsed hits; only early stop is `date_from` (freshness, not relevance). Fit stays with the scorer. |
| II. Two improvement paths | ✅ PASS | Pure Code change (new scraper). No prose/config payload. |
| III. Unified profile | ✅ PASS | No profile logic touched; scraper is profile-agnostic. |
| IV. Deterministic structure, LLM prose only | ✅ PASS | `work_mode` derived deterministically (`location == "Remote"`); no LLM in the scraper. |
| V. Surgical modification | ✅ PASS | One new file `scrapers/boards/joinup.py` + `tests/test_joinup.py` + one `scraper_checks.py` entry. No refactor, no new dep. |
| VI. Empirical validation | ✅ PASS | Offline tests assert the id-10230 mapping and the date-cutoff early stop against real fixtures. |
| VII. Security first | ✅ PASS | Scraper contacts only `https://joinup.ch`; no secrets; read-only GET. |
| VIII. Acquisition models | ✅ PASS | `ACQUISITION_MODEL = "board"`, `SUPPORTS_DISCOVERY = True` → participates in broad sweep, skipped by `--monitored-only`. |
| IX. Scoring optional | ✅ PASS | Scraper returns `JobPosting` regardless of profile/LLM; no scoring dependency. |

**Result**: No violations. Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/027-joinup-scraper/
├── spec.md
├── plan.md              # this file
├── research.md          # __NEXT_DATA__ structure + live-source findings
├── data-model.md        # no new entities (reuses JobPosting)
└── quickstart.md        # validation commands
```

(`contracts/` is omitted — this is an internal scraper with no external
interface; its contract is the existing `BaseScraper.fetch() → list[JobPosting]`.)

### Source Code (repository root)

```text
scrapers/boards/joinup.py        # NEW — JoinupScraper (board convention, TieTalent analog)
tests/test_joinup.py             # NEW — offline fixture test + wide-net lock
tests/scraper_checks.py          # EDIT — add JoinupScraper entry (optional_fields)
tests/fixtures/joinup_browse_jobs.html        # already captured
tests/fixtures/joinup_browse_jobs_page2.html  # already captured
```

**Structure Decision**: The scraper lives in `scrapers/boards/joinup.py` — the
same subpackage as TieTalent (the `__NEXT_DATA__` analog) and every other board
scraper. `discover_scrapers()` already scans `scrapers/boards/`. (The spec's
shorthand `scrapers/joinup.py` resolves to the boards subpackage to match the
existing convention.)

## Implementation phases

### Phase 1 — `scrapers/boards/joinup.py`

1. `JoinupScraper(BaseScraper)` with `SOURCE_NAME="Joinup"`, `ENABLED=True`,
   `ACQUISITION_MODEL="board"`, `SUPPORTS_DISCOVERY=True`.
2. Constants: `BASE_URL`, `PAGE_DELAY=0.5`, `MAX_PAGES=120`, a browser-ish
   `HEADERS` dict.
3. `_extract_next_data(html)` → `dict | None`: regex the
   `<script id="__NEXT_DATA__" type="application/json">…</script>` node and
   `json.loads`. On missing/!parseable JSON, return `None` (do NOT raise).
4. `_page_results(data)` → `dict | None`: navigate
   `props.pageProps.serverState.initialResults.jobs.results[0]`; return
   `{"hits", "nbPages", "nbHits"}` or `None` on shape change.
5. `_fetch_page(page: int)` → `dict | None`: `GET /browse/jobs?page=N`
   (1-indexed) via httpx, `timeout=20`, `follow_redirects=True`; non-200 or
   parse failure → `None`.
6. `_hit_to_job(hit)` → `JobPosting` per the spec mapping:
   - `source="Joinup"`, `title = hit["title"] or hit["headline"] or ""`,
     `company = hit["startup"]`, `location = hit["location"]`,
     `url = f"{BASE_URL}/job/{hit['slug']}"`.
   - `posted_date`: `int(hit["created"])` ms epoch → `datetime.fromtimestamp(ms/1000, tz=utc).date()`; guard `ValueError/TypeError/OverflowError` → `None`.
   - `description = hit["description"]` (raw markdown; `__post_init__` truncates at 3000).
   - `tags`: skills (real list, or `ast.literal_eval` of a stringified list,
     fallback `[]`) + `jobType` + `paymentType` (raw) + `startupIndustry`
     (with `\xa0` → space). Skip empty/None values.
   - `work_mode = "remote"` iff `location == "Remote"`, else `None`.
   - `base_location`, `geo_zone`, `company_size`, `contract_type`,
     `company_country`, `comp_*` left `None`.
7. `fetch(job_filter)` → `list[JobPosting]`:
   - Fetch page 1; if `None`, log greppable
     `[Joinup] ⚠️ __NEXT_DATA__ missing/unparseable — 0 jobs` and return `[]`
     (no `disable()`).
   - Read `nbPages`; loop `page` from 1 to `min(nbPages, MAX_PAGES)`.
   - After each page, if `date_from` is set and **every** hit on the page is
     older than `date_from`, stop (no further requests). `sleep(PAGE_DELAY)`
     between pages.
   - Dedup by hit id (`seen_ids`); return the mapped list. No title/geo/
     work-mode/company filtering anywhere.
8. Module docstring records the acquisition mechanics (FR-008) and flags that
   this is undocumented site behavior that may change.

### Phase 2 — tests

`tests/test_joinup.py` (offline — monkeypatch `_fetch_page` + `time.sleep`, read
fixtures via `os.path.dirname(__file__)`):
- `test_parses_page1_fixture` — 10 JobPostings, non-empty source/title/company/url.
- `test_id_10230_mapping` — url, company "Quantum Highlands", location "Remote",
  work_mode "remote".
- `test_non_remote_location_work_mode_none` — location "Zürich" → work_mode None.
- `test_page2_disjoint_older_ids` — page 2 fixture parses a disjoint, older id set.
- `test_date_cutoff_stops_pagination` — `date_from` after page-1 dates → page 2+ never requested.
- `test_wide_net_no_relevance_filter` — three different `JobFilter(titles=…)`
  produce identical page requests (locks Constitution I).
- `test_malformed_next_data_returns_empty` — missing `__NEXT_DATA__` → `[]`,
  no auto-disable.

`tests/scraper_checks.py` — add `JoinupScraper` with
`optional_fields={"base_location", "work_mode"}` (base_location is unset;
work_mode is `None` for non-"Remote" locations).

### Phase 3 — validation

- `python -m pytest tests/test_joinup.py -q` → all pass, no network.
- `python -m pytest tests/ -q` → no regressions (162 storage tests + existing).

## Complexity Tracking

> No violations — table intentionally empty.
