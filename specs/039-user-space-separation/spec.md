# Feature Specification: Shared Catalogue vs User Space (roadmap step 3 — Phase B)

**Feature Branch**: `039-user-space-separation`

**Created**: 2026-10-12

**Status**: Clarified — ready for `/speckit.plan`

**Input**: Roadmap `docs/roadmap-api.md`, step 3; Constitution v2.0.x principles III (seam `user_id`) and X (shared catalogue / user space). "Ingestion depends on no one; the judgement on a job depends on each user. Every piece of data that expresses a user's decision or relationship must be owned by a user (`user_id`), never stored on a catalogue entity. Contacts are private per user. Multi-user is NOT built here — only the seam, with a single user row. The app must behave exactly as today."

---

## Clarifications

### Session 2026-10-13

- Q: `job_tracking` / `job_applications` primary keys — rebuild now or defer? → A: **Rebuild now**: rekey both tables to `(user_id, job_id)` via the standard table-rebuild procedure inside the migration transaction.
- Q: where do per-user settings `active_profile_id` / `onboarding_complete` live? → A: **Move to `users`** (`active_profile_id`, `onboarding_completed_at` columns); `config` keeps platform-only keys.
- Q: default user id format? → A: **Opaque UUID**, with `display_name` for humans.

## Why now

- The API (Phase C) exposes `/me/...` routes; they must be designed on the final ownership model, not rebuilt later.
- With one user, the migration is a backfill onto one row. With several users and real data, the same migration becomes a data-attribution problem.
- Since spec 037, every DB access goes through `JobStorage` (guard-tested): this step changes one module and its tests, not the UI.

---

## Context (verified against source at `4304f0e`, 2026-10-12)

### Tables and their target space

| Table | Today | Target |
|---|---|---|
| `jobs`, `runs`, `migrations` | catalogue | catalogue — unchanged |
| `companies` | facts **and** user decisions mixed | facts only (see split below) |
| `search_profiles` | no owner | + `user_id` (a profile belongs to a user) |
| `job_scores` | `PRIMARY KEY (job_id, profile_id)` | unchanged — a score is the user's view through their profile |
| `job_tracking` | `job_id TEXT PRIMARY KEY` | key `(user_id, job_id)` |
| `job_applications` | `job_id TEXT PRIMARY KEY` (+ `profile_id`) | key `(user_id, job_id)` |
| `status_history` | no owner | + `user_id` |
| `contacts` | no owner; `UNIQUE (linkedin_url)` | + `user_id`, private; unique `(user_id, linkedin_url)` |
| `interactions` | no owner | + `user_id` |
| `company_status_history` | no owner | + `user_id` (it is the history of the user's relationship status) |
| new `user_companies` | — | `(user_id, company_id)` → the user's relationship to a company |
| new `users` | — | one row (the current user) |
| `config` | global key/value | global, except user-level keys (open question Q2) |

### `companies`: facts vs user decisions

| Column(s) | Nature | Target |
|---|---|---|
| `name`, `name_normalized`, `website`, `careers_url`, `summary`, `company_country`, `industry_sector`, `company_size`, `enriched_*`, `ats_provider`, `ats_identifier`, `scraper_id`, `detected_at`, `x_handle`, `research_notes`, `research_confidence`, `researched_at`, `scraping_method`, `first_seen_at`, `last_seen_at` | facts about the company / platform capability | stay on `companies` |
| `status` (`prospect`, `active_outreach`, `blacklisted`, …) and `notes` | the user's relationship and judgement | → `user_companies` |
| `monitored` | the user's decision to watch the company | → `user_companies` |
| `monitoring_status` (`unmonitored`, `watch_pending`, `watching`, `watch_ready`, `watch_unsuitable`) | **mixes** the user's request to watch (`watch_pending`) with the platform's capability to monitor (`watch_ready` = a scraper exists, `watch_unsuitable` = no usable ATS) | split: the request → `user_companies`; the capability → stays on `companies` (renamed or re-valued in the plan) |

`blacklisted` is a user decision that currently filters the feed (`c.status != 'blacklisted'` in several queries) — it becomes a filter on the user's `user_companies` row.

Monitoring ingestion (spec VIII: "union of companies watched by users") reads `user_companies` across all users; with one user the set is identical to today.

### Surface in `JobStorage` (98 public/private methods)

About 45 methods read or write user-space tables, among them: `set_status`, `get_all_for_tracker`, `get_stats`, `get_dashboard_data`, `record_lifecycle_event`, `reconcile_lifecycle_status`, `save_application`, `save_prepared_application`, `get_all_applications`, `upsert_contact`, `update_contact_fields`, `get_all_contacts`, `log_interaction`, `get_company_relationship_summary`, `set_company_status`, `set_monitoring_status`, `get_companies`, `get_calibration_signals`, `purge_stale_jobs`. The plan inventories all of them.

### Notable behaviours to preserve or adapt

- `purge_stale_jobs` deletes a job and its user data unless it was "engaged" (non-`new` status or notes in `job_tracking`). With the seam, engagement must be checked **across all users**: a catalogue job is never purged while any user has engaged with it.
- `_auto_log_status_interaction` writes an interaction on status change → must carry the same `user_id`.
- The unique index `idx_interactions_source_ref` and `idx_contacts_linkedin` must become per-user.
- `JobStorage(":memory:")` is used by the test suite (in-memory DB): the seam must work there too.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Every user-space row is owned by a user (Priority: P1)

As the maintainer, I want every row that expresses a user's decision, relationship or content to carry `user_id`, with the existing data backfilled to the single current user, so that the API can be built on the final model.

**Independent Test**: After migration on a copy of the live DB: row counts per table unchanged; every user-space row has a non-null `user_id` equal to the current user; `user_companies` has exactly one row per company that had a non-default `status`, non-empty `notes` or `monitored = 1`; parity fingerprint identical.

**Acceptance Scenarios**:

1. **Given** an existing DB, **When** `JobStorage` opens it, **Then** the migration runs once (recorded in `migrations`), inside a single transaction, and is a no-op on the next open.
2. **Given** the migration, **Then** a `users` row exists for the current user, and `search_profiles`, `job_tracking`, `job_applications`, `status_history`, `contacts`, `interactions`, `company_status_history` rows all carry that `user_id`.
3. **Given** `companies`, **Then** `status`, `notes`, `monitored` and the user-request part of `monitoring_status` are copied to `user_companies` and no longer read from `companies` by any method (old columns kept, unused, until step 10 — expand/contract).
4. **Given** a failure in the middle of the migration, **Then** the transaction rolls back and the DB is left exactly as before.

---

### User Story 2 — `JobStorage` is bound to a user and never leaks across users (Priority: P1)

As the maintainer, I want `JobStorage` to act on behalf of one user, filtering every user-space read and setting `user_id` on every user-space write, and a test proving that one user never sees another's data.

**Independent Test**: An isolation test suite creates two users in an in-memory DB, writes data through every public user-space method as user A, and checks that every public read method called as user B returns none of it — and vice versa.

**Acceptance Scenarios**:

1. **Given** `JobStorage(db_path)` with no user argument, **Then** it binds to the default user (single-user behaviour unchanged for all current callers: tracker, cron, scripts, cv_agent).
2. **Given** `JobStorage(db_path, user_id=X)`, **Then** all user-space reads are filtered on `X` and all user-space writes set `X`.
3. **Given** catalogue methods (jobs, extraction, runs, company facts), **Then** they ignore the bound user.
4. **Given** two users, **When** both watch the same company, **Then** the monitoring set contains it once; **When** one blacklists a company, **Then** only that user's feed hides it.
5. **Given** a job engaged by user A only, **When** `purge_stale_jobs` runs, **Then** the job is kept.
6. **Given** contacts, **Then** two users may each hold a contact with the same `linkedin_url`, and neither sees the other's.

---

### User Story 3 — Released through the standard flow, no visible change (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `staging.sh up <exact SHA>`, **Then** the migration runs on the staging copy; a migration report (row counts per table before/after, rows without `user_id` = 0, `user_companies` count) is produced and checked.
2. **Given** staging, **Then** parity (candidate vs current prod image, same backup copy, same `--as-of`) is identical; the tracker checklist passes (feed, status change, application view, contact edit, interaction add/delete, company status change incl. blacklist, monitored toggle, dashboard, settings); `core.scrape --monitored-only --no-score` targets the same companies as before; `core.score --extract` runs.
3. **Given** the deploy, **Then** the pre-rebuild backup is verified first; deployed SHA = staged SHA; health OK; next scheduled cron `success`.
4. **Given** a problem after deploy, **Then** the documented rollback is: restore the pre-rebuild backup (spec 031 procedure) + redeploy the previous SHA. It is rehearsed once on staging before the deploy.

---

### Edge Cases

- Companies with default values only (`status = 'prospect'`, no notes, not monitored) → no `user_companies` row; reads treat "no row" as the default relationship.
- Jobs with a `job_applications` row but no `job_tracking` row (or the reverse) → both migrated as they are; no row is invented.
- `status_history` / `interactions` rows referring to purged jobs → migrated as they are (`user_id` set), no cleanup in this spec.
- The cv_agent on the Mac writes to the DEV DB through `JobStorage`: it binds to the default user of that DB; the DEV DB gets the same migration on first open.
- New DB (fresh install / tests): the default user is created at init, so onboarding works unchanged.
- Concurrency: the migration runs at the first `JobStorage` open after deploy; the deploy avoids cron windows; `deploy.sh`'s `core.seed` step is the first opener.

---

## Requirements *(mandatory)*

### Functional Requirements

**Schema (US1)**

- **FR-001**: Create `users (id TEXT PRIMARY KEY, display_name TEXT, active_profile_id TEXT, onboarding_completed_at TEXT, created_at TEXT NOT NULL)` with an **opaque UUID** id; create the default user at migration / fresh init; record the default user id in `config` (`default_user_id`); move `active_profile_id` and `onboarding_completed_at` out of `config` into `users`.
- **FR-002**: Add `user_id` to `search_profiles`, `status_history`, `contacts`, `interactions`, `company_status_history`; backfill to the default user.
- **FR-003**: `job_tracking` and `job_applications` MUST be **rekeyed** to `(user_id, job_id)` via the standard table-rebuild procedure (create new table, copy, drop, rename, recreate indexes) inside the migration transaction.
- **FR-004**: Create `user_companies (user_id, company_id, status, notes, monitored, watch_requested_at, …, PRIMARY KEY (user_id, company_id))`; backfill from `companies` (rows with a non-default relationship only); move `company_status_history` ownership to the user.
- **FR-005**: Split `monitoring_status`: the user's request to watch → `user_companies`; the platform's capability (`watch_ready` / `watch_unsuitable` / research pending) stays on `companies`. The plan defines the exact columns and value mapping after reading every writer (`set_monitoring_status`, `core/monitoring_agent.py`, `core/company_researcher.py`, `core/seed.py`).
- **FR-006**: Per-user unique indexes: `contacts (user_id, linkedin_url)`, `interactions (user_id, source_ref)`; indexes on `user_id` where queries filter on it.
- **FR-007**: The whole migration is one transaction, recorded in `migrations`, idempotent; old columns on `companies` are kept unused (dropped at step 10).

**Storage behaviour (US2)**

- **FR-008**: `JobStorage.__init__(db_path, user_id=None)` binds to `user_id` or the default user. No caller changes are needed for single-user behaviour.
- **FR-009**: Every method reading user-space tables filters on the bound user; every method writing them sets it. Catalogue methods are unaffected.
- **FR-010**: Monitoring targets = companies watched by at least one user; feed exclusion of blacklisted companies = the bound user's blacklist only.
- **FR-011**: `purge_stale_jobs` keeps any job engaged by any user.
- **FR-012**: Isolation test suite (two users, in-memory DB) covering every public method that touches a user-space table; the plan lists the methods and the suite asserts coverage (a new user-space method without an isolation test fails the suite).
- **FR-013**: Existing tests keep passing unchanged except where they assert the old schema; those are updated, not deleted.

**Release (US3)**

- **FR-014**: A migration report command (`python -m scripts.migration_report --db <path>`) prints row counts per table, rows without `user_id`, and `user_companies` count — run before and after on staging.
- **FR-015**: Standard release flow, plus a rehearsed rollback on staging (restore backup + previous image) before the prod deploy.
- **FR-016**: Constitution X's known violations (personal tables without `user_id`, `companies.monitored` on a shared entity) are removed from the Sync Impact Report (PATCH); `CLAUDE.md` DB schema section updated.

### Non-goals

- No authentication, no second real user, no user switching in the UI.
- No API (Phase C), no change to `job_scores` keys, no change to `runs.profile_id`.
- No change to the ingestion scope (step 4).
- No dropping of old columns (step 10).
- No UI change (no mockup): same screens, same results.

### Key Entities

- **User**: the owner of a profile, tracking, applications, company relationships, contacts and interactions.
- **User–company relationship** (`user_companies`): status, notes, watch decision for one user and one company.

---

## Success Criteria *(mandatory)*

- **SC-001**: On the staging copy: row counts unchanged per table; 0 user-space rows without `user_id`; `user_companies` count = companies with a non-default relationship.
- **SC-002**: Isolation suite green: no public read returns another user's rows; adding a user-space method without isolation coverage fails the suite.
- **SC-003**: Parity identical; staging tracker checklist passes; monitoring target set unchanged.
- **SC-004**: Rollback rehearsed on staging and documented with its measured duration.
- **SC-005**: Deploy: SHA = staged SHA; health OK; next scheduled cron `success`.

---

## Resolved questions

- **Q1 — `job_tracking` / `job_applications` primary keys** → rebuild now, key `(user_id, job_id)` (see Clarifications).
- **Q2 — User-level settings in `config`** → moved to `users` (see Clarifications).
- **Q3 — User id format** → opaque UUID (see Clarifications).
