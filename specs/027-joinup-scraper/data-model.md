# Data Model — Joinup scraper

No new entities, tables, or schema changes. The scraper is a **function**
`fetch(JobFilter) → list[JobPosting]` that maps raw Typesense hits onto the
existing `JobPosting` dataclass (`models.py`), which the orchestrator persists.

## Input → output mapping (hit → JobPosting)

| JobPosting field | Source | Notes |
|------------------|--------|-------|
| `source` | constant | `"Joinup"` |
| `title` | `hit["title"]` | fall back to `hit["headline"]`, else `""` |
| `company` | `hit["startup"]` | |
| `location` | `hit["location"]` | |
| `url` | `https://joinup.ch/job/{hit['slug']}` | |
| `posted_date` | `hit["created"]` (ms epoch) | `int()` guard; unparseable → `None` |
| `description` | `hit["description"]` | raw markdown; `__post_init__` caps at 3000 |
| `tags` | `skills` + `jobType` + `paymentType` + `startupIndustry` | skills may be list or stringified list |
| `work_mode` | `location == "Remote"` | `"remote"` else `None` (no other classification) |
| `base_location` | — (unset) | `None` |
| `geo_zone`, `company_size`, `contract_type`, `company_country`, `industry_sector`, `comp_*` | — (unset) | filled later by extractor/scorer |

## Identity

- `JobPosting.id` = `sha256(canonical_url)[:20]`; `canonical_url` is passthrough
  for Joinup, so identity is the raw `/job/{slug}` URL.
- In-scraper dedup is by `hit["id"]` (int) across pagination.

## Derived fields (computed by `models.JobPosting.__post_init__`)

- `canonical_url` = `normalize_url(url, "Joinup")` → passthrough.
- `norm_title` / `norm_company` — used by the orchestrator's cross-source dedup,
  not the scraper.
