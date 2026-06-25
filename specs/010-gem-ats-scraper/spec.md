# Spec 010 — Gem ATS Scraper

> Spec for Claude Code. Read `scrapers/ats/ashby.py` and `scrapers/base.py` before starting.
> Do not modify `storage.py`, `models.py`, `profiles.py`, `main.py`, or any existing scraper.

---

## Context

Gem is an AI-powered recruiting platform with a public Job Board API. One monitored company
(caffeine.ai) uses Gem as its ATS. The job board is hosted at `jobs.gem.com/{vanity_slug}`
and all job data is accessible via a public REST API — no authentication required.

The Gem scraper follows the exact same architecture as the existing Ashby adapter:
company-keyed, driven by DB targets (`monitored_companies` with `ats_provider = 'gem'`),
one API call per company.

---

## API

### Endpoint

```
GET https://api.gem.com/job_board/v0/{vanity_url_path}/job_posts/
```

Where `{vanity_url_path}` is the value stored in `ats_identifier` on the monitored company
(e.g. `caffeine-ai`).

No authentication required. No API key needed.

### Response shape (inferred from Gem documentation)

```json
[
  {
    "id": "...",
    "title": "Senior Product Engineer",
    "location": "Zürich, Switzerland",
    "departments": [{"name": "Engineering"}],
    "offices": [{"location": "Zürich, Switzerland", "name": "Zürich"}],
    "apply_url": "https://jobs.gem.com/caffeine-ai/...",
    "published_at": "2026-05-01T00:00:00Z",
    "description": "..."
  }
]
```

Field names should be verified at runtime against the actual API response. If the shape
differs, Claude Code should adapt accordingly and document the actual field names in a
comment at the top of the file.

### Job post URL pattern

Individual job posts are hosted at:
```
https://jobs.gem.com/{vanity_url_path}/{job_id}
```

Use `apply_url` if present, otherwise construct from `id` and `vanity_url_path`.

---

## File to create

`scrapers/ats/gem.py`

Follow the exact same structure as `scrapers/ats/ashby.py`:
- Class `GemScraper(BaseScraper)`
- `SOURCE_NAME = "Gem"`
- `ENABLED = True`
- `fetch(self, job_filter)` method iterating over `self._targets`
- Each target provides `ats_identifier` (the vanity slug)
- Return `list[JobPosting]`

---

## Field mapping

| Gem API field | JobPosting field | Notes |
|---|---|---|
| `title` | `title` | — |
| `location` or `offices[0].location` | `location` | Prefer `offices[0].location` if available |
| `apply_url` or constructed URL | `url` | See URL pattern above |
| `published_at` | `posted_date` | Parse ISO date, take first 10 chars |
| `description` | `description` | May be HTML — pass as-is, scorer handles it |
| `"Gem"` | `source` | Hardcoded |
| company name from target | `company` | From `self._targets` entry |

---

## Error handling

Same pattern as Ashby:
- HTTP status != 200 → log `[Gem] {slug}: HTTP {status}` and continue
- Empty response or no jobs → log `[Gem] {slug}: 0 jobs` and continue
- JSON parse error → log and continue
- Per-company `time.sleep(1)` between calls

---

## Registration in `__init__.py`

Add `GemScraper` to `scrapers/ats/__init__.py` following the existing pattern for
`AshbyScraper`, `LeverScraper`, etc.

---

## DB update required (manual, before running)

Before testing, update Caffeine.ai in the `monitored_companies` table:

```sql
UPDATE companies
SET ats_provider = 'gem',
    scraping_method = 'gem',
    ats_identifier = 'caffeine-ai'
WHERE name LIKE '%caffeine%';
```

Verify with:
```sql
SELECT name, ats_provider, ats_identifier, status
FROM companies
WHERE name LIKE '%caffeine%';
```

---

## Non-objectives

- No support for Gem's embed code or hosted board HTML scraping — API only
- No support for Gem's authenticated ATS API (requires API key) — public job board API only
- No generic "all companies on Gem" discovery — monitored targets only, same as Ashby

---

## Acceptance criteria

- [ ] `python main.py --monitored-only --mock` runs without error
- [ ] Caffeine.ai jobs appear in output with `source = "Gem"`
- [ ] Job URLs point to `jobs.gem.com/caffeine-ai/...`
- [ ] `posted_date` is correctly parsed from the API response
- [ ] HTTP errors are logged and do not crash the pipeline
- [ ] Adding a second Gem company requires only a DB entry, no code change
