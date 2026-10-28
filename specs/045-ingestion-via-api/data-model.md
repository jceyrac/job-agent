# Data Model — Ingestion through the API (spec 045)

No new catalogue tables. Two persistent additions and two transient payloads.

## Persistent

### `users.is_service` (migration, expand)

- New column `is_service INTEGER NOT NULL DEFAULT 0` on `users`.
- New row: `platform` — `id='platform'`, `display_name='Platform ingestion'`,
  `is_service=1`, `active_profile_id=NULL`, `onboarding_completed_at=NULL`.
- The platform principal owns only `ingest`-scoped `api_tokens`; it has no profile, no
  user-space rows, and is excluded from any user enumeration (guard).
- No removal step in this spec (contraction is step 10).

### `config.ingestion_scope` extension

The JSON value gains two keys (defaults preserve the current shape):

```json
{
  "title_families": ["..."],
  "exclude": ["..."],
  "remote_or_hybrid": false,
  "search_locations": ["Switzerland", "France", "..."],
  "greenhouse_boards": ["coinbase", "chainalysis", "..."]
}
```

- `search_locations` — list[str]; seeded once from the active profile's
  `effective_search_locations()` (order-preserving dedup). Drives LinkedIn/Indeed query
  locations.
- `greenhouse_boards` — list[str]; seeded once from the active profile's
  `greenhouse_boards`. Drives the broad Greenhouse sweep.
- `get_ingestion_scope()`/`set_ingestion_scope()` handle both keys with the existing
  safe-default coercion (`[]` when absent/malformed).

## Transient (assembled at request time)

### Ingestion plan (`GET /v1/ingest/plan` response)

| Field | Type | Source |
|-------|------|--------|
| `title_families` | list[str] | scope |
| `exclude` | list[str] | scope |
| `remote_or_hybrid` | bool | scope |
| `search_locations` | list[str] | scope |
| `greenhouse_boards` | list[str] | scope |
| `freshness_days` | int | `get_freshness_days()` |
| `enabled_sources` | {slug: bool} | `scraper.<slug>.enabled` config |
| `monitoring_gates` | {provider: bool} | `monitoring.ats.<provider>.enabled` config |
| `monitored_targets` | list[{company_id, name, ats_provider, ats_identifier}] | `get_monitored_companies()` |
| `board_slugs` | {lever: [..], workable: [..], greenhouse_watching: [..]} | `get_watching_companies_by_method()` |

No profile, no user-space field.

### Ingestion batch (`POST /v1/ingest/batch`)

Request — a list of postings (each the same shape as `JobCreate` from spec 044):
`{source, title, company, location, url, description}` (+ optional `monitored_company_id`).

Response:

```json
{ "new": 12, "existing": 3, "dropped_engaged": 1,
  "outcomes": [{"job_id": "...", "result": "new|existing|dropped_engaged"}] }
```

## State transitions

- **Source auto-disable** (during a run): reported via `POST /v1/ingest/events` →
  `scraper.<slug>.enabled=false` + `scraper.<slug>.disabled_reason`; the source is
  skipped on the next plan. No new states — mirrors `BaseScraper.disable()` today.
- **Job lifecycle**: unchanged — batch writes `save_unscored` (new → `new` status) or
  `touch_many` (existing → `last_seen`), and drops engaged matches with a `last_seen`
  touch. Idempotent: the same batch twice creates nothing new (id is deterministic).
