# Spec 003 — Lever & Workable Scrapers + Greenhouse Board IDs

## Goal

Three closely related additions to the scraping pipeline:

1. **Greenhouse board IDs** — add `dfinity` and `amun` to the existing Greenhouse scraper config. Zero new code, config only.
2. **Lever scraper** — new `LeverScraper` supporting any company board at `jobs.lever.co/{company}`. Initial boards: `impossiblecloud`.
3. **Workable scraper** — new `WorkableScraper` supporting any company board at `apply.workable.com/{company}`. Initial boards: `walletconnect`.

Both new scrapers follow the existing `BaseScraper` ABC pattern. They are config-driven so adding a new company requires only one line in `profiles.py` or a config list — no new scraper file needed.

## Context

From the company watchlist audit (Tier A), three ATS platforms were identified:
- **Greenhouse** (already scraped): Dfinity (`dfinity`), Crypto Finance AG (TBC), 21Shares (`amun`)
- **Lever** (new): Impossible Cloud (`impossiblecloud`)
- **Workable** (new): WalletConnect/Reown (`walletconnect`, `walletconnect-foundation`)

Lever and Workable both expose public JSON APIs — no authentication required, no rate limiting issues.

## API Details

### Lever
- Jobs list: `https://api.lever.co/v0/postings/{company}?mode=json`
- Returns JSON array of postings with fields: `id`, `text` (title), `categories` (team, location, commitment), `description`, `hostedUrl`, `createdAt`
- No auth required.

### Workable
- Jobs list: `https://apply.workable.com/api/v3/accounts/{company}/jobs`
- Method: POST with body `{"query": "", "location": [], "department": [], "worktype": [], "remote": []}`
- Returns JSON with `results` array containing: `id`, `title`, `department`, `location`, `url`, `created_at`
- No auth required.

## Scraper Interface

Both scrapers must implement `BaseScraper`:

```python
class LeverScraper(BaseScraper):
    SOURCE = "lever"
    
    def __init__(self, company_slugs: list[str]):
        self.company_slugs = company_slugs  # e.g. ["impossiblecloud"]
    
    def fetch(self) -> list[JobPosting]:
        ...
```

```python
class WorkableScraper(BaseScraper):
    SOURCE = "workable"
    
    def __init__(self, company_slugs: list[str]):
        self.company_slugs = company_slugs  # e.g. ["walletconnect"]
    
    def fetch(self) -> list[JobPosting]:
        ...
```

## JobPosting Mapping

Both scrapers must map to the existing `JobPosting` dataclass. Key fields:

| JobPosting field | Lever source | Workable source |
|---|---|---|
| `title` | `posting.text` | `job.title` |
| `company` | slug (capitalized) | slug (capitalized) |
| `location` | `categories.location` | `job.location.city + country` |
| `url` | `hostedUrl` | `job.url` |
| `date_posted` | `createdAt` (ms epoch → datetime) | `created_at` (ISO string) |
| `source` | `"lever"` | `"workable"` |
| `description` | `descriptionPlain` (strip HTML) | fetch individual job page if needed, else empty |
| `job_id` | `id` | `id` (shortcode) |

## Greenhouse Config Change

In the existing Greenhouse scraper config (wherever `GREENHOUSE_BOARDS` or equivalent is defined), add:

```python
"dfinity",   # Dfinity / ICP — Zug
"amun",      # 21.co / 21Shares — Zurich
```

Read `scraper_greenhouse.py` to find the exact config location before editing.

## Files to create/modify

- **Create** `scraper_lever.py` at project root (alongside other scrapers)
- **Create** `scraper_workable.py` at project root
- **Modify** `scraper_greenhouse.py` — add board IDs to config list
- **Modify** `main.py` or `scrape.py` — instantiate and register `LeverScraper` and `WorkableScraper`

Read `main.py` / `scrape.py` to understand how scrapers are discovered and registered before modifying.

## Non-goals

- No authentication handling
- No proxy support (add `curl_cffi` only if 403s appear in testing)
- No pagination beyond what the API naturally returns (Lever returns all postings; Workable may paginate — handle `next_page` token if present)
- No deduplication logic (handled downstream by `storage.py`)
- Do not modify `scorer.py`, `storage.py`, or `models.py`
