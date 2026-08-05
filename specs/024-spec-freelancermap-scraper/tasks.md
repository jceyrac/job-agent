# Tasks: Scraper freelancermap

**Input**: `specs/024-spec-freelancermap-scraper/024 - spec-freelancermap-scraper.md`, `plan.md`

**Tests**: Not requested in spec. Validation via SC-001–SC-007 acceptance criteria.

---

## Phase 1: Setup — scorer.py contract type override (Foundational)

**Purpose**: `AUTHORITATIVE_CONTRACT_SOURCES` whitelist so freelancermap's source `contract_type` takes authority over Groq inference. Blocks US1 contract_type correctness (SC-004).

- [x] T001 Add `AUTHORITATIVE_CONTRACT_SOURCES = {"Freelancermap"}` constant in `scorer.py` (near other module-level constants, e.g. after `_EVAL_PASSTHROUGH_KEYS`)
- [x] T002 Add contract_type override logic in `extract_job_fields()` in `scorer.py` — after the LLM result is parsed into `result` dict, if `job.source` is in `AUTHORITATIVE_CONTRACT_SOURCES` and `job.contract_type` is set and not `"unknown"`, override `result["contract_type"] = job.contract_type`

**Checkpoint**: A freelancermap job with `contract_type="regie"` set by the scraper survives extraction with `contract_type` unchanged. Groq inference is bypassed for this source.

---

## Phase 2: Foundational — scraper skeleton + single-page fetch

**Purpose**: File creation, BaseScraper integration, basic HTTP fetch. Blocks all user story implementation.

- [x] T003 Create `scrapers/boards/freelancermap.py` — `BaseScraper` subclass with:
  - `SOURCE_NAME = "Freelancermap"`
  - `ENABLED = True`
  - `ACQUISITION_MODEL = "board"`
  - `SUPPORTS_DISCOVERY = True`
- [x] T004 Implement `fetch()` skeleton in `scrapers/boards/freelancermap.py` — accept `JobFilter`, log start message, return empty `list[JobPosting]`
- [x] T005 Implement single-page HTTP GET in `scrapers/boards/freelancermap.py`:
  - URL: `https://www.freelancermap.com/projects/switzerland?projectContractTypes[0]=contracting&projectContractTypes[1]=employee_leasing&countries[]=3&sort=1&pagenr=1`
  - Headers: `User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36`
  - `requests.get()` with `timeout=15`
  - Log HTTP status code
- [x] T006 Verify HTML response structure in `scrapers/boards/freelancermap.py` — print response length, search for `data-component-name="ProjectSearch"` to confirm React-on-Rails JSON blob is present; if absent, search for alternative data sources (internal API endpoint, embedded `<script>` tags)

**Checkpoint**: Scraper imports cleanly, auto-discovers via `main.py`, fetches one page successfully with HTTP 200, and the page contains recognizable project data.

---

## Phase 3: User Story 1 — Missions freelance suisses dans le digest (Priority: P1) 🎯 MVP

**Goal**: Scraper returns valid `JobPosting` objects for Swiss freelance + regie projects, with source-authoritative `contract_type`. Projects appear in the daily digest.

**Independent Test**: `python main.py` with only freelancermap scraper enabled → digest contains CH missions with title, company, location, contract_type (`freelance` or `regie` badge), work_mode, and valid URL.

### Implementation for User Story 1

- [x] T007 [P] [US1] Extract job listings from React-on-Rails JSON in `scrapers/boards/freelancermap.py`:
  - Parse `<script data-component-name="ProjectSearch">` JSON blob with `json.loads()`
  - Navigate to the projects array (key TBD from T006 verification — expected: `formChoiceData` contains initial state, projects may be in separate endpoint)
  - If projects are NOT in the embedded JSON: identify the internal API endpoint and issue a second GET/submit; document the endpoint in a comment
  - Fallback if JSON is unavailable: BeautifulSoup + lxml CSS selectors on server-rendered HTML
- [x] T008 [P] [US1] Map freelancermap fields to `JobPosting` in `scrapers/boards/freelancermap.py`:
  - `source = self.SOURCE_NAME`
  - `title`: from project title field (accept anonymized `ID: *****` as-is)
  - `company`: from poster/company field (often an agency name: Hays, ITech Consult, etc.)
  - `location`: from location field, truncated to 200 chars
  - `url`: absolute URL to project detail page (`https://www.freelancermap.com/projects/{slug}`)
  - `posted_date`: parse two formats — `HH:MM` → `date.today()`, `DD.MM.YYYY` → `datetime.strptime()`
  - `description`: None (detail page not fetched to keep runtime low)
  - `tags`: empty list
  - `salary`: None
- [x] T009 [US1] Map `contract_type` from source labels in `scrapers/boards/freelancermap.py` per FR-006:
  - `contracting` → `"freelance"`
  - `employee_leasing` → `"regie"`
  - `permanent_position` → `"permanent"`
  - Map BEFORE creating `JobPosting` so `contract_type` is set on the object for T001–T002 override
- [x] T010 [P] [US1] Map `work_mode` from remote percentage in `scrapers/boards/freelancermap.py` per FR-005:
  - `100%` or `"100%"` → `"remote"`
  - `1-99%` → `"hybrid"`
  - `"On-site"` or `"0%"` → `"on-site"`
  - Unparseable → `None`
- [x] T011 [US1] Implement URL dedup in `scrapers/boards/freelancermap.py` per FR-012:
  - Track seen URLs in a `set()` within the fetch call
  - Skip duplicate URLs within the same batch
  - Also check `self._storage.url_exists(url)` if storage is available
- [x] T012 [US1] Prose path — add DE/FR `job_titles` to active profile via Streamlit Settings UI:
  - Open tracker, navigate to Settings → Profile Editor
  - Add to `job_titles`: `Leiter Produktmanagement`, `Chef de produit`, `Responsable produit`
  - Save profile
  - (No code change required — documented here for sequencing)

**Checkpoint**: Swiss freelance + regie projects appear in the digest with correct contract_type badges. SC-004 verified: 100% of freelancermap jobs have contract_type from source, not Groq.

---

## Phase 4: User Story 2 — Extension DACH / Europe (Priority: P2)

**Goal**: Multi-country support via configuration. Switzerland active by default; other countries opt-in. Page cap enforced.

**Independent Test**: Set `COUNTRIES = [{"slug": "austria", "id": 2}]` in the scraper, run → Austrian projects appear without code changes.

### Implementation for User Story 2

- [x] T013 [US2] Add configurable country list in `scrapers/boards/freelancermap.py`:
  - Class attribute `COUNTRIES: list[dict] = [{"slug": "switzerland", "id": 3}]`
  - Default: Switzerland only (per spec: single active country by default)
  - Reference table in comment per FR-015b: `{"slug": "germany", "id": 1}`, `{"slug": "austria", "id": 2}`, `{"slug": "uk", "id": 4}`, `{"slug": "belgium", "id": 39}`, `{"slug": "usa", "id": 5}`
- [x] T014 [US2] Implement multi-country loop in `fetch()` in `scrapers/boards/freelancermap.py`:
  - Iterate `self.COUNTRIES`
  - For each country: construct URL with `countries[]={id}` param and `/projects/{slug}` path
  - Aggregate results from all countries into single `list[JobPosting]`
  - Log per-country counts
- [x] T015 [US2] Implement page cap in `scrapers/boards/freelancermap.py` per FR-008:
  - Class constant `MAX_PAGES_PER_COUNTRY = 3`
  - Paginate `pagenr=1` to `MAX_PAGES_PER_COUNTRY` per country
  - Stop early if a page returns 0 projects
  - 0.5s `time.sleep()` between pages
- [x] T016 [US2] Handle pagination detection in `scrapers/boards/freelancermap.py`:
  - Check for `rel="next"` link in HTML
  - If absent on page N, stop pagination (don't request page N+1)
  - This avoids unnecessary requests beyond the last page

**Checkpoint**: Scraper handles Switzerland (3 pages) by default. Adding `{"slug": "austria", "id": 2}` to `COUNTRIES` fetches Austrian projects too. Page cap respected. SC-003: time budget maintained.

---

## Phase 5: User Story 3 — Résilience au changement de structure HTML (Priority: P3)

**Goal**: Silent regression detection via in-page oracle (FR-013) and source-level filter funnel diagnostic (FR-019).

**Independent Test**: Modify the JSON/HTML selector to return zero results → oracle detects header counter > 0 but 0 cards parsed → alert appears in logs. Run `filter_funnel.py --source Freelancermap` → staged counts printed.

### Implementation for User Story 3

- [x] T017 [US3] Implement in-page oracle in `scrapers/boards/freelancermap.py` per FR-013:
  - Parse the `"N jobs & projects"` counter from page header/JSON (exact selector TBD from T006)
  - Compare `N` to the number of project cards actually parsed from the SAME response
  - If counter is unreadable → `print(f"  [{self.SOURCE_NAME}] warning: could not read project counter")`
  - If counter > 50 AND 0 cards parsed → `print(f"  [{self.SOURCE_NAME}] ALERT: structural breakage detected — {N} listed, 0 parsed")` + `self.disable("structural breakage: 0 cards parsed from non-empty page")`
  - No persistent state, no run-to-run comparison
- [x] T018 [US3] Create `scripts/filter_funnel.py` — standalone diagnostic script:
  - Same pattern as `scripts/audit_provenance.py`: `argparse`, read-only SQLite connection, `--source` argument
  - Import `JobFilterEngine` from `filters.py` and active profile from `profiles.py` (do NOT reimplement predicates)
  - Read all jobs for the given source from DB
  - Apply filter stages in sequence, logging counts:
    ```
    collectés bruts          →  N
      après filtre titre     →  N
      après filtre work_mode →  N
      après filtre langue    →  N
      après filtre géo       →  N
      au digest (score≥seuil)→  N
    ```
  - Each count is cumulative — shows what remains AFTER that filter
- [x] T019 [US3] Add `--country` and `--contract-type` optional filters to `scripts/filter_funnel.py`:
  - `--country switzerland` → only jobs from that country slug
  - `--contract-type freelance` → only jobs with that contract_type
  - Useful for per-segment diagnostics without code changes

**Checkpoint**: FR-013 oracle fires on structural breakage. `filter_funnel.py` runs on any source, not just freelancermap. SC-007 satisfied.

---

## Phase 6: Polish & Validation

**Purpose**: Verify all success criteria, run regression tests.

- [x] T020 Run `python -m pytest tests/test_storage.py -q` — all 162 tests pass (no storage.py changes, but verify no accidental breakage)
- [x] T021 [P] Verify SC-001 (completeness): run scraper for Switzerland, confirm oracle reports parsed count ≈ expected (~60 from 3 pages). If mismatch, investigate selector or API changes.
- [x] T022 [P] Verify SC-003 (time): run `time python main.py`, confirm wall-clock under 25 minutes
- [x] T023 Verify SC-004 (contract_type source): query DB — `SELECT contract_type, COUNT(*) FROM jobs JOIN job_scores USING (id) WHERE source='Freelancermap' GROUP BY 1` — verify values are `freelance`/`regie`/`permanent`, never `unknown` or `internship`
- [x] T024 [P] Verify SC-005 (zero dupes): run scraper twice, `SELECT url, COUNT(*) FROM jobs WHERE source='Freelancermap' GROUP BY url HAVING COUNT(*) > 1` → empty result
- [x] T025 [P] Verify SC-006 (no regression): compare `SELECT source, COUNT(*) FROM jobs GROUP BY source` before/after — existing scrapers unchanged
- [x] T026 Verify SC-007 (filter tunnel): run `python scripts/filter_funnel.py --source Freelancermap` → each stage count is non-zero and attributable

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (scorer.py) ─────────────────────────────────────────────────┐
                                                                       │
Phase 2 (scraper skeleton) ─── BLOCKS all user stories ──────────────┤
                                                                       │
Phase 3 (US1: CH freelance) ─── depends on Phase 1 + Phase 2 ────────┤
                                                                       │
Phase 4 (US2: multi-country) ─── depends on Phase 3 (builds on scraper)┤
                                                                       │
Phase 5 (US3: resilience) ─── depends on Phase 3 (needs working scraper)┤
                                                                       │
Phase 6 (Validation) ─── depends on Phase 3 + Phase 4 + Phase 5 ─────┘
```

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 1 + Phase 2. No dependency on US2 or US3.
- **US2 (P2)**: Builds on US1's scraper — adds country loop + page cap. Independently testable (change `COUNTRIES` config, re-run).
- **US3 (P3)**: Can start after US1's scraper is functional (T017 needs a running scraper; T018 is independent). Independently testable.

### Within Each Phase

- Phase 1: T001 → T002 (sequential: define constant first, then use it)
- Phase 3: T007 + T008 + T010 are [P] (different concerns in same file, but share extraction logic — T007 must complete before T008/T009/T010)
- Phase 3: T009 depends on T008 (needs JobPosting creation to exist before mapping contract_type)
- Phase 4: T013 → T014 → T015 (country list → loop → pagination cap)
- Phase 5: T017 (in scraper) and T018 (standalone script) are independent of each other

### Parallel Opportunities

- **Phase 1 + Phase 2**: Different files (`scorer.py` vs new `freelancermap.py`) — can run in parallel
- **Phase 5**: T017 (scraper oracle) and T018 (filter_funnel.py) are different files — can run in parallel
- **Phase 6**: T021–T026 are all independent verification tasks — can run in parallel

---

## Parallel Example: Phase 5

```bash
# Independent tasks — different files:
Task: "Implement in-page oracle in scrapers/boards/freelancermap.py"
Task: "Create scripts/filter_funnel.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: `scorer.py` whitelist (T001–T002)
2. Complete Phase 2: scraper skeleton (T003–T006)
3. Complete Phase 3: US1 — Swiss freelance jobs in digest (T007–T012)
4. **STOP and VALIDATE**: Run `main.py`, verify digest contains CH missions with `freelance`/`regie` badges
5. Deploy if ready — this delivers the core value

### Incremental Delivery

1. Setup + Foundational → scorer override working, scraper fetching
2. Add US1 → Swiss freelance jobs appear in digest → **Deploy MVP**
3. Add US2 → multi-country configuration active → **Deploy**
4. Add US3 → oracle + funnel diagnostic → **Deploy**
5. Validation → all SC-001–SC-007 confirmed

### Single Developer Strategy

```
Phase 1 (scorer.py)          ── 10 min
Phase 2 (scraper skeleton)   ── 15 min
Phase 3 (US1 core)           ── 60 min (bulk of work)
Phase 4 (US2 multi-country)  ── 20 min
Phase 5 (US3 resilience)     ── 30 min
Phase 6 (Validation)         ── 30 min
                              ≈ 2h45 total
```
