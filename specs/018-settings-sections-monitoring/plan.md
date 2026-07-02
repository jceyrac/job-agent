# Plan — Spec 018: Settings sections + monitoring master-switch

## Summary

Reorganise Settings into three sections (Profile Editor / Broad scraping / Company Monitoring), split scraper toggles by acquisition model via class attributes, and add a non-destructive per-ATS monitoring master-switch with cascade re-arm.

## Technical Context

- **Language**: Python 3.11
- **Storage**: SQLite config keys (`monitoring.ats.<provider>.enabled`, existing `scraper.<slug>.enabled`)
- **UI**: Streamlit with expanders, checkboxes, selectboxes
- **No schema changes**, no new dependencies

## Constitution Check

| Principle | Status |
|---|---|
| VIII (acquisition models) | ✅ Strengthened — `ACQUISITION_MODEL` explicit in code, UI split reflects it |
| II (two paths) | ✅ All switches = config writes (prose path); class attrs + gates = code payload |
| V (surgical) | ✅ 2 class attrs + 2 pipeline gates + Settings reorg; no fetch changes |
| III / IX | ✅ Unchanged |

## SUPPORTS_DISCOVERY per scraper (verified by reading each fetch)

| Scraper | SUPPORTS_DISCOVERY | Reason |
|---|---|---|
| greenhouse.py | True | Seed + DB watching fallback |
| lever.py | True | `get_lever_slugs()` seed + DB watching |
| workable.py | True | `get_workable_slugs()` seed + DB watching |
| ashby.py | False | `if self._targets is None: return []` |
| gem.py | False | `if self._targets is None: return []` |
| recruitee.py | False | `if self._targets is None: return []` |
| smartrecruiters.py | False | `if self._targets is None: return []` |
| workday.py | False | `if self._targets is None: return []` |
| sygnum.py | True | Self-contained, always runs |
| tangem.py | True | Self-contained, always runs |

## Files changed (6 files)

| # | File | Change |
|---|---|---|
| 1 | `scrapers/base.py` | Add `ACQUISITION_MODEL` + `SUPPORTS_DISCOVERY` class attrs |
| 2 | 10 company-keyed scrapers | Set `ACQUISITION_MODEL="company_keyed"` + per-file `SUPPORTS_DISCOVERY` |
| 3 | `scrape.py` | Broad-path discovery skip + monitored-path per-provider gate |
| 4 | `tracker_views/shared.py` | Repoint `is_monitoring_source_enabled` to monitoring key |
| 5 | `tracker_views/settings.py` | `_render_broad_scraping` + `_render_company_monitoring` rewrite |
| 6 | Validate | Toggle independence, cascade re-arm, config key verification |

## Key design decisions

1. **Independence invariant**: broad reads only `scraper.<slug>.enabled`; monitoring reads only `monitoring.ats.<provider>.enabled`. No cross-reading.
2. **Master switch default**: absent key → enabled (default on). Only explicit `"false"` pauses.
3. **Cascade re-arm**: monitoring a company under a paused ATS flips the switch back to `"true"`.
4. **Non-destructive**: pausing never writes company rows — only the config key changes.
5. **Discovery ATS label**: Greenhouse/Lever/workable shown with "(discovery)" suffix in Broad scraping section.
