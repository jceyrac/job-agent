# Data Model — spec 041 (API v1 read-only)

One new table (`api_tokens`, user space), four new read-only `JobStorage`
methods (`get_catalogue_jobs`, `get_catalogue_job`, `get_company_facts`,
`get_user`), and the request/response envelopes. No existing table changes.

---

## 1. `api_tokens` — scoped bearer tokens (FR-004, FR-013)

User-space table created by an idempotent migration (`api_tokens`), recorded in
`migrations`. The raw token is never stored — only its SHA-256 digest.

```sql
CREATE TABLE api_tokens (
  id           TEXT PRIMARY KEY,           -- uuid4().hex (32 chars)
  user_id      TEXT NOT NULL,              -- owner (user space)
  name         TEXT NOT NULL,              -- operator label, e.g. "web-front"
  token_hash   TEXT NOT NULL UNIQUE,       -- sha256 hex of the raw token
  scopes       TEXT NOT NULL,              -- JSON list, e.g. ["read:catalogue"]
  created_at   TEXT NOT NULL,              -- ISO 8601
  last_used_at TEXT,                       -- throttle: ≤1 write/min/token
  revoked_at   TEXT,                       -- set → 401 immediately
  expires_at   TEXT                        -- FR-013; NULL = no expiry
);
CREATE INDEX idx_api_tokens_user ON api_tokens(user_id);
```

| Column | Type | Rules |
|--------|------|-------|
| `id` | text | `uuid4().hex`; the id used by `list`/`revoke`. |
| `user_id` | text | owner; the API binds `JobStorage(user_id=…)` to it. |
| `name` | text | operator label; no secret. |
| `token_hash` | text | `sha256(raw).hexdigest()`; `UNIQUE`; never returned by `list`. |
| `scopes` | text | JSON list of scope strings. v1 scopes: `read:catalogue`, `read:me`. |
| `created_at`/`last_used_at`/`revoked_at`/`expires_at` | text | ISO 8601 strings; `last_used_at` throttled to once/minute/token. |

**Scope semantics** (US2-6): `read:catalogue` grants the catalogue routes;
`read:me` grants the token user's space routes. A token may hold both.

---

## 2. Request/response envelopes

**List envelope** (US3-1) — every list endpoint returns:

```json
{"items": [ … ], "total": 123, "limit": 100, "offset": 0}
```

- `limit` int, default 100, clamped to `[1, 500]`; `offset` int, default 0.
- `items` is the array of the endpoint's resource model; `total` is the count
  **after** API-side filters and **before** slicing (so clients can page).

**Error envelope** (FR-015) — every non-2xx response is:

```json
{"error": "not_found", "message": "Job not found"}
```

`error` is a stable machine code (`unauthorized`, `forbidden`, `not_found`,
`validation_error`); `message` is short, human-readable, and never contains a
token, an internal path, or a stack trace.

**Auth** (US2-3): missing/unknown/revoked/expired token → `401`; valid token
lacking the route's scope → `403`; resource of another user (or absent) → `404`
(never `403` — no existence disclosure).

---

## 3. New read-only `JobStorage` methods (shared-catalogue facts)

These are the only persistence additions beyond `api_tokens`. They return shared
catalogue facts only — **no** `score`, `tracked_status`, `status`, `notes`,
`monitored`, or any user-space aggregate. Keys are verbatim, mirroring the SELECT
columns (with the same `COALESCE` fallbacks the existing methods use).

### 3.1 `get_catalogue_jobs(*, company_id=None, source=None, posted_after=None) -> list[dict]`

WHERE filters (all optional, AND-ed): `company_id` (int), `source` (exact),
`posted_after` (ISO date; keeps `j.posted_date >= posted_after`, drops rows with
NULL `posted_date`). Ordered `j.last_seen DESC`.

Keys (verbatim): `id`, `title`, `company`, `company_id`, `url`, `source`,
`location`, `base_location`, `posted_date`, `description`, `first_seen`,
`last_seen`, `extracted_at`, `extracted_by`, `monitored_company_id`,
`canonical_url`, `norm_title`, `norm_company`, `summary`, `work_mode`,
`geo_zone`, `company_size`, `contract_type`, `company_country`,
`industry_sector`, `language_required`, `country_code`.

### 3.2 `get_catalogue_job(job_id) -> dict | None`

Same keys as 3.1 for a single row; `None` when the job does not exist.

### 3.3 `get_company_facts(company_id) -> dict | None`

Shared company facts only (no `status`/`notes`/`monitored` user-space coalesces,
no `job_count`/`contact_count`/`interaction_count`/`last_interaction_at`
user-space aggregates).

Keys (verbatim): `id`, `name`, `website`, `careers_url`, `company_country`,
`industry_sector`, `company_size`, `ats_provider`, `ats_identifier`,
`scraper_id`, `first_seen_at`, `last_seen_at`, `summary`, `monitoring_status`,
`scraping_method`, `research_notes`, `research_confidence`, `researched_at`,
`x_handle`.

### 3.4 `get_user() -> dict | None`

The bound user's `users` row for `self.user_id`. Keys: `id`, `display_name`,
`active_profile_id`, `onboarding_completed_at`.

### 3.5 `get_user_scores_for_job(job_id) -> list[dict]`

The bound user's scores for a single job — the user-scoped counterpart to the
existing cross-user `get_scores_for_job`. Filters `job_scores` to the user's
profiles via `search_profiles.user_id = self.user_id`, newest first.

Keys (verbatim, same as `get_scores_for_job`): `profile_id`, `score`, `reason`,
`scored_by`, `scored_at`.

---

## 4. Response model ↔ `JobStorage` method map

Full per-endpoint field lists live in `contracts/endpoints_*.md`; this is the
source-of-truth mapping (each pydantic model mirrors exactly the named method's
dict keys, `extra="forbid"`).

| Endpoint | Source method(s) | Scope |
|---|---|---|
| `GET /health` | — (static `{status, version}`) | none |
| `GET /v1/jobs` | `get_catalogue_jobs` | `read:catalogue` |
| `GET /v1/jobs/{job_id}` | `get_catalogue_job` | `read:catalogue` |
| `GET /v1/companies/{company_id}` | `get_company_facts` | `read:catalogue` |
| `GET /v1/me` | `get_user` + `get_active_profile_id` + `get_all_profiles` | `read:me` |
| `GET /v1/me/feed` | `get_all_for_tracker(profile_id, exclude_archived)` + API-side `status`/`min_score`/pagination | `read:me` |
| `GET /v1/me/jobs/{job_id}` | `get_job_for_prepare` + `get_user_scores_for_job` + `get_lifecycle_summary` + `get_lifecycle_events` + `get_application` | `read:me` |
| `GET /v1/me/stats` | `get_stats` | `read:me` |
| `GET /v1/me/dashboard` | `get_dashboard_data` | `read:me` |
| `GET /v1/me/applications` | `get_all_applications` | `read:me` |
| `GET /v1/me/companies` | `get_companies` | `read:me` |
| `GET /v1/me/companies/{company_id}` | `get_company_by_id` + `get_company_contacts` + `get_company_interactions` + `get_company_relationship_summary` | `read:me` |
| `GET /v1/me/contacts` | `get_all_contacts` | `read:me` |
| `GET /v1/me/contacts/{contact_id}` | `get_contact` + `get_contact_interactions` | `read:me` |

**Isolation (FR-010)**: `read:me` responses are the token user's view; the only
shared input is the catalogue rows themselves (jobs, companies). Scores are
user-scoped in `JobStorage` (`get_user_scores_for_job`), never filtered in the API
(spec 039 rule). `get_scores_for_job`, `get_last_run`, `get_digest`,
`get_jobs_for_scoring`, and every write method are **not** exposed in v1.

---

## 5. Rules & invariants

- `api/` performs **no SQL**; it calls only the allow-listed `JobStorage` read
  methods plus the two auth accessors (guard-tested, FR-009).
- One `JobStorage` per request is created in `api/deps.py` only (unbound for auth,
  bound to the token user for data) — see `research.md` §4.
- Response models use `extra="forbid"`; a new internal column that leaks into a
  method's dict fails the contract test rather than shipping (US3-4).
- Dates/enums stay as the strings `JobStorage` returns (no reformatting in v1).
