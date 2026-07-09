# Implementation Plan: HeadHunter Network Scraper

**Branch**: `023-hh-network-scraper` | **Date**: 2026-07-09 | **Spec**: [spec.md](spec.md)

**Status**: Ready. Spec 022 reverted — this spec has no code dependency on it.

## Summary

Three parts: (A) a new `hh_network` board scraper for HeadHunter's public REST API
across 6 CIS areas, (B) extraction vocabulary amendments adding `russian`/`turkish`
languages, `russia_cis` geo zone, RUB salary normalization, and English-only
summaries, and (C) operator-only prose-path edits in Settings. No schema changes,
no UI changes beyond geo-zone option lists, no profiles.py touched.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: `requests` (stdlib pattern, already used by other scrapers)
**Storage**: No schema changes — uses existing `JobPosting` fields and `save_unscored()`
**Testing**: pytest (152 tests), +4 mock cases in `score.py`
**Target Platform**: Dev Mac → Docker/Linux production (egress via gluetun-scrape:8890)
**Project Type**: CLI pipeline + Streamlit UI
**Performance Goals**: 5 pages × 100 results per (query × area), ~0.5s sleep between detail calls
**Constraints**: No new dependencies, no profiles.py changes, no storage.py changes, no settings UI design

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Filet large** | ✅ | English `text=` queries are board query scoping, not relevance filtering. Latin-title counter observes pre-existing pipeline, doesn't add a gate. |
| **II. Deux chemins** | ✅ | Part C is prose path; Parts A/B are code path. Listed together for sequencing, never mixed. |
| **IV. Structure déterministe** | ✅ | work_mode hint only; classification stays in extractor. |
| **V. Chirurgical** | ✅ | One new file + vocab line-edits in scorer.py + option list edit. No pipeline rewiring. |
| **VII. Sécurité** | ✅ | Existing egress path only. User-Agent identifies agent. No credentials. |

**Gate: PASS.**

## Project Structure

### Documentation

```text
specs/023-hh-network-scraper/
├── spec.md, plan.md, tasks.md
```

### Source Code

```text
scrapers/boards/hh_network.py   # NEW — the scraper
scorer.py                        # Part B — vocab amendments (4 changes)
score.py                         # 4 new mock cases
tracker_views/settings.py        # GEO_ZONES += russia_cis
```

## Implementation Phases

### Phase 1: scorer.py vocab amendments (independent, ship first)
### Phase 2: score.py mock cases
### Phase 3: scrapers/boards/hh_network.py (scraper)
### Phase 4: tracker_views/settings.py geo_zone option
### Phase 5: Validation
