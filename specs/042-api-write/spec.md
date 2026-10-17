# Feature Specification: API v1 — write endpoints for the user space (roadmap step 6, part 1 of 2)

**Feature Branch**: `042-api-write`

**Created**: 2026-10-17

**Status**: Clarified 2026-10-17 — ready for `/speckit.plan`

**Input**: Roadmap `docs/roadmap-api.md`, step 6 ("API écriture + tâches", which also absorbs step 2b). Proposed split: **spec 042 = synchronous write endpoints** (this spec); **spec 043 = task queue + worker + SSE progress, tracker buttons moved onto it (step 2b)** — see Q1. Constitution: Architecture Constraints (API = future single writer; no LLM call or long work inside an HTTP request), VII (least privilege, scoped tokens), X (user space vs catalogue), XI (progressive migration). "Expose, over the API, every user-space write the tracker performs today, with the same effects, so that the future front and agents can act — not only read. The tracker is not touched."

---

## Clarifications

### Session 2026-10-17

- Q1: Split of step 6? → A: **Two specs.** 042 = synchronous write endpoints (this spec). 043 = task queue + worker + SSE progress + tracker buttons moved onto the queue (former step 2b). Step 6b (agents through the API) follows 043.
- Q2: Profile editing in 042? → A: **Included** (US3: `PUT /v1/me/active-profile`, `PUT /v1/me/profiles/{id}`).

---

## Context (verified against source at `5f2891c`, 2026-10-17)

- **API v1 read-only is live** (spec 041): `api/` (FastAPI, routers `catalogue` and `me`), scoped bearer tokens (`read:catalogue`, `read:me`, hashed, revocable, optional expiry), `HTTPBearer` security scheme, 401/403/404 semantics, user always taken from the token, OpenAPI snapshot, API isolation suite, read-method allow-list guard, `/health` returns the image's git SHA, HTTPS on the tailnet under `/api`.
- **Write actions the tracker performs today** (all through `JobStorage`):

| Tracker action | Storage method(s) | Space |
|---|---|---|
| Change a job's status (non-lifecycle: queue, expired, archived, …) | `set_status` | user |
| Change a job's status (lifecycle: applied, interviewing, offer, rejected, withdrawn — with a manual date) | `record_lifecycle_event` (status→event mapping per `_confirm_lifecycle`) | user |
| Lifecycle events (applied, interview, offer, rejected… with a manual date) | `record_lifecycle_event`, `update_lifecycle_event`, `delete_lifecycle_event`, `reconcile_lifecycle_status` | user |
| Company relationship: status (incl. blacklist), notes, watch | `set_company_status`, `set_company_monitored` | user (`user_companies`) |
| Create a contact / edit a contact | `upsert_contact`, `update_contact_fields` | user |
| Log an interaction | `log_interaction` | user |
| Active profile / profile criteria | `set_active_profile_id`, `upsert_profile` | user |
| Edit company facts (website, careers URL, size, sector…) | `update_company_fields` | **catalogue** (shared facts) |

- No `delete` exists today for contacts or interactions; the API does not add new behaviour (non-goal).
- Lifecycle dates are set **manually** (often batched at month end), never from the request time — the API must take `occurred_at` from the client.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Act on my jobs (Priority: P1)

As a client of the API, I want to change a job's status and manage its lifecycle events, with exactly the same effects as the tracker, so that the future front and agents can track applications.

**Independent Test**: For each endpoint, calling the API and calling the same `JobStorage` method on a copy of the same DB produce identical rows in `job_tracking`, `status_history`, `interactions` and lifecycle tables (contract tests).

**Acceptance Scenarios**:

1. **Given** `PUT /v1/me/jobs/{job_id}/status` `{status, occurred_at?, notes?}`, **Then** a lifecycle status (`applied`/`interviewing`/`offer`/`rejected`/`withdrawn`) requires `occurred_at` and calls `record_lifecycle_event` (mapped status→event as the tracker's `_confirm_lifecycle`), never defaulting the date to "now"; a non-lifecycle status calls `set_status` unchanged; and it returns the updated job tracking; an invalid status → `422` with the allowed values.
2. **Given** `POST /v1/me/jobs/{job_id}/events` `{event, occurred_at, new_status, outcome?, notes?}`, `PATCH /v1/me/jobs/{job_id}/events/{event_id}`, `DELETE /v1/me/jobs/{job_id}/events/{event_id}`, **Then** they call `record_lifecycle_event` / `update_lifecycle_event` / `delete_lifecycle_event` with the same arguments and effects, followed by `reconcile_lifecycle_status(job_id)` on edit/delete; `occurred_at` is required on create and is never defaulted to "now".
3. **Given** a job or event not visible to the token's user, **Then** `404`; an `event_id` that exists but belongs to another job (or another user) also → `404`, and reconciliation never runs on the wrong job (tested explicitly).

---

### User Story 2 — Manage my companies, contacts and interactions (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `PATCH /v1/me/companies/{company_id}` `{status?, notes?, monitored?}`, **Then** it calls `set_company_status` / `set_company_monitored` (user space only — never `companies` facts); blacklisting hides the company from **this user's** feed only (spec 039 behaviour).
2. **Given** `POST /v1/me/contacts` and `PATCH /v1/me/contacts/{contact_id}`, **Then** they call `upsert_contact` / `update_contact_fields`; `POST` is an upsert (merge on the user's `linkedin_url`, same as the tracker) and always returns the contact id — `201` only if `upsert_contact` reports a create vs merge, otherwise `200` (no storage change).
3. **Given** `POST /v1/me/contacts/{contact_id}/interactions` (and `POST /v1/me/companies/{company_id}/interactions` if the tracker logs company-level interactions), **Then** they call `log_interaction` with the same fields.
4. **Given** another user's contact or company relationship, **Then** `404`.

---

### User Story 3 — Manage my profile (Priority: P2)

**Acceptance Scenarios**:

1. **Given** `PUT /v1/me/active-profile` `{profile_id}`, **Then** `set_active_profile_id` is called after checking the profile belongs to the user (else `404`).
2. **Given** `PUT /v1/me/profiles/{profile_id}` with the profile criteria, **Then** `upsert_profile` is called with the same validation as Settings; no rescoring is triggered (scoring is a task — spec 043).

---

### User Story 4 — Least privilege and safety for writes (Priority: P1)

**Acceptance Scenarios**:

1. **Given** the new scopes `write:me` (user-space writes) and `write:catalogue` (shared company facts), **Then** read-only tokens get `403` on every write route; a `write:me` token gets `403` on catalogue writes.
2. **Given** `PATCH /v1/companies/{company_id}` (scope `write:catalogue`), **Then** it calls `update_company_fields` with an allow-list of editable fact fields; user-relationship fields are rejected (`422`).
3. **Given** any write, **Then** the request body is validated by an explicit pydantic model (`extra="forbid"`), limited to 64 KB (`413` beyond), and the response is the updated resource or `204` for deletes.
4. **Given** any write, **Then** one structured log line records token id, user id, route, resource id and outcome — never the body, never token material.
5. **Given** the API guard test, **Then** `api/` may call only the allow-listed read **and write** methods of this spec; ingestion, scoring, extraction, run logging, seeding and platform-config writes (`save_unscored`, `save_scored`, `update_job_extraction`, `log_run`, `set_ingestion_scope`, `upsert_company*`, `set_config`, …) stay forbidden.
6. **Given** the API isolation suite, **Then** it is extended to every write route: user A's token can never modify user B's data (A's write on B's resource → `404`, B's data unchanged).

---

### User Story 5 — Released through the standard flow (Priority: P1)

**Acceptance Scenarios**:

1. **Given** staging on the exact SHA, **Then** with a staging `write:me` token, each write route is exercised once from the Mac and its effect is visible on the staging tracker (status change, lifecycle event with a past date, company blacklist then restore, contact edit, interaction); a write made in the staging tracker is visible through the API; tracker parity vs prod image identical before the API writes.
2. **Given** the deploy, **Then** SHA = staged SHA (`/api/health` version), health OK, tracker and cron unaffected; production write tokens are created only when a client needs them (none by default).

---

### Edge Cases

- Tracker and API writing the same row at the same time: last write wins (SQLite WAL, short transactions, `busy_timeout`); no optimistic locking in v1 (single user).
- Status change via the API must produce the same auto-logged interaction and lifecycle reconciliation as the tracker — no API-specific shortcut.
- Lifecycle event dated in the past or future: accepted as given (manual dates are the rule).
- Duplicate contact (`linkedin_url` already used by the same user) → upsert merges and returns the existing contact id (no `409`).
- Deleting a lifecycle event that drives the current status → the same reconciliation as the tracker.
- CSRF does not apply (bearer tokens, no cookies); it will when cookie sessions arrive (recorded in `docs/api-exposure.md`).

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Write routes of US1–US3 under `/v1/me/...`, scope `write:me`; catalogue fact edits under `/v1/companies/{id}`, scope `write:catalogue`.
- **FR-002**: Each route calls the same `JobStorage` method(s) as the tracker, with the same arguments; no new storage behaviour, no SQL in `api/`.
- **FR-003**: Pydantic request models (`extra="forbid"`), enums for statuses/events, 64 KB body limit, `422` / `404` / `413` semantics, updated resource or `204` in responses.
- **FR-004**: Contract tests per route: API call vs direct `JobStorage` call on copies of the same DB → identical resulting rows.
- **FR-005**: Guard test: read + write allow-list; forbidden list of pipeline/platform writes; still no `sqlite3`, `JobStorage` always bound to the token user.
- **FR-006**: API isolation suite extended to all write routes.
- **FR-007**: Structured write log (no body, no token); token CLI accepts the new scopes.
- **FR-008**: OpenAPI snapshot updated deliberately; `docs/api-exposure.md` gains the CSRF note.
- **FR-009**: Standard release flow; `CLAUDE.md` API section updated.

### Non-goals

- Task queue, worker, SSE, scoring/extraction/scrape/prepare triggers, tracker buttons moving onto the queue (spec 043, includes step 2b).
- Agents through the API (step 6b).
- New behaviours (deleting contacts or interactions, bulk operations).
- Platform administration over the API (ingestion scope, config) — CLI only.
- Changes to the tracker.

---

## Success Criteria *(mandatory)*

- **SC-001**: Every write route has a contract test proving identical effects to the tracker path.
- **SC-002**: Scope tests: read-only tokens → 403 on writes; `write:me` → 403 on catalogue writes; isolation suite green on writes.
- **SC-003**: Guard test green and failing on a forbidden pipeline/platform write call.
- **SC-004**: Staging round-trip: API writes visible in the staging tracker and vice versa; parity identical before writes.
- **SC-005**: Deploy: SHA = staged SHA via `/api/health`; tracker and next cron unaffected.

