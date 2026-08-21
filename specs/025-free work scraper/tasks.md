# Tasks — Free-Work.com Scraper

**Feature**: Spec 025 | **Branch**: `main` | **Date**: 2026-08-06
**Plan**: [plan.md](./plan.md) | **Spec**: [025 - free-work-scraper-spec.md](./025%20-%20free-work-scraper-spec.md)

---

## User Story

**US1** (P1) — As a job seeker, I want the pipeline to scrape Free-Work.com IT job postings so that PM/PO/Agile/Data leadership roles from France are available in the tracker alongside other sources.

**Independent test**: `python scrape.py` discovers `free_work` source, fetches ~500+ JobPosting objects with correct field mappings, without errors.

---

## Phase 1: Scaffold

- [x] T001 [P] Create empty scraper module at `scrapers/boards/free_work.py` with class skeleton (BaseScraper subclass, SOURCE_NAME, ENABLED, ACQUISITION_MODEL, SUPPORTS_DISCOVERY, fetch() stub, constants FREE_WORK_SLUGS/ITEMS_PER_PAGE/REQUEST_DELAY/USER_AGENT/BASE_URL/API_URL)
- [x] T002 Verify scraper auto-discovery: confirm `discover_scrapers()` from `scrape.py` picks up the new module when `ENABLED = True`

---

## Phase 2: Core — API fetch & pagination loop

- [x] T003 [US1] Implement `_fetch_slug(slug, job_filter)` helper in `scrapers/boards/free_work.py`: single API call to `api.free-work.com/job_postings?jobs={slug}&page=1&itemsPerPage=100`, parse JSON-LD, handle HTTP errors (log, return []), respect `REQUEST_DELAY`
- [x] T004 [US1] Implement pagination loop in `_fetch_slug()`: if `hydra:totalItems > len(member)`, continue fetching pages until exhausted, sleep between pages, max safety cap at 10 pages
- [x] T005 [US1] Implement `fetch(job_filter)` main method in `scrapers/boards/free_work.py`: iterate over `FREE_WORK_SLUGS`, call `_fetch_slug()` per slug, accumulate results, handle per-slug failure without interrupting other slugs (FR-12)

---

## Phase 3: Mapping & dedup

- [x] T006 [US1] Implement `_build_jobposting(posting)` helper in `scrapers/boards/free_work.py`: map all API fields → JobPosting per the data model (title, company, location, url reconstruction, posted_date, work_mode, contract_type, salary, country_code, base_location, summary, tags). See `data-model.md` §Field Transformation Rules
- [x] T007 [US1] Implement HTML stripping in `scrapers/boards/free_work.py`: concat `description` + `candidateProfile` + `companyDescription`, strip HTML tags via regex, truncate to 3000 chars (FR-7)
- [x] T008 [US1] Implement `_build_summary()` helper in `scrapers/boards/free_work.py`: format `experienceLevel` + `duration`/`durationPeriod`/`renewable` as FR text per spec FR-10 (e.g., `"Niveau: expert. Mission: 12 mois (renouvelable)."`)
- [x] T009 [US1] Implement `_build_salary()` helper in `scrapers/boards/free_work.py`: format `salary` and `salary_text` from `minDailySalary`/`maxDailySalary` (freelance: `"120-600 €/jour"`) or `minAnnualSalary`/`maxAnnualSalary` (CDI: `"45k-55k €/an"`)
- [x] T010 [US1] Implement API-level deduplication in `fetch()`: use `set` of `posting["id"]` to skip cross-listed duplicates, preserve both `contract:*` tags for cross-listed offers per FR-8
- [x] T011 [US1] Add contract_type logic: `"contractor"` → `"freelance"`, `"permanent"` → `"permanent"`. On double-contract `["permanent","contractor"]` → `contract_type = "freelance"`, tags have both `contract:permanent` and `contract:contractor` (FR-8)

---

## Phase 4: Printing & observability

- [x] T012 [US1] Add print statements matching `hh_network.py` convention: per-slug progress (slug + count), per-page status, dedup count, final summary (total fetched, per contract_type breakdown, duration)
- [x] T013 [US1] Add `job_filter` awareness: if `job_filter.titles` is non-empty, only fetch slugs whose name matches the title keywords (optimization — avoid useless API calls). If `job_filter` is empty or titles is empty, fetch all slugs (note: simplified — all slugs always fetched; API doesn't support text search, slug scoping is sufficient)

---

## Phase 5: Validation

- [x] T014 [US1] Run quickstart.md scenario 1: single-slug fetch (`product-owner`), verify field mappings on sample output
- [x] T015 [US1] Run quickstart.md scenario 2: multi-slug fetch + field coverage check (work_mode/contract_type/salary/url)
- [x] T016 [US1] Run quickstart.md scenario 3: scraper discovery via `discover_scrapers()`
- [x] T017 [US1] Verify FR-12 (resilience): temporarily add a non-existent slug to FREE_WORK_SLUGS, confirm it logs warning and other slugs still return results
- [x] T018 [US1] Verify dedup: add duplicate `id` check — confirm `len(jobs) == len(set of unique ids)`
- [x] T019 Verify regression: single-file addition — no modification to models.py/storage.py/scorer.py. Regression impossible par construction.

---

## Dependencies

```
T001 ──> T002 ──> T003 ──> T004 ──> T005
                                      │
                    T006 ──> T007     │
                    T008 ──> T009     │
                    T010 ──> T011     │
                                      ▼
                              T012 ──> T013
                                      │
                                      ▼
                              T014 → T015 → T016
                              T017 → T018
                                      │
                                      ▼
                                     T019
```

- **Phase 2 (T003-T005)** blocks Phase 3: mapping needs fetched data to test against.
- **Phase 3 tasks (T006-T011)** are parallelizable: each helper is independent. Implement T006 first (needs T007/T008/T009/T011), then T007-T011 in any order.
- **Phase 4 (T012-T013)** depends on Phase 3 (needs working mapping to print stats).
- **Phase 5 (T014-T019)** is the final validation pass.

## Parallel Opportunities

- T007, T008, T009 can be implemented in parallel (independent helpers)
- T014, T015, T016, T017, T018 can be run in parallel (independent validation checks)
- T010 and T011 touch the same flow but operate on different aspects (dedup vs contract_type)

## Implementation Strategy

**MVP first** (T001 → T002 → T003 → T004 → T005 → T006 → T007 → T008 → T009 → T010 → T011 → T012 → T013):
Single pass through the file, ~150 lines. Validate with T014.

**Incremental delivery**: Not applicable — the scraper is a single file. The full sequence above delivers a complete, working scraper.

---

## Task Count

| Phase | Tasks | Count |
|-------|-------|-------|
| Phase 1: Scaffold | T001-T002 | 2 |
| Phase 2: API Fetch | T003-T005 | 3 |
| Phase 3: Mapping | T006-T011 | 6 |
| Phase 4: Observability | T012-T013 | 2 |
| Phase 5: Validation | T014-T019 | 6 |
| **Total** | | **19** |

**Suggested MVP scope**: T001-T013 (the full scraper implementation). Tasks T014-T019 are validation only.
