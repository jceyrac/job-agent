# 027 — Joinup Scraper (Swiss startup board)

## Input
Add a new aggregation scraper for Joinup (https://joinup.ch), Switzerland's
startup job platform (ETH Entrepreneur Club + Swisspreneur). Board acquisition
model, auto-discovered, participates in the broad scrape sweep.

## Context (verified against live source AND the live site before writing)
- `scrapers/base.py`: BaseScraper ABC. Set SOURCE_NAME="Joinup",
  ACQUISITION_MODEL="board", SUPPORTS_DISCOVERY=True, ENABLED=True.
- Data source is the `__NEXT_DATA__` JSON embedded in the server-rendered
  `/browse/jobs` HTML — the SAME structured-JSON pattern already used for
  TieTalent. Do NOT scrape the rendered DOM.
- Path to the job list inside the page JSON:
  `props.pageProps.serverState.initialResults.jobs.results[0].hits`
  (a Typesense-backed InstantSearch response; fields look Algolia-like).
  Sibling keys on `results[0]`: `nbHits` (~1115), `nbPages` (~112),
  `hitsPerPage` (10), `page` (0-indexed).
- PAGINATION IS CREDENTIAL-FREE AND SSR'd: `GET /browse/jobs?page=N`
  (N is 1-indexed) server-renders that page into `__NEXT_DATA__`. Verified:
  `?page=2` returns internal page=1 with a disjoint, older ID set. No Typesense
  key is exposed in the page (backend sits behind a Cloud Run app) and none is
  needed. No login.
- Hits are sorted newest-first by `created` (descending id). This pairs with
  the existing 30-day date filter: page forward until a page falls entirely
  before JobFilter.date_from, then stop — no need to pull all 112 pages daily.
- httpx already in requirements.txt — NO new dependencies. (beautifulsoup4 is
  available if light markdown/HTML cleanup of `description` is wanted, but the
  primary parse is json, not bs4.)
- storage.normalize_url is passthrough for this source, so Joinup job URLs
  dedup as-is and the derived JobPosting.id is stable.

## Hit → JobPosting field mapping
Each `hit` object exposes:
`_id, id, created (epoch ms, string), description (markdown), headline,
isVisible, jobType, location, paymentType, skills (stringified list),
slug, startup, startupIndustry, title, objectID`.

- source        = "Joinup"
- title         = hit["title"] (fall back to headline)
- company       = hit["startup"]
- location      = hit["location"]              # e.g. "Remote", "Zürich", "Ticino"
- url           = "https://joinup.ch/job/" + hit["slug"]
- posted_date   = date from int(hit["created"]) ms epoch; guard bad values → None
- description   = hit["description"] kept as raw markdown (no stripping; the
                  model truncates at 3000 chars)
- tags          = parse hit["skills"] defensively (may be a real list, or a
                  stringified python list like "['Building World Class Teams']"
                  → handle both; fallback []). Append jobType and paymentType
                  (raw values) so the extractor sees them. Optionally append
                  startupIndustry.
- work_mode     = "remote" ONLY if location == "Remote", else None
- everything else (geo_zone, company_size, contract_type, company_country,
  industry_sector, comp_*) = None — filled by the extractor/scorer.

## Constitutional guardrails (MUST hold — .specify/memory/constitution.md §I, §VIII)
- Wide net: collect ALL parsed jobs with ZERO relevance filtering inside the
  scraper (no geo, work-mode, company-size, or title filtering). Fit judgment
  stays with the scorer alone.
- The ONLY permitted early stop is the date cutoff (JobFilter.date_from), which
  is a freshness bound already honored across the pipeline — not a relevance
  filter. It is an efficiency guard on pagination, not a fit decision.
- Do NOT classify geo_zone/company_size/contract_type/company_country/comp_* at
  scrape time. jobType and paymentType MAY be surfaced (e.g. appended to tags or
  left in description) so the extractor sees them, but MUST NOT be mapped into
  classified JobPosting fields by the scraper.
- Surgical: one new file `scrapers/joinup.py` plus tests. Do NOT modify
  storage.py, models.py, profiles.py, main.py, scrape.py, or any other scraper.
- DEV-only on the Mac via Claude Code. verva is deploy-only.

## Functional requirements
- FR-001: Extract and json-parse `__NEXT_DATA__` from `/browse/jobs`, read the
  `hits` list at the path above, and map each hit to a JobPosting per the table.
- FR-002: posted_date from `created` (ms epoch) → date; unparseable → None.
- FR-003: work_mode = "remote" only for location == "Remote"; else None. No other
  classification.
- FR-004: Paginate `?page=N` starting at page 1, reading nbPages from the first
  response. Stop when either (a) nbPages is reached, or (b) a full page's jobs
  are all older than JobFilter.date_from. Apply a 0.5s politeness delay between
  page requests and a 120-page hard max-pages safety cap.
- FR-005: Return every parsed hit within the freshness window. No filtering of
  any other kind inside fetch().
- FR-006: Resilient parsing — a hit missing an optional field still yields a
  JobPosting; skills that fail to parse → []. A missing/!changed __NEXT_DATA__
  shape logs a distinct, greppable message and returns [] WITHOUT auto-disabling.
- FR-007: Config-driven enable via the inherited scraper.joinup.enabled key.
- FR-008: The scraper module docstring MUST record the acquisition mechanics for
  future maintainers — data comes from the `__NEXT_DATA__` JSON at
  `props.pageProps.serverState.initialResults.jobs.results[0].hits`, and full
  pagination is the SSR'd 1-indexed `?page=N` query param (no Typesense key, no
  DOM scraping) — and flag that this is undocumented site behavior that may change.

## Clarifications

### Session 2026-08-31

- Q: Politeness delay per page and hard max-pages cap value? → A: 0.5s delay
  between page requests; 120-page hard safety cap. The date-cutoff early-stop is
  the real bound.
- Q: jobType ("cofounder"/"fulltime"/"parttime"/…) and paymentType ("paid"/
  "unpaid"): append to tags, keep only in description, or drop? → A: Append both
  to tags (raw values) so the extractor sees them; do NOT map to contract_type
  or comp_*.
- Q: description keep raw markdown or strip to plain text? → A: Keep raw
  markdown (no stripping); the model truncates at 3000 chars.

## Acceptance scenarios
- Given tests/fixtures/joinup_browse_jobs.html, When the parse runs, Then it
  returns 10 JobPostings with non-empty source/title/company/url and no exception.
- Given the page-1 fixture, Then hit id 10230 maps to
  url https://joinup.ch/job/founding-commercial-partner-68b89d3c-9a42-46d8-8e66-2bf0540909a0-10230,
  company "Quantum Highlands", location "Remote", work_mode "remote".
- Given a hit with location "Zürich", Then work_mode is None.
- Given tests/fixtures/joinup_browse_jobs_page2.html, Then it parses a disjoint,
  older set of ids (pagination proven offline).
- Given date_from set so page 2 is entirely older, Then pagination stops and
  page 3+ is not requested.
- Given malformed/absent __NEXT_DATA__, Then fetch() returns [] and logs a
  distinct parse-fail message without auto-disabling.
- Given the scraper runs inside _run_broad_scrape, Then no relevance filtering
  occurs inside the scraper (asserted by a wide-net test).

## Non-goals
- No DOM scraping (JSON is the source of truth). No Typesense direct-API calls.
- No login / authenticated scraping.
- No geo / size / work-mode / title / paymentType filtering inside the scraper.
- No changes to scoring, DB schema, or the tracker UI.

## Success criteria
- A broad `scrape.py` run lists "Joinup" among discovered scrapers and saves
  parsed jobs to the DB.
- New Swiss startup jobs surface in the tracker after a scrape + score run.
- The FELFEL regression case and existing scraper checks still pass.

## Test artifacts (already captured on the Mac)
- tests/fixtures/joinup_browse_jobs.html        (page 1, 10 hits)
- tests/fixtures/joinup_browse_jobs_page2.html  (page 2, 10 older hits)
- New offline test mirrors tests/test_free_work_wide_net.py: parse the fixture,
  assert well-formed JobPostings, the id-10230 mapping above, and that no
  in-scraper relevance filtering occurs. Add a Joinup entry to
  tests/scraper_checks.py. Tests MUST NOT hit the network.
