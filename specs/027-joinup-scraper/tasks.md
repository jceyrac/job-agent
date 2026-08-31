# Tasks: Joinup Scraper (Swiss startup board)

**Input**: Design documents from `specs/027-joinup-scraper/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `quickstart.md`

**Tests**: Required — the spec mandates an offline fixture test (`tests/test_joinup.py`)
plus a `scraper_checks.py` entry. Tests MUST run against fixtures, never the network.

**Organization**: This spec has functional requirements (FR-001…FR-008), not user
stories. Tasks are grouped by delivery phase mirroring the plan. Story labels are
omitted (no user stories); FR mapping is noted inline for traceability.

## Phase 1: Scraper file (scrapers/boards/joinup.py)

**Goal**: A `JoinupScraper` that parses `__NEXT_DATA__` and returns wide-net
`JobPosting`s — no relevance filtering, date-cutoff-only early stop.

**Independent Test**: `python -c "from scrapers.boards.joinup import JoinupScraper; print(JoinupScraper)"`
imports cleanly; then the Phase-2 offline tests pass.

- [X] T001 Create `scrapers/boards/joinup.py` with `JoinupScraper(BaseScraper)`, class attrs `SOURCE_NAME="Joinup"`, `ENABLED=True`, `ACQUISITION_MODEL="board"`, `SUPPORTS_DISCOVERY=True`, and module docstring recording the `__NEXT_DATA__` acquisition mechanics + "undocumented behavior may change" warning (FR-008)
- [X] T002 Add constants `BASE_URL="https://joinup.ch"`, `PAGE_DELAY=0.5`, `MAX_PAGES=120`, and a browser-ish `HEADERS` dict in `scrapers/boards/joinup.py`
- [X] T003 Implement `_extract_next_data(html)` (regex the `<script id="__NEXT_DATA__"…>` node → `json.loads`, return `None` on missing/unparseable) and `_page_results(data)` (navigate `props.pageProps.serverState.initialResults.jobs.results[0]` → `{"hits","nbPages","nbHits"}` or `None`) in `scrapers/boards/joinup.py` (FR-001, FR-006)
- [X] T004 Implement `_fetch_page(page)` (httpx `GET /browse/jobs?page=N`, 1-indexed, `timeout=20`, `follow_redirects=True`; non-200/parse-fail → `None`) in `scrapers/boards/joinup.py`
- [X] T005 Implement `_hit_to_job(hit)` mapping to `JobPosting`: title/headline fallback, `startup`→company, `location`, `url=f"{BASE_URL}/job/{slug}"`, `created` ms-epoch→date (guard bad values→None), raw-markdown description, tags = skills (list or `ast.literal_eval` stringified) + `jobType` + `paymentType` + normalized `startupIndustry`, `work_mode="remote"` iff `location=="Remote"` else `None`, all classified fields left `None` (FR-002, FR-003)
- [X] T006 Implement `fetch(job_filter)`: fetch page 1 (log greppable `[Joinup] ⚠️ __NEXT_DATA__ missing/unparseable — 0 jobs` and return `[]` WITHOUT `disable()` on `None`), read `nbPages`, loop to `min(nbPages, MAX_PAGES)`, stop early when a full page is entirely older than `job_filter.date_from`, `time.sleep(PAGE_DELAY)` between pages, dedup by hit id (FR-004, FR-005, FR-006, FR-007)

**Checkpoint (PAUSE)**: scraper file complete — review before tests.

---

## Phase 2: Offline tests (tests/test_joinup.py)

**Goal**: Offline tests that prove the parse, the id-10230 mapping, pagination
disjointness, the date-cutoff early stop, the wide-net (no relevance filter)
contract, and resilient malformed-input handling — all against fixtures.

**Independent Test**: `python -m pytest tests/test_joinup.py -q` → all pass, zero network I/O.

- [ ] T007 Create `tests/test_joinup.py` with a fixture loader (read `joinup_browse_jobs.html` / `joinup_browse_jobs_page2.html` via `os.path.dirname(__file__)`) and monkeypatch `_fetch_page` + `time.sleep` so no network occurs
- [ ] T008 Add `test_parses_page1_fixture` — 10 JobPostings with non-empty source/title/company/url, no exception (FR-001)
- [ ] T009 Add `test_id_10230_mapping` — hit id 10230 → url `https://joinup.ch/job/founding-commercial-partner-68b89d3c-9a42-46d8-8e66-2bf0540909a0-10230`, company "Quantum Highlands", location "Remote", work_mode "remote"
- [X] T010 Add `test_non_remote_location_work_mode_none` — location "Zürich" → work_mode None (FR-003)
- [X] T011 Add `test_page2_disjoint_older_ids` — page-2 fixture parses a disjoint, older id set (pagination proven offline)
- [X] T012 Add `test_date_cutoff_stops_pagination` — `date_from` set so page 2 is entirely older → page 2+ never requested (FR-004)
- [X] T013 Add `test_wide_net_no_relevance_filter` + `test_malformed_next_data_returns_empty` — three different `JobFilter(titles=…)` produce identical page requests (Constitution I lock); missing `__NEXT_DATA__` → `[]` and no auto-disable (FR-005, FR-006)

**Checkpoint (PAUSE)**: tests complete — run `python -m pytest tests/test_joinup.py -q` before the checks entry.

---

## Phase 3: scraper_checks entry (tests/scraper_checks.py)

**Goal**: Joinup participates in the live scraper contract checks with correctly
scoped optional fields.

**Independent Test**: `python tests/run_all.py` shows a `JoinupScraper` row with `base_location` + `work_mode` optional (⚠️), the rest ✅.

- [X] T014 Add `JoinupScraper` to `_load_scrapers()` in `tests/run_all.py` with `optional_fields={"base_location", "work_mode"}` (base_location unset; work_mode None for non-"Remote")

---

## Phase 4: Validation & Polish

**Goal**: Confirm no regressions and the wide-net scraper is discoverable.

- [X] T015 Run `python -m pytest tests/ -q` → all existing tests (162 storage + scraper parsing) pass, no regressions
- [X] T016 Run `python -m pytest tests/test_joinup.py -q` and confirm it performs no network I/O; run `python tests/run_all.py` to confirm "Joinup" is discovered and listed

---

## Dependencies & Execution Order

- **Phase 1** (scraper file): no dependencies — start immediately.
- **Phase 2** (tests): depends on Phase 1 (imports `JoinupScraper`).
- **Phase 3** (checks entry): depends on Phase 1 (imports `JoinupScraper`); independent of Phase 2 tests.
- **Phase 4** (validation): depends on Phases 1–3.

### Parallel Opportunities

- T002–T005 within Phase 1 are conceptually separable but live in the same file — implement in one pass.
- Phase 3 (T014) can be done in parallel with Phase 2 (T007–T013) once Phase 1 is complete — different files.

### Pause Points (per the implement workflow)

1. **After Phase 1** (scraper file) — review `scrapers/boards/joinup.py`.
2. **After Phase 2** (tests) — run `python -m pytest tests/test_joinup.py -q`.

## Notes

- Every task names an exact file path; no other module is touched.
- No new dependencies — stdlib `json`/`re`/`ast`/`datetime` + existing `httpx`.
- Wide-net contract (Constitution I) is asserted by T013, mirroring `test_free_work_wide_net.py`.
