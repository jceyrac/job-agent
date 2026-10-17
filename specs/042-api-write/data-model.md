# Data model — API v1 write endpoints (spec 042)

No new persisted entities: the API writes the same tables the tracker writes,
via the same `JobStorage` methods. This file records the **request/response**
shapes and the **scope map** only.

## Scopes

| Scope | Grants | Routes |
|---|---|---|
| `write:me` | user-space writes | all `/v1/me/*` write routes (US1–US3) |
| `write:catalogue` | shared company-fact writes | `PATCH /v1/companies/{company_id}` |

`read:me` / `read:catalogue` (spec 041) are unchanged. `API_TOKEN_SCOPES` gains
the two write scopes; a token may combine read + write scopes.

## Enums (pydantic)

| Enum | Values | Source |
|---|---|---|
| `JobStatus` | `new, queued, ready, applied, interviewing, offer, rejected, withdrawn, archived, expired` | `JobStorage.VALID_STATUSES` |
| `LifecycleEventType` | `application_submitted, interview_invited, interview, decision_received, withdrawn` (the events `record_lifecycle_event` accepts) | `tracker_views/job_detail.py` event set |
| `InteractionType` | `discovered_on_posting, discovered_manual, outreach_sent, reply_received, intro_received, call, meeting, application_submitted, interview_invited, interview, decision_received, withdrawn, note` | `INTERACTION_TYPES` |
| `InteractionDirection` | `inbound, outbound, none` | `INTERACTION_DIRECTIONS` |
| `InteractionOutcome` | `positive, negative` | `INTERACTION_OUTCOMES` |
| `CompanyStatus` | `prospect, watching, active_outreach, engaged, dormant, passed_by_me, declined_by_them, blacklisted` | `COMPANY_STATUSES` |

## Request models (all `extra="forbid"`)

### US1 — jobs & lifecycle

`StatusUpdate` — `{ status: JobStatus, occurred_at: str | None = None, notes: str
| None = None }` (`occurred_at` required when `status` is a lifecycle status
`applied`/`interviewing`/`offer`/`rejected`/`withdrawn` — enforced by a pydantic
`model_validator`).

`LifecycleEventCreate` — `{ event: LifecycleEventType, occurred_at: str
(required), new_status: JobStatus (required), outcome: InteractionOutcome |
None = None, notes: str | None = None }`

`LifecycleEventUpdate` — `{ occurred_at: str | None = None, notes: str | None =
None }` (at least one field present — enforced by pydantic `model_validator`).

### US2 — companies, contacts, interactions

`CompanyRelationshipUpdate` — `{ status: CompanyStatus | None = None, notes: str
| None = None, monitored: bool | None = None }` (at least one field).

`ContactCreate` — the `upsert_contact` inputs: `company_id: int`, then optional
`linkedin_url, email, first_name, last_name, full_name, role_title, role_family,
seniority, email_status, email_confidence, x_handle, telegram_handle,
github_handle, phone, is_unverified, notes`.

`ContactUpdate` — the `_ALLOWED_CONTACT_FIELDS` subset:
`is_unverified, last_verified_at, is_current, notes` (at least one).

`InteractionCreate` — `{ type: InteractionType, contact_id: int | None = None,
job_id: str | None = None, direction: InteractionDirection = "none", outcome:
InteractionOutcome | None = None, subject: str | None = None, body_excerpt: str
| None = None, occurred_at: str | None = None, follow_up_due_at: str | None =
None }` (for the contact-scoped route, `contact_id` comes from the path; for the
company-scoped route, it is `None`).

### US3 — profile

`ActiveProfileUpdate` — `{ profile_id: str }`

`ProfileUpdate` — the `SearchProfile` criteria object (`upsert_profile` consumes
a `SearchProfile.to_criteria_dict()`); shape mirrors Settings validation.

### US4 — catalogue company facts

`CompanyFactUpdate` — the fact fields only (see research R4): `name, website,
company_country, industry_sector, company_size, x_handle, careers_url,
ats_provider, ats_identifier, scraping_method, research_notes` (at least one).
`notes`, `status`, `monitored` are **absent** — rejected as `422` by
`extra="forbid"`.

## Response models

Responses reuse the read serializers (`api/serializers.py`) so a write returns
the same shape a subsequent GET would (research R8). Deletes return `204`.

## Invariants

- No write route constructs `JobStorage` — it receives the bound storage from
  `get_user_db` (Constitution X / spec 041 guard).
- `occurred_at` on `LifecycleEventCreate` is **required** and passed through
  verbatim; the API never defaults it to "now".
- Lifecycle edits/deletes always reconcile the tracking status afterward (R3).
- A `write:catalogue` route never writes `user_companies` (R4).
