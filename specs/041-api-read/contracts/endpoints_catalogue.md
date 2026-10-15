# Contract — health + `read:catalogue` endpoints (US1, US3, FR-007)

All responses are explicit pydantic models with `extra="forbid"` (US3-4). Dates
and enums are the strings `JobStorage` returns — no reformatting.

## `GET /health` — no auth (US1-4)

```json
{"status": "ok", "version": "<git sha>"}
```

- `version` = the running image's git SHA (injected at app build/startup, never a
  live DB read). No data beyond `status` + `version`.

## `GET /v1/jobs` — `read:catalogue`

Query: `company_id` (int), `source` (str), `posted_after` (ISO date);
`limit` (default 100, max 500), `offset` (default 0).

Response: the list envelope (`{items, total, limit, offset}`). Each item mirrors
`get_catalogue_jobs` keys **verbatim**:

```
id, title, company, company_id, url, source, location, base_location,
posted_date, description, first_seen, last_seen, extracted_at, extracted_by,
monitored_company_id, canonical_url, norm_title, norm_company, summary,
work_mode, geo_zone, company_size, contract_type, company_country,
industry_sector, language_required, country_code
```

- `total` = count after `company_id`/`source`/`posted_after` filters, before slicing.
- No user-space field is present (contract test asserts the set of keys).

## `GET /v1/jobs/{job_id}` — `read:catalogue`

Response: a single item with the same keys as `GET /v1/jobs` (source
`get_catalogue_job`). Unknown job → `404` `{"error":"not_found", …}`.

## `GET /v1/companies/{company_id}` — `read:catalogue`

Response: a single company-facts item mirroring `get_company_facts` keys **verbatim**:

```
id, name, website, careers_url, company_country, industry_sector, company_size,
ats_provider, ats_identifier, scraper_id, first_seen_at, last_seen_at, summary,
monitoring_status, scraping_method, research_notes, research_confidence,
researched_at, x_handle
```

- Unknown company → `404`. No user-space `status`/`notes`/`monitored`, no
  user-space aggregates (contract test asserts the set of keys).

## Tests (US3, FR-008, FR-009)

- Contract test per endpoint: seed an in-memory DB, call the corresponding
  `JobStorage` method, and assert the API payload equals the method output
  field-for-field (after serialisation), and that the key set contains no
  user-space key.
- `404` for absent ids; `read:catalogue` routes reject a `read:me`-only token
  with `403`.
