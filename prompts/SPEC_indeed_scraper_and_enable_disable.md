# SPEC — Indeed Scraper Extraction + Scraper Enable/Disable

> Spec for Claude Code.  
> Read `scrapers/base.py`, `scrapers/jobspy_scraper.py`, `scrape.py`, and `storage.py` before starting.  
> Do not modify `models.py`, `filters.py`, `profiles.py`, or any scorer file.

---

## Overview

Two changes in this spec:

1. **Extract Indeed** out of `jobspy_scraper.py` into its own `scrapers/indeed.py`
2. **Add scraper enable/disable** — persistent flag in DB config + auto-disable after N consecutive timeouts during a run

---

## Part 1 — `scrapers/indeed.py`

### Goal

Move all Indeed-specific logic out of `JobSpyScraper` into a standalone `IndeedScraper` class with the same interface as every other scraper (`fetch()` → `list[JobPosting]`).

### What to extract from `jobspy_scraper.py`

- `SEARCH_TERMS_INDEED` list
- `INDEED_COUNTRIES` list
- The entire Indeed loop (currently inside `JobSpyScraper.fetch()`)
- `_scrape_with_timeout()` — **do not duplicate**. Move it to a shared helper or keep it in `jobspy_scraper.py` and import it in `indeed.py`. Preferred: extract to a module-level function `_scrape_with_timeout(timeout_seconds, **kwargs)` in a new `scrapers/_jobspy_helpers.py` file, imported by both scrapers.
- `_dataframe_to_postings()` — same situation. Keep in `_jobspy_helpers.py` and import in both scrapers.
- `_add_unique()` — same.

### New file: `scrapers/indeed.py`

```python
from scrapers.base import BaseScraper
from scrapers._jobspy_helpers import scrape_with_timeout, dataframe_to_postings, add_unique
from models import JobFilter, JobPosting
import time

SEARCH_TERMS_INDEED = [ ... ]  # same as current

INDEED_COUNTRIES = [ ... ]  # same as current

class IndeedScraper(BaseScraper):
    SOURCE_NAME = "Indeed"
    ENABLED = True

    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        # Indeed loop — identical logic to what's currently in JobSpyScraper.fetch()
        # Returns list[JobPosting] with source="Indeed"
```

### Updated `scrapers/jobspy_scraper.py`

- Remove Indeed loop, `SEARCH_TERMS_INDEED`, `INDEED_COUNTRIES`
- Remove `_scrape_with_timeout`, `_dataframe_to_postings`, `_add_unique` (now in helpers)
- Import and use helpers from `scrapers._jobspy_helpers`
- Keep `SEARCH_TERMS_LINKEDIN`, `LINKEDIN_LOCATIONS`, `KNOWN_COUNTRIES`, `_COUNTRY_PATTERN`, `_extract_country_from_html`
- `SOURCE_NAME = "JobSpy:Linkedin"` (rename from `"JobSpy"` for clarity — update any log messages accordingly)
- Summary log at end of `fetch()` only covers LinkedIn (Indeed no longer runs here)

### New file: `scrapers/_jobspy_helpers.py`

Module-level helpers shared between `JobSpyScraper` and `IndeedScraper`:

```python
def scrape_with_timeout(timeout_seconds: int, **kwargs):
    """Runs jobspy.scrape_jobs() in a thread with a hard timeout.
    Returns DataFrame or None on timeout."""
    ...

def dataframe_to_postings(df, source: str) -> list[JobPosting]:
    """Converts a jobspy DataFrame to list[JobPosting]."""
    ...

def add_unique(df, source: str, seen_urls: set, all_jobs: list) -> tuple[int, int]:
    """Appends unique postings to all_jobs, returns (new_count, dupe_count)."""
    ...
```

Move the existing implementations verbatim. Do not change their logic.

---

## Part 2 — Scraper enable/disable

### Goal

- Each scraper can be disabled persistently (survives restarts) via a DB config key
- `scrape.py` skips disabled scrapers before instantiating them
- A scraper auto-disables itself mid-run after N=3 consecutive timeouts, writes a warning to DB, and logs it
- Re-enabling is manual (via `storage.set_config()` directly, or future Streamlit UI)

### Storage

Use the existing `config` table (key/value) in `storage.py`. The key format:

```
scraper.<SOURCE_NAME>.enabled    →  "true" | "false"
scraper.<SOURCE_NAME>.disabled_reason  →  human-readable string, e.g. "auto-disabled: 3 consecutive timeouts"
```

`SOURCE_NAME` is lowercased and spaces replaced with underscores for the key, e.g.:
- `"Indeed"` → `scraper.indeed.enabled`
- `"JobSpy:Linkedin"` → `scraper.jobspy_linkedin.enabled`

No new table needed — `config` table is already in the schema.

### Changes to `scrapers/base.py`

Add a `storage` parameter to `BaseScraper.__init__()` so scrapers can write their disabled state:

```python
class BaseScraper(ABC):
    SOURCE_NAME: str = "Unknown"
    ENABLED: bool = True  # class-level default; overridden by DB at runtime

    def __init__(self, storage: "JobStorage"):
        self._storage = storage
        self._config_key = f"scraper.{self.SOURCE_NAME.lower().replace(' ', '_').replace(':', '_')}"

    def is_enabled(self) -> bool:
        """Check DB config. Falls back to class-level ENABLED if no DB entry."""
        val = self._storage.get_config(f"{self._config_key}.enabled")
        if val is None:
            return self.ENABLED
        return val.lower() == "true"

    def disable(self, reason: str) -> None:
        """Persist disabled state to DB."""
        self._storage.set_config(f"{self._config_key}.enabled", "false")
        self._storage.set_config(f"{self._config_key}.disabled_reason", reason)
        print(f"  🚫 [{self.SOURCE_NAME}] auto-disabled: {reason}")

    @abstractmethod
    def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
        ...
```

### Changes to `scrape.py`

**`discover_scrapers()`** — no change needed (still returns classes, not instances).

**`main()` loop** — instantiate with `storage` and call `is_enabled()` before running:

```python
db = JobStorage(DB_PATH)

for ScraperClass in scraper_classes:
    scraper = ScraperClass(storage=db)          # pass storage on init
    if not scraper.is_enabled():
        print(f"[{scraper.SOURCE_NAME}] disabled — skipping")
        continue
    print(f"Fetching from {scraper.SOURCE_NAME}...")
    raw = scraper.fetch(job_filter)
    ...
```

### Auto-disable on timeouts — `scrapers/indeed.py`

`IndeedScraper` tracks consecutive timeouts during the run. After `MAX_CONSECUTIVE_TIMEOUTS = 3`, it calls `self.disable()` and breaks out of the loop early:

```python
MAX_CONSECUTIVE_TIMEOUTS = 3

def fetch(self, job_filter: JobFilter) -> list[JobPosting]:
    consecutive_timeouts = 0
    all_jobs = []
    seen_urls = set()

    for term in SEARCH_TERMS_INDEED:
        for country in INDEED_COUNTRIES:
            df = scrape_with_timeout(60, site_name=["indeed"], ...)
            if df is None:
                consecutive_timeouts += 1
                print(f"  ⚠️ [Indeed] '{term}' [{country}]: timed out ({consecutive_timeouts}/{MAX_CONSECUTIVE_TIMEOUTS})")
                if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                    self.disable(f"{MAX_CONSECUTIVE_TIMEOUTS} consecutive timeouts — likely IP block")
                    return all_jobs  # return whatever was collected before the block
                time.sleep(2)
                continue
            consecutive_timeouts = 0  # reset on success
            new, skipped = add_unique(df, "Indeed", seen_urls, all_jobs)
            ...
    return all_jobs
```

**Do not add auto-disable to `JobSpyScraper`** (LinkedIn) — LinkedIn timeouts are rare and not an IP-block signal.

### Re-enabling a scraper

Manual only for now. From the shell:

```bash
python -c "from storage import JobStorage; db = JobStorage('data/jobs.db'); db.set_config('scraper.indeed.enabled', 'true')"
```

Document this command in a comment at the top of `scrapers/indeed.py`.

---

## Checklist for Claude Code

- [ ] Create `scrapers/_jobspy_helpers.py` with `scrape_with_timeout`, `dataframe_to_postings`, `add_unique`
- [ ] Create `scrapers/indeed.py` with `IndeedScraper` using helpers
- [ ] Update `scrapers/jobspy_scraper.py`: remove Indeed logic, import helpers, rename SOURCE_NAME to `"JobSpy:Linkedin"`
- [ ] Update `scrapers/base.py`: add `__init__(storage)`, `is_enabled()`, `disable(reason)`
- [ ] Update `scrape.py` main loop: pass `storage=db` on scraper init, call `is_enabled()` before running each scraper
- [ ] Add `MAX_CONSECUTIVE_TIMEOUTS = 3` auto-disable logic in `IndeedScraper.fetch()`
- [ ] Verify `discover_scrapers()` in `scrape.py` still works — it checks `ENABLED` class attr, which is now a fallback; `is_enabled()` is the runtime check
- [ ] Smoke test: `python scrape.py` with `scraper.indeed.enabled = false` in DB → Indeed skipped, LinkedIn runs normally
- [ ] Smoke test: manually set `MAX_CONSECUTIVE_TIMEOUTS = 1` and verify auto-disable writes to DB after first timeout

---

## What NOT to change

- `models.py`
- `filters.py`
- `profiles.py`
- `scorer.py` / `score.py`
- `storage.py` schema (config table already exists from previous spec)
- Any other scraper files not named above
