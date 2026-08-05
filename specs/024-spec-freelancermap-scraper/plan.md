# Implementation Plan: Scraper freelancermap

**Branch**: `024-freelancermap-scraper` | **Date**: 2026-08-05 | **Spec**: [024 - spec-freelancermap-scraper.md](024%20-%20spec-freelancermap-scraper.md)

**Status**: Ready. All architectural decisions made during clarify phase.

## Summary

Four deliverable files, zero stable-core changes beyond `scorer.py`:
1. **`scrapers/boards/freelancermap.py`** — new board scraper for freelancermap.com, fetching freelance + regie projects across configurable countries
2. **`scorer.py`** — one-line addition: `AUTHORITATIVE_CONTRACT_SOURCES` whitelist + override logic after extraction parsing
3. **`scripts/filter_funnel.py`** — standalone diagnostic script, same pattern as `scripts/audit_provenance.py`
4. **Prose path** — add DE/FR `job_titles` to profile via Settings UI (no code change)

No changes to `models.py`, `storage.py`, `profiles.py`, `scrape.py`, `filters.py`, `main.py`, or any existing scraper.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: `requests` + `beautifulsoup4` (lxml backend) — already in `requirements.txt`
**Storage**: No schema changes. Uses existing `JobPosting` fields and `save_unscored()`
**Testing**: pytest (162 tests in `test_storage.py`); new scraper validated via SC-001–SC-007 acceptance criteria
**Target Platform**: Dev Mac → Docker/Linux production (egress via gluetun-scrape)
**Project Type**: CLI pipeline + Streamlit UI
**Performance Goals**: 3 pages/pays × ~20 offres/page, ~0.5s sleep between requests, total scrape <2 min/pays
**Constraints**: No new dependencies, no `models.py`/`storage.py` changes, no `scrape.py` changes (auto-discovery), `scorer.py` touch limited to contract_type override logic only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Filet large** | ✅ | Scraper fetches all projects matching contract type filter (`contracting` + `employee_leasing`). No title/language/geo filtering — left to `filters.py` + `scorer.py` downstream. |
| **II. Deux chemins** | ✅ | `job_titles` DE/FR additions are prose path (Settings UI). Scraper + `scorer.py` whitelist + `filter_funnel.py` are code path. Listed together for sequencing, never mixed. |
| **III. Profil unifié** | ✅ | No new profiles. `job_titles` additions benefit all sources. |
| **IV. Structure déterministe** | ✅ | `contract_type` is source-authoritative via `AUTHORITATIVE_CONTRACT_SOURCES` whitelist in `scorer.py` — Groq inference overridden, not used. `work_mode` derived deterministically from percentage (FR-005). `regie` is a hardcoded mapping from source label, not LLM inference. |
| **V. Chirurgical** | ✅ | One new scraper file + one `scorer.py` override block + one diagnostic script. No pipeline rewiring, no refactoring of existing scrapers. |
| **VI. Validation empirique** | ✅ | SC-006 (non-regression on existing 78 jobs), SC-005 (zero dupes), SC-001 (completeness via in-page oracle). |
| **VII. Sécurité** | ✅ | User-Agent browser header (FR-002). Egress to `freelancermap.com` only. No credentials or API tokens. |
| **VIII. Modèle d'acquisition** | ✅ | Board scraper (`ACQUISITION_MODEL = "board"`), query-driven by country + contract type. Fits existing board pattern. |
| **IX. Scoring optionnel** | ✅ | Scraper produces `JobPosting` via `save_unscored()`. Pipeline works without scoring configured. |

**Gate: PASS. No violations to justify.**

## Project Structure

### Documentation (this feature)

```text
specs/024-spec-freelancermap-scraper/
├── 024 - spec-freelancermap-scraper.md   # Feature specification
├── plan.md                                # This file
├── tasks.md                               # Phase 2 output (/speckit-tasks)
```

### Source Code

```text
scrapers/boards/freelancermap.py   # NEW — the scraper
scorer.py                           # Contract type override logic (FR-004)
scripts/filter_funnel.py            # NEW — diagnostic script (FR-019)

# Prose path (no code change):
#   tracker_views/settings.py → add DE/FR titles to job_titles via UI
```

## Implementation Phases

### Phase 1: `scorer.py` — Authoritative contract type override

**Deliverable**: In `scorer.py`, add:

```python
AUTHORITATIVE_CONTRACT_SOURCES = {"Freelancermap"}
```

In `extract_job_fields()`, after parsing the LLM result, override `contract_type` if the source is authoritative:

```python
if (job.source in AUTHORITATIVE_CONTRACT_SOURCES 
    and job.contract_type 
    and job.contract_type != "unknown"):
    result["contract_type"] = job.contract_type
```

**Why first**: Independent of the scraper. Can be tested with a manually inserted job. Enables SC-004 (100% source-authoritative contract_type for freelancermap).

**Files touched**: `scorer.py` only (one block, ~8 lines).

### Phase 2: `scrapers/boards/freelancermap.py` — The scraper

**Deliverable**: New board scraper inheriting from `BaseScraper`.

**Configuration**:
- `SOURCE_NAME = "Freelancermap"`
- `ENABLED = True`
- `ACQUISITION_MODEL = "board"`
- Country list configurable via class attribute, default `[{"slug": "switzerland", "id": 3}]`
- Contract types: `["contracting", "employee_leasing"]` (freelance + regie)
- Max 3 pages per country (FR-008)

**Fetch flow**:
1. For each country in configured list:
   1. For each page (1 to 3):
      - GET `https://www.freelancermap.com/projects/{slug}?projectContractTypes[0]=contracting&projectContractTypes[1]=employee_leasing&countries[]={id}&sort=1&pagenr={page}`
      - Browser User-Agent required (FR-002)
      - 0.5s delay between pages
   2. Parse response:
      - Extract job cards from page (embedded JSON React-on-Rails `ProjectSearch` component data OR internal API endpoint — whichever is available; JSON preferred over HTML scraping)
      - Extract: title, company, location, contract_type label, remote percentage, duration, start date, URL
   3. Oracle in-page (FR-013): parse `"N jobs & projects"` counter from page header, compare to parsed card count
2. Map each project to `JobPosting`:
   - `source = self.SOURCE_NAME`
   - `contract_type`: map source labels per FR-006 (`contracting` → `freelance`, `employee_leasing` → `regie`)
   - `work_mode`: per FR-005 (100% → `remote`, 1-99% → `hybrid`, "On-site" → `on-site`)
   - `posted_date`: parse two formats per edge cases spec (today's time `HH:MM` → `date.today()`, older `DD.MM.YYYY` → `date`)
   - `location`: truncate to reasonable length (edge case: long location text)
   - `tags`: empty list
   - `salary`: None
3. Dedup by URL (FR-012) — skip if already in `self._storage` or in current batch
4. Return `list[JobPosting]`

**Title filtering**: None. The scraper returns all fetched projects unfiltered. Title matching happens in `filters.py` downstream (FR-017 decision).

**Files touched**: `scrapers/boards/freelancermap.py` only (new file, ~250 lines).

### Phase 3: `scripts/filter_funnel.py` — Diagnostic script

**Deliverable**: Standalone read-only script, same pattern as `scripts/audit_provenance.py`.

```bash
python scripts/filter_funnel.py --source Freelancermap [--db path/to/jobs.db]
```

**Logic**:
1. Read jobs from DB for the given source
2. Import `JobFilterEngine` + active profile predicates (do NOT reimplement)
3. Apply each filter stage in sequence, logging counts:
   ```
   collectés bruts          →  N
     après filtre titre     →  N
     après filtre work_mode →  N
     après filtre langue    →  N
     après filtre géo       →  N
     au digest (score≥seuil)→  N
   ```

**Files touched**: `scripts/filter_funnel.py` only (new file, ~80 lines).

### Phase 4: Prose path — DE/FR job_titles

**Deliverable**: Add to active profile's `job_titles` via Settings UI:
- `Leiter Produktmanagement`
- `Chef de produit`
- `Responsable produit`

**Why**: Enables FR-017 title matching for German/French PM roles. Benefits all sources, not just freelancermap. Existing substring matching already covers `Product Owner`, `Produktmanager`, `Produkt Manager`.

**No code change required.** Done via Streamlit Settings page.

### Phase 5: Validation

Run the full pipeline and verify all success criteria:

1. **SC-001 (completeness)**: Parsed card count matches in-page counter — verified by FR-013 oracle
2. **SC-001b (yield measured)**: PM/PO yield reported via `filter_funnel.py`, no minimum threshold enforced
3. **SC-003 (<25 min)**: Run `main.py` with all scrapers, verify wall-clock time
4. **SC-004 (contract_type source)**: Verify 100% of freelancermap jobs have `contract_type` from source, not Groq. Check: `AUTHORITATIVE_CONTRACT_SOURCES` override is exercised
5. **SC-005 (zero dupes)**: Two consecutive runs, verify `COUNT(DISTINCT url)` = `COUNT(url)` for freelancermap jobs
6. **SC-006 (no regression)**: Compare job counts per source before/after change
7. **SC-007 (filter tunnel)**: Run `filter_funnel.py --source Freelancermap`, verify each stage count is non-zero and attributable

## Edge Cases — Implementation Notes

- **403 without User-Agent** (verified): Always send `Mozilla/5.0` browser header
- **Long location text**: Truncate to 200 chars with `[:200]`, do not crash
- **German-language content**: Groq scoring works on non-English text — verify with 2-3 sample jobs
- **Anonymized titles** (`ID: *****`): Accept as-is, pass to pipeline, let title filter reject them naturally
- **Two date formats**: Parse `HH:MM` → `date.today()`, `DD.MM.YYYY` → `datetime.strptime()`. Use fallback `None` for unparseable dates.
- **`contractTypes[]` vs `projectContractTypes[]`**: Per FR-015 trap — use ONLY `projectContractTypes[]`. The `contractTypes[]` param is silently accepted but doesn't filter.
- **Country slugs vs IDs**: URL path uses slug (`/switzerland`), but `countries[]` param uses numeric ID. The `countries[]` param is authoritative for multi-country iteration.

## Complexity Tracking

No constitution violations — this section intentionally left empty.
