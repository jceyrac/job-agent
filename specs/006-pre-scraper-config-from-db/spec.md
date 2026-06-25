# Spec 006-pre — Scraper Config from DB

## Context

Currently the Greenhouse, Lever, and Workable scrapers have their company slugs
hardcoded in Python files:

- `scrapers/greenhouse.py` — `GREENHOUSE_BOARDS = ["dfinity", "amun", ...]`
- `scrapers/ats/workable.py` — `DEFAULT_SLUGS = ["walletconnect", ...]`
- `scrapers/ats/lever.py` — equivalent hardcoded list

This is wrong for two reasons:
1. Adding a new company to monitor requires a code change + deploy, not just a DB
   update via the UI.
2. The DB `monitoring_status = 'watching'` is meaningless if the scraper ignores it
   and reads from its own hardcoded list instead.

The fix: scrapers read their target slugs from the DB at runtime, filtering on
`monitoring_status = 'watching'` and `scraping_method = '{provider}'`.

---

## Goal

Remove all hardcoded slug/board lists from Greenhouse, Lever, and Workable scrapers.
Replace with a DB query at scraper init time. The hardcoded lists become the
**fallback seed** only — used if the DB returns zero rows on a fresh install.

Once this spec is implemented and deployed, the status lifecycle becomes fully
meaningful:

```
watch_pending  — ATS/scraper identified, not yet operational
    ↓  (user activates via UI after 006-pre deployed)
watch_ready    — ready to activate, scraper can serve it
    ↓  (user clicks "Activate monitoring" in UI)
watching       — scraper picks it up on next cron run
```

---

## Important note on legacy `watching` companies

The 30 companies currently at `watching` (Aave, Circle, Coinbase, etc.) were
created before spec 004 and have `scraping_method = NULL`. They are NOT covered
by `get_watching_companies_by_method()` which filters on `scraping_method = ?`.

These legacy companies are served by the existing `GREENHOUSE_BOARDS` seed list
which already contains their slugs. After this spec is implemented, they will
continue to be served via the seed fallback.

**Do not change the status or data of these 30 legacy companies.** They work
correctly today and must continue to work after this spec.

The seed list must therefore remain complete (all existing legacy slugs intact)
and only be used as fallback when the DB returns zero rows for a given method.

---

## Changes per scraper

### `scrapers/greenhouse.py`

```python
# Before
GREENHOUSE_BOARDS = [
    "dfinity",
    "amun",
    # ... all existing slugs
]

# After — preserve ALL existing slugs as seed fallback
GREENHOUSE_BOARDS_SEED = [
    "dfinity",
    "amun",
    # ... all existing slugs — do not remove any
]

def get_greenhouse_boards(db: JobStorage) -> list[str]:
    """Return slugs from DB (watching + greenhouse).
    Falls back to GREENHOUSE_BOARDS_SEED if DB returns zero rows (fresh install).
    NOTE: legacy watching companies have scraping_method=NULL and are not returned
    by the DB query — they remain covered by the seed list.
    """
    rows = db.get_watching_companies_by_method("greenhouse")
    slugs = [r["ats_identifier"] for r in rows if r.get("ats_identifier")]
    # Merge DB slugs with seed to also cover legacy companies
    all_slugs = list(dict.fromkeys(GREENHOUSE_BOARDS_SEED + slugs))
    return all_slugs
```

Note the merge strategy: seed first (legacy companies), then DB slugs appended.
`dict.fromkeys()` deduplicates while preserving order.

### `scrapers/ats/lever.py`

```python
LEVER_SLUGS_SEED = ["impossiblecloud"]

def get_lever_slugs(db: JobStorage) -> list[str]:
    rows = db.get_watching_companies_by_method("lever")
    slugs = [r["ats_identifier"] for r in rows if r.get("ats_identifier")]
    return list(dict.fromkeys(LEVER_SLUGS_SEED + slugs))
```

### `scrapers/ats/workable.py`

```python
WORKABLE_SLUGS_SEED = ["walletconnect", "walletconnect-foundation"]

def get_workable_slugs(db: JobStorage) -> list[str]:
    rows = db.get_watching_companies_by_method("workable")
    slugs = [r["ats_identifier"] for r in rows if r.get("ats_identifier")]
    return list(dict.fromkeys(WORKABLE_SLUGS_SEED + slugs))
```

---

## New method on JobStorage

```python
def get_watching_companies_by_method(self, scraping_method: str) -> list[dict]:
    """Return companies with monitoring_status='watching' and given scraping_method.
    Does NOT return legacy companies with scraping_method=NULL.
    """
    with self._conn() as conn:
        rows = conn.execute(
            """SELECT id, name, ats_identifier, ats_provider, careers_url
               FROM companies
               WHERE monitoring_status = 'watching'
                 AND scraping_method = ?
                 AND ats_identifier IS NOT NULL""",
            (scraping_method,),
        ).fetchall()
        return [dict(r) for r in rows]
```

---

## How scrapers receive the DB instance

Read `scrape.py` and `main.py` carefully before making any changes — understand
how scrapers are currently instantiated. The `db` instance is already available
at the point where scrapers are created. Pass it through as a constructor argument.

Do NOT create a new `JobStorage` instance inside the scraper. If the scraper
`__init__` currently takes no arguments, add `db: JobStorage` as first argument
and update the instantiation call in `scrape.py` accordingly.

---

## Transition behaviour

| DB state | Scraper behaviour |
|---|---|
| `watching` + `scraping_method` set + `ats_identifier` set | ✅ Picked up from DB |
| `watching` + `scraping_method = NULL` (legacy) | ✅ Covered by seed list |
| `watch_ready` + `ats_identifier` set | ❌ Not scraped — user must activate |
| `watch_pending` + `ats_identifier` set | ❌ Not scraped — not yet activated |
| `watching` + `ats_identifier = NULL` | ⚠️ Skipped with warning log |

---

## Files to modify

- **`storage.py`** — add `get_watching_companies_by_method()` method
- **`scrapers/greenhouse.py`** — rename to `GREENHOUSE_BOARDS_SEED`, add `get_greenhouse_boards(db)`, merge DB + seed
- **`scrapers/ats/lever.py`** — rename slug constant, add `get_lever_slugs(db)`, merge DB + seed
- **`scrapers/ats/workable.py`** — rename slug constant, add `get_workable_slugs(db)`, merge DB + seed
- **`scrape.py`** — pass `db` to scraper instantiation where needed

## Non-goals

- No change to Ashby or custom HTML scrapers
- No change to `main.py` orchestration logic
- No UI changes
- No status changes on legacy `watching` companies
- Do not remove any slug from existing seed lists
