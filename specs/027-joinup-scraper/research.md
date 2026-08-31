# Research — Joinup scraper

Live-source findings (verified against the captured fixtures AND the codebase,
not stale specs).

## 1. Data source: `__NEXT_DATA__` JSON

- The server-rendered `/browse/jobs` HTML embeds a
  `<script id="__NEXT_DATA__" type="application/json">…</script>` node.
- JSON path to the job list (confirmed by walking both fixtures):
  `props.pageProps.serverState.initialResults.jobs.results[0]`.
- `results[0]` siblings: `hits` (list of 10), `nbHits` (1115), `nbPages` (112),
  `hitsPerPage` (10), `page` (0-indexed **internal** page).

**Decision**: primary parse is `json` via `re` extraction of the script node —
mirror TieTalent's `__NEXT_DATA__` approach, but without BeautifulSoup (JSON is
the source of truth; bs4 not needed). A regex
`<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>` is sufficient and keeps the
diff dependency-free.

**Alternatives considered**: BeautifulSoup (`soup.find("script", id=…)`) — works
but adds bs4 to this file for no benefit; regex is what the fixtures confirm and
matches "primary parse is json, not DOM".

## 2. Hit shape (from fixtures)

Each `hit` has keys: `_highlightResult`, `_id`, `_rawTypesenseHit`,
`_snippetResult`, `created`, `description`, `headline`, `id`, `isVisible`,
`jobType`, `location`, `objectID`, `paymentType`, `skills`, `slug`, `startup`,
`startupIndustry`, `title`.

Deviations from the spec's assumptions (important for the mapping):
- `created` is an **int** epoch-ms in the fixture (spec said "string") →
  `int(hit["created"])` handles both.
- `skills` is a **real JSON list** in the parsed hit (spec said "stringified") →
  handle both list and stringified-list via `ast.literal_eval` in try/except.
- `startupIndustry` contains `\xa0` non-breaking spaces
  ("Renewables\xa0&\xa0Environment") → normalize `\xa0` → space when appending
  to tags.
- `description` is raw markdown (starts with `# …`) → keep raw.
- `jobType` ∈ {cofounder, fulltime, parttime, …}; `paymentType` ∈ {paid,
  equity, unpaid, …}.
- `location` ∈ {"Remote", "Zürich", "Ticino", …}; "Remote" is the only remote
  signal the scraper may act on (→ `work_mode="remote"`, else `None`).

## 3. Pagination (credential-free, SSR'd)

- `GET /browse/jobs?page=N` is 1-indexed; `?page=2` returns internal `page=1`
  with a disjoint, older id set. Confirmed offline.
- Sorted newest-first by `created` (descending id) → page forward until a full
  page falls entirely before `JobFilter.date_from`, then stop.
- No Typesense key exposed; backend behind a Cloud Run app; no login.

**Decision**: `PAGE_DELAY = 0.5s`, `MAX_PAGES = 120` safety cap (from the
clarified spec). The date-cutoff early stop is the real bound — a fresh sweep
rarely exceeds a few pages.

## 4. Dedup / identity

- `storage.normalize_url` is passthrough for non-`linkedin`/`indeed` sources →
  Joinup URLs (`https://joinup.ch/job/{slug}`) dedup as-is; the derived
  `JobPosting.id` (sha256 of canonical URL) is stable.

## 5. Orchestrator integration

- `scrape.discover_scrapers()` scans `scrapers/boards/` and instantiates any
  class with `fetch` + `SOURCE_NAME` + `ENABLED`. Joinup needs no `scrape.py`
  change.
- `_run_broad_scrape` already applies `JobFilterEngine` (date filter) and the
  central `title_matches_profile` gate **after** `fetch()`. The scraper must NOT
  filter titles itself (Constitution I) — the orchestrator owns that gate.
- `ACQUISITION_MODEL="board"` + `SUPPORTS_DISCOVERY=True` → included in broad
  sweep, skipped by `--monitored-only`.

**Decision**: no changes to `scrape.py`, `models.py`, `storage.py`, or any other
module.
