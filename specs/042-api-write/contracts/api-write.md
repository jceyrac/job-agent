# Write-route contracts (spec 042)

Each route: pre-check → write (same `JobStorage` method(s) as the tracker) →
response. Errors follow research R5. `write:*` scopes per the map in
`data-model.md`.

## US1 — jobs & lifecycle (scope `write:me`)

### `PUT /v1/me/jobs/{job_id}/status`
- **Body**: `StatusUpdate { status: JobStatus, occurred_at?: str, notes?: str }`
- **Pre-check**: `get_job_for_prepare(job_id)` → `None` ⇒ `404`
- **Write**: mirror the tracker's sequence (FR-002) — a lifecycle status
  (`applied`/`interviewing`/`offer`/`rejected`/`withdrawn`) routes through
  `record_lifecycle_event` with the client's `occurred_at` (required for those
  statuses; never defaulted to "now"), mapped status→event as
  `tracker_views/job_helpers.py` `_confirm_lifecycle` does:
  `applied`→`application_submitted`, `interviewing`→`interview`,
  `offer`→`decision_received`(+`positive`), `rejected`→`decision_received`(+`negative`),
  `withdrawn`→`withdrawn`. A non-lifecycle status (`new`/`queued`/`ready`/
  `expired`/`archived`) calls `set_status(job_id, status, notes)` unchanged.
- **Response**: `200` → `get_job_for_prepare(job_id)` (new `status` visible)
- **Errors**: `404` unknown job · `422` invalid status · `422` missing
  `occurred_at` on a lifecycle status

### `POST /v1/me/jobs/{job_id}/events`
- **Body**: `LifecycleEventCreate { event, occurred_at (required), new_status
  (required), outcome?, notes? }`
- **Pre-check**: `get_job_for_prepare(job_id)` → `None` ⇒ `404`
- **Write**: `record_lifecycle_event(job_id, event=…, occurred_at=…,
  new_status=…, outcome=…, notes=…)` — `occurred_at` never defaulted to "now"
- **Response**: `200` → `get_lifecycle_events(job_id)`
- **Errors**: `404` · `422` invalid event/status/outcome · `422` missing
  `occurred_at`

### `PATCH /v1/me/jobs/{job_id}/events/{event_id}`
- **Body**: `LifecycleEventUpdate { occurred_at?, notes? }` (≥1 field)
- **Pre-check**: `event_id` ∈ `get_lifecycle_events(job_id)` else `404` — an
  event that exists but belongs to another job (or another user) is simply not in
  this job's list, so it → `404` and reconciliation never runs on the wrong job
- **Write**: `update_lifecycle_event(event_id, occurred_at=…, notes=…)` **then**
  `reconcile_lifecycle_status(job_id)`
- **Response**: `200` → `get_lifecycle_events(job_id)`
- **Errors**: `404` foreign/unknown event (incl. one that exists but belongs to
  another job/user) · `422` bad args

### `DELETE /v1/me/jobs/{job_id}/events/{event_id}`
- **Body**: none
- **Pre-check**: `event_id` ∈ `get_lifecycle_events(job_id)` else `404` (an event
  that belongs to another job/user → `404`; reconciliation never runs on the
  wrong job)
- **Write**: `delete_lifecycle_event(event_id)` **then**
  `reconcile_lifecycle_status(job_id)`
- **Response**: `204`
- **Errors**: `404` (unknown, or exists but belongs to another job/user)

## US2 — companies, contacts, interactions (scope `write:me`)

### `PATCH /v1/me/companies/{company_id}`
- **Body**: `CompanyRelationshipUpdate { status?, notes?, monitored? }` (≥1)
- **Pre-check**: `get_company_by_id(company_id)` → `None` ⇒ `404`
- **Write**: `set_company_status(company_id, status)` for `status`;
  `update_company_fields(company_id, {"notes": notes})` for `notes` (writes
  `user_companies.notes` — the persistent relationship note, not the history
  note); `set_company_monitored(company_id, monitored)` for `monitored`.
- **Response**: `200` → `get_company_by_id` + `get_company_relationship_summary`
- **Errors**: `404` · `422` invalid status
- **Note**: blacklist hides the company from *this user's* feed only (read-side,
  already handled — no API work).

### `POST /v1/me/contacts`
- **Body**: `ContactCreate` (company_id + optional fields)
- **Pre-check**: `get_company_by_id(company_id)` → `None` ⇒ `404`
- **Write**: `upsert_contact(company_id=…, …)` → returns the contact id (merge —
  no 409, see research R7)
- **Response**: `200` → `get_contact(new_id)` (upsert returns only the id — no
  created/merged signal, so always `200`)
- **Errors**: `404` unknown company · `422` invalid role_family/seniority/email_status

### `PATCH /v1/me/contacts/{contact_id}`
- **Body**: `ContactUpdate { is_unverified?, last_verified_at?, is_current?,
  notes? }` (≥1)
- **Pre-check**: `get_contact(contact_id)` → `None` ⇒ `404`
- **Write**: `update_contact_fields(contact_id, fields)`
- **Response**: `200` → `get_contact(contact_id)`
- **Errors**: `404` · `422` unknown field

### `POST /v1/me/contacts/{contact_id}/interactions`
- **Body**: `InteractionCreate { type, job_id?, direction?, outcome?, subject?,
  body_excerpt?, occurred_at?, follow_up_due_at? }`
- **Pre-check**: `get_contact(contact_id)` → `None` ⇒ `404`
- **Write**: `log_interaction(company_id=<contact.company_id>,
  contact_id=contact_id, type=…, …)`
- **Response**: `201`
- **Errors**: `404` · `422` invalid type/direction/outcome

### `POST /v1/me/companies/{company_id}/interactions`
- **Body**: `InteractionCreate` (company-level: `contact_id` optional)
- **Pre-check**: `get_company_by_id(company_id)` → `None` ⇒ `404`
- **Write**: `log_interaction(company_id=company_id, contact_id=None, type=…, …)`
- **Response**: `201`
- **Errors**: `404` · `422`

## US3 — profile (scope `write:me`)

### `PUT /v1/me/active-profile`
- **Body**: `ActiveProfileUpdate { profile_id }`
- **Pre-check**: `get_profile(profile_id)` — foreign ⇒ `ProfileNotFoundError`
  (`404`), missing ⇒ `None` (`404`)
- **Write**: `set_active_profile_id(profile_id)`
- **Response**: `204`
- **Errors**: `404`

### `PUT /v1/me/profiles/{profile_id}`
- **Body**: `ProfileUpdate` (profile criteria)
- **Pre-check**: `get_profile(profile_id)` (as above) for an existing profile;
  create-with-new-id allowed only if the id is the user's (ownership enforced by
  `upsert_profile`)
- **Write**: `upsert_profile(profile)` — **no rescoring** (scoring is spec 043)
- **Response**: `200` → `get_profile(profile_id)`
- **Errors**: `404` · `422` invalid criteria

## US4 — catalogue company facts (scope `write:catalogue`)

### `PATCH /v1/companies/{company_id}`
- **Body**: `CompanyFactUpdate` (fact fields only — no `notes`/`status`/`monitored`)
- **Pre-check**: `get_company_facts(company_id)` → `None` ⇒ `404`
- **Write**: `update_company_fields(company_id, fields)`
- **Response**: `200` → `get_company_facts(company_id)`
- **Errors**: `404` · `422` (unknown/relationship field via `extra="forbid"`)
