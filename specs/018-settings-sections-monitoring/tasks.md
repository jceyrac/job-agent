# Tasks: Spec 018 — Settings sections + monitoring master-switch

**Input**: Design documents from `/specs/018-settings-sections-monitoring/`

---

## Phase 1: Scraper class attributes

**Goal**: Every scraper declares its acquisition model and discovery capability.

- [ ] T001 Add `ACQUISITION_MODEL: str = "board"` and `SUPPORTS_DISCOVERY: bool = True` to `BaseScraper` in `scrapers/base.py`
- [ ] T002 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = True` in `scrapers/greenhouse.py`
- [ ] T003 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = True` in `scrapers/ats/lever.py`
- [ ] T004 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = True` in `scrapers/ats/workable.py`
- [ ] T005 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = False` in `scrapers/ats/ashby.py`
- [ ] T006 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = False` in `scrapers/ats/gem.py`
- [ ] T007 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = False` in `scrapers/ats/recruitee.py`
- [ ] T008 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = False` in `scrapers/ats/smartrecruiters.py`
- [ ] T009 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = False` in `scrapers/ats/workday.py`
- [ ] T010 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = True` in `scrapers/company_sites/sygnum.py`
- [ ] T011 [P] Set `ACQUISITION_MODEL = "company_keyed"` + `SUPPORTS_DISCOVERY = True` in `scrapers/company_sites/tangem.py`

---

## Phase 2: Pipeline gates (scrape.py)

**Goal**: Broad path skips monitoring-only ATS; monitoring path honours per-provider gate.

- [ ] T012 Add discovery skip gate in `_run_broad_scrape`: skip company_keyed scrapers with `SUPPORTS_DISCOVERY = False` in `scrape.py`
- [ ] T013 Add per-provider monitoring gate in `_run_monitored_only`: skip provider if `monitoring.ats.<provider>.enabled` is `"false"` in `scrape.py`

---

## Phase 3: shared.py — repoint monitoring helper

- [ ] T014 Repoint `is_monitoring_source_enabled` to read `monitoring.ats.<provider>.enabled` instead of `scraper.<provider>.enabled` in `tracker_views/shared.py`

---

## Phase 4: Settings UI reorg

**Goal**: Three-section layout: Profile Editor (017), Broad scraping, Company Monitoring.

- [ ] T015 Replace `_render_scraper_toggles` with `_render_broad_scraping(db)`: boards + discovery ATS toggles, `scrape.enabled_in_pipeline` checkbox, remove `CRYPTO_WEB3_NAMES` / 🪙 logic in `tracker_views/settings.py`
- [ ] T016 Replace `_render_company_monitoring` with full rewrite: per-provider expanders with master switch, monitored-company list with unmonitor buttons, add-to-monitoring dropdown with cascade re-arm in `tracker_views/settings.py`
- [ ] T017 Update `render()` ordering: Setup → Profile Editor → Broad scraping → Company Monitoring → Purge → Re-onboard in `tracker_views/settings.py`

---

## Phase 5: Validate

- [ ] T018 Verify independence: toggle `scraper.greenhouse.enabled=false` → broad skips Greenhouse, monitoring still runs; toggle `monitoring.ats.greenhouse.enabled=false` → monitoring skips, broad still runs
- [ ] T019 Verify cascade re-arm: monitor a company under paused ATS → switch flips back to `"true"`
- [ ] T020 Verify `sqlite3 data/jobs.db "SELECT key,value FROM config WHERE key LIKE 'monitoring.ats.%'"` reflects toggles

---

## Dependencies

```
Phase 1 (T001–T011) ──> Phase 2 (T012–T013) ──> Phase 3 (T014)
                                                    │
Phase 4 (T015–T017) ────────────────────────────────┘
        │
Phase 5 (T018–T020)
```

Phase 1 and Phase 4 are independent and can run in parallel. Phase 2 depends on Phase 1 (needs class attributes). Phase 3 depends on Phase 2 (needs new config key semantics).

## Parallel opportunities

- All T002–T011 are independent (different files, one-liners each)
- T015 and T016 touch the same file but different functions — sequential safer
