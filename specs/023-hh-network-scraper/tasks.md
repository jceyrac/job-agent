# Tasks: HeadHunter Network Scraper

**Input**: `specs/023-hh-network-scraper/spec.md`, plan.md

---

## Phase 1: scorer.py vocab amendments (Priority: P1) 🎯

**Goal**: Add `russian`/`turkish` languages, `russia_cis` geo zone, RUB salary, English summaries. Independent — ships first.

**Independent Test**: `python score.py --mock --profile unified_jc` — YandexLike Tier-0 rejected at score 1 (new russian vocab), MoscowChain parses salary correctly.

- [ ] T001 [US1] Add `"russian"` and `"turkish"` to `_VALID_LANGUAGES` in `scorer.py` (line 393)
- [ ] T002 [US1] Update `language_required` section in `EXTRACTION_PROMPT` in `scorer.py` — add Russian/Turkish detection signals; update JSON schema enum line
- [ ] T003 [US1] Update `language_required` section in `SYSTEM_PROMPT` in `scorer.py` — same signals as T002
- [ ] T004 [US1] Add `russia_cis` geo zone to both `SYSTEM_PROMPT` and `EXTRACTION_PROMPT` geo_zone sections in `scorer.py` — definition + amend europe to exclude Russia/CIS; update JSON enum lines
- [ ] T005 [US1] Add RUB salary line + monthly×12 rule to `EXTRACTION_PROMPT` salary section in `scorer.py`
- [ ] T006 [US1] Add "Always write the summary in English" line to Summary section in both `SYSTEM_PROMPT` and `EXTRACTION_PROMPT` in `scorer.py`

---

## Phase 2: Mock cases (Priority: P1)

**Goal**: 4 new mock cases exercising Russian language gate, RUB salary parsing, geography resolve, and CIS Web3 remote.

**Independent Test**: `python score.py --mock --profile unified_jc` — 15 cases all in band.

- [ ] T007 [US1] Add 4 mock jobs to `MOCK_JOBS` in `score.py` (YandexLike, MoscowChain, MoscowBank, AstanaChain)
- [ ] T008 [US1] Add corresponding expectations in `_run_mock()` in `score.py`

---

## Phase 3: hh_network scraper (Priority: P2)

**Goal**: New board scraper for HeadHunter REST API covering 6 CIS areas. Auto-discovered, togglable.

**Independent Test**: `python scrape.py --source HeadHunter` fetches jobs. Latin-title counter appears in logs.

- [ ] T009 [US2] Create `scrapers/boards/hh_network.py` — `BaseScraper` subclass with SOURCE_NAME, ENABLED, ACQUISITION_MODEL, SUPPORTS_DISCOVERY
- [ ] T010 [US2] Implement `fetch()` — iterate profile.job_titles × HH_AREAS, paginate, map JSON → JobPosting, fetch detail descriptions, Latin-title counter
- [ ] T011 [US2] Verify area IDs against `GET https://api.hh.ru/areas` and correct `HH_AREAS` constant

---

## Phase 4: tracker geo_zone option list (Priority: P2)

**Goal**: Add `russia_cis` to the hardcoded GEO_ZONES list in settings so it appears in the profile editor's remote geo-zone fallback multiselect.

**Independent Test**: Launch Settings → Profile Editor → Countries & filters expander → "Remote geo-zone fallback" multiselect shows `russia_cis`.

- [ ] T012 [US3] Add `"russia_cis"` to `GEO_ZONES` list in `tracker_views/settings.py` (line 610)

---

## Phase 5: Validation

- [ ] T013 Run `python -m pytest tests/test_storage.py tests/test_jobspy_helpers.py -q` — 152 pass
- [ ] T014 Run `python score.py --mock --profile unified_jc` — all 15 cases in band, FELFEL unchanged
- [ ] T015 Manual: `python scrape.py --source HeadHunter` — verify area IDs, counter, VPN egress

---

## Dependencies

```
US1 (scorer.py) → US1 mocks (score.py)
US2 (scraper) — independent, can run in parallel with US1
US3 (settings GEO_ZONES) — independent
```

## Implementation Strategy

1. US1 first (scorer.py + mocks) — mock-validate vocab changes
2. US2 (scraper) — build and test independently
3. US3 (settings) — one-line addition
4. Validate
