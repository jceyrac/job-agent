# Contract — `read:me` endpoints (US3, FR-007)

All responses are explicit pydantic models with `extra="forbid"`. Every route is
`read:me`, bound to the token user (`JobStorage(user_id=current_user.user_id)`).
List endpoints use the `{items, total, limit, offset}` envelope; `limit` default
100 max 500, `offset` default 0, pagination applied API-side.

## `GET /v1/me`

Source: `get_user()` + `get_active_profile_id()` + `get_all_profiles()`.

```json
{
  "user": {"id": "…", "display_name": "…", "active_profile_id": "…", "onboarding_completed_at": "…"},
  "active_profile_id": "…",
  "profiles": [{"id": "…", "name": "…", "criteria": "…"}, …]
}
```

`user.active_profile_id` and top-level `active_profile_id` agree (same value).

## `GET /v1/me/feed`

Query: `profile_id` (default = the user's active profile), `status` (optional),
`min_score` (optional int), `exclude_archived` (bool, default false),
`limit`, `offset`.

Source: `get_all_for_tracker(profile_id, exclude_archived)` (full list, no
`limit`), then API-side: filter by `status` (the derived `status` field,
including `"unscored"`) and `min_score` (exclude `score is None`); slice
`[offset:offset+limit]`; `total` = filtered count.

Item keys **verbatim** (`get_all_for_tracker`):

```
id, company_id, title, company, url, source, location, base_location,
posted_date, description, first_seen, last_seen, extracted_at, extracted_by,
monitored_company_id, summary, work_mode, geo_zone, company_size, contract_type,
company_country, industry_sector, language_required, score, reason, scored_by,
country_code, tracked_status, status, notes, status_changed_at, score_count
```

## `GET /v1/me/jobs/{job_id}`

Sources composed (all user-bound):

```json
{
  "job":         { …get_job_for_prepare(job_id)… },
  "scores":      [ …get_user_scores_for_job(job_id)… ],
  "lifecycle":   { …get_lifecycle_summary(job_id)… },
  "events":      [ …get_lifecycle_events(job_id)… ],
  "application": { …get_application(job_id)… } | null
}
```

- `job` keys verbatim (`get_job_for_prepare`): `id, title, company, company_id,
  url, source, location, base_location, posted_date, description, first_seen,
  last_seen, extracted_at, extracted_by, monitored_company_id, canonical_url,
  norm_title, norm_company, summary, work_mode, geo_zone, company_size,
  contract_type, company_country, industry_sector, language_required,
  country_code, status`.
- `scores` item keys verbatim (`get_user_scores_for_job`): `profile_id, score,
  reason, scored_by, scored_at` — the bound user's profiles' scores only
  (isolation in `JobStorage`, spec 039 rule).
- `lifecycle` keys verbatim (`get_lifecycle_summary`): `application_date, stage,
  label, reason, interviews_held, round, next_interview, decision_outcome,
  decision_date, withdrawn_date, last_event_date, orp_result`.
- `events` item keys verbatim (`get_lifecycle_events`): `id, type, direction,
  outcome, occurred_at, created_at, notes, source, source_ref`.
- `application` keys verbatim (`get_application`): `user_id, job_id, profile_id,
  analysis, cover_letter, cv_bullets_selected, company_research,
  screening_answers, language, prepared_by, prepared_at, created_at` (or `null`).
- Unknown job → `404` (a job is shared catalogue; a known job with no user
  tracking still returns its facts + the user's empty space).

## `GET /v1/me/stats`

Source: `get_stats()`. Keys verbatim: `total, scored, hot, solid, by_status`.

## `GET /v1/me/dashboard`

Source: `get_dashboard_data()`. Keys verbatim: `follow_ups_due_today,
recent_inbound, unverified_contacts_count, stale_active_outreach`.

## `GET /v1/me/applications`

Source: `get_all_applications()`. Envelope of items with the `get_application`
keys (full `job_applications` column list).

## `GET /v1/me/companies`

Source: `get_companies()` (default filters). Item keys verbatim: `id, name,
website, status, notes, company_country, industry_sector, company_size,
first_seen_at, last_seen_at, summary, ats_provider, ats_identifier, scraper_id,
monitored, monitoring_status, scraping_method, research_notes, research_confidence,
researched_at, job_count, contact_count, interaction_count, last_interaction_at`.

## `GET /v1/me/companies/{company_id}`

```json
{
  "company":      { …get_company_by_id(company_id)… },
  "relationship": { …get_company_relationship_summary(company_id)… },
  "contacts":     [ …get_company_contacts(company_id)… ],
  "interactions": [ …get_company_interactions(company_id)… ]
}
```

- `company` keys verbatim (`get_company_by_id`): `id, name, website, status, notes,
  company_country, industry_sector, company_size, careers_url, ats_provider,
  ats_identifier, scraper_id, monitored, first_seen_at, last_seen_at, summary,
  monitoring_status, scraping_method, research_notes, research_confidence,
  researched_at, x_handle, job_count, contact_count, interaction_count,
  last_interaction_at`.
- `relationship` keys verbatim: `company_id, total_contacts, by_status,
  last_interaction_at`.
- `contacts` item keys verbatim (`get_company_contacts`, the `contacts` columns):
  `id, user_id, company_id, first_name, last_name, full_name, role_title,
  role_family, seniority, email, email_status, email_confidence, linkedin_url,
  x_handle, telegram_handle, github_handle, phone, is_current, is_unverified,
  last_verified_at, notes, first_seen_at, last_seen_at, created_at`.
- `interactions` item keys verbatim (the `interactions` columns): `id, user_id,
  company_id, contact_id, job_id, type, direction, outcome, subject, body_excerpt,
  occurred_at, follow_up_due_at, created_at, notes, source, source_ref`.
- Unknown company → `404`.

## `GET /v1/me/contacts`

Source: `get_all_contacts()` (default filters). Item keys verbatim: `id,
first_name, last_name, full_name, role_title, role_family, seniority, email,
email_status, email_confidence, linkedin_url, x_handle, telegram_handle,
github_handle, phone, is_current, is_unverified, notes, first_seen_at, last_seen_at,
created_at, company_id, company_name, company_status, last_interaction_at`.

## `GET /v1/me/contacts/{contact_id}`

```json
{
  "contact":      { …get_contact(contact_id)… },
  "interactions": [ …get_contact_interactions(contact_id)… ]
}
```

- `contact` keys verbatim (the `contacts` columns, as above).
- `interactions` item keys verbatim (the `interactions` columns, as above).
- Contact absent **or** not the user's → `404` (never `403`, US3-2).

## Tests (US3, FR-008, FR-010)

- Contract test per endpoint: in-memory DB seeded for one user; assert each
  payload equals its `JobStorage` source(s) field-for-field.
- Isolation test: two users + two tokens; token B on A's `contact_id`/company →
  `404`; B's feed/stats/dashboard/applications show only B's data; `/v1/me/jobs`
  scores are filtered to B's profile.
- `read:me` routes reject a `read:catalogue`-only token with `403`.
