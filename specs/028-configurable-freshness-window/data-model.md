# Data Model: Configurable Freshness Window

No schema change. This spec reuses existing structures.

## config table (existing — storage.py:253)

| key | value | default (when absent) |
|-----|-------|-----------------------|
| `freshness_days` | string integer, e.g. `"30"` | `"30"` |

- Read via `JobStorage.get_config("freshness_days", default="30")`.
- Written via `JobStorage.set_config("freshness_days", str(days))`.
- Coerced to `int` by `JobStorage.get_freshness_days()`; malformed/absent → 30.
- Global (not per-profile), mirroring `purge_retention_days`.

## `JobFilter.date_from` (existing — models.py:104)

`Optional[date] = None`. Already present; not modified.

Becomes the single carrier of the freshness cutoff:
- `scrape.py` (broad path) sets `date_from = date.today() - timedelta(days=get_freshness_days())`.
- `filters.py` reads `job_filter.date_from or (date.today() - timedelta(days=30))`.
- The Joinup scraper (spec 027) already reads `date_from` for its early-stop.

## Relationship to purge (documented, unchanged)

| Window | Key | Dates off | Role |
|--------|-----|-----------|------|
| Freshness | `freshness_days` | `posted_date` | admission (which jobs enter / are shown) |
| Purge | `purge_retention_days` | `first_seen` | survival (how long an un-actioned job stays) |

If `freshness_days` > `purge_retention_days`, untouched jobs still vanish at the
purge horizon — expected; stated in the Settings help text.
