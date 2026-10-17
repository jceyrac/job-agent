# Tasks: API v1 — write endpoints for the user space (spec 042)

**Input**: `specs/042-api-write/` — `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/api-write.md`, `quickstart.md`

**Tests**: requested by the spec (FR-004 contract, FR-005 guard, FR-006 isolation, SC-001/002/003). Written first (TDD).

**Organization**: by user story; each story is independently testable and releasable.

---

## Phase 1: Setup

- [X] T001 Confirm baseline: on branch `042-api-write`, `.venv` active, `python -m pytest tests/ -q` green (record the passing count as the pre-change baseline).

---

## Phase 2: Foundational (blocks all stories)

*The least-privilege plumbing (scopes, body cap, audit log, enums) plus the guard-test allow-list. No user story can start until this is green.*

- [X] T002 Add `write:me` and `write:catalogue` to `API_TOKEN_SCOPES` in `core/storage.py` (line 65).
- [X] T003 Add `write_me = require_scope("write:me")` and `write_catalogue = require_scope("write:catalogue")` to `api/deps.py`.
- [X] T004 [P] Add `RequestSizeLimitMiddleware` (64 KB → `413 {"error":"payload_too_large",…}`) in new `api/middleware.py`; register it in `api/main.py`.
- [X] T005 [P] Add the structured write-log helper in new `api/write_log.py` (one line: `token_id`, `user_id`, `route`, `resource_id`, `outcome` — no body, no token value).
- [X] T006 [P] Add request enums in `api/schemas.py`: `JobStatus`, `LifecycleEventType`, `InteractionType`, `InteractionDirection`, `InteractionOutcome`, `CompanyStatus` (values per `data-model.md`).
- [X] T007 [P] Update `scripts/api_token.py` `--help`/docstring to name the write scopes (cosmetic — validation already reads `API_TOKEN_SCOPES`).
- [X] T008 Extend `tests/test_api_guard.py`: rename `READ_ALLOW_LIST` → read+write allow-list (add the write methods this spec uses), add a `FORBIDDEN_METHODS` frozenset (`save_unscored`, `save_scored`, `update_job_extraction`, `log_run`, `set_ingestion_scope`, `set_config`, `upsert_company`, `upsert_company_from_seed`, `create_api_token`, `list_api_tokens`, `revoke_api_token`, `purge_stale_jobs`, `import_companies_from_csv`, …) + `test_api_never_calls_forbidden_methods`.

**Checkpoint**: `pytest tests/test_api_guard.py tests/test_api_auth.py -q` green; a `write:me` token can be minted via `create_api_token`.

---

## Phase 3: User Story 1 — Act on my jobs (P1) 🎯 MVP

**Goal**: change a job's status and manage lifecycle events with the same effects as the tracker.

**Independent Test**: `PUT /v1/me/jobs/{id}/status` and the event routes produce rows identical to direct `set_status` / `record_lifecycle_event` / `update_lifecycle_event`+`reconcile_lifecycle_status` / `delete_lifecycle_event`+`reconcile_lifecycle_status`.

### Tests (write first — FAIL before implementation)

- [X] T009 [P] [US1] Contract tests in `tests/test_api_contract.py`: for each route, drive the API on one copy of the seeded DB and the matching `JobStorage` method on an identical copy, assert identical resulting rows in `job_tracking`/`status_history`/`interactions`; `occurred_at` required on create (missing → 422); unknown job/event → 404; an `event_id` that exists but belongs to another job (or another user) → 404 and reconciliation never runs on the wrong job.
- [X] T010 [P] [US1] Isolation tests in `tests/test_isolation.py`: user A's status/event writes are invisible to user B (B's feed/tracking/lifecycle unchanged); A writing B's event id → 404, B's data unchanged; an event owned by A but addressed under a different `job_id` (same or another user) → 404, reconciliation never runs on the wrong job.

### Implementation

- [X] T011 [US1] Request models in `api/schemas.py`: `StatusUpdate`, `LifecycleEventCreate` (required `occurred_at`), `LifecycleEventUpdate` (≥1 field) — all `extra="forbid"`.
- [X] T012 [US1] Routes in `api/routers/me.py`: `PUT /v1/me/jobs/{job_id}/status`, `POST /v1/me/jobs/{job_id}/events`, `PATCH /v1/me/jobs/{job_id}/events/{event_id}`, `DELETE /v1/me/jobs/{job_id}/events/{event_id}` — 404 pre-checks + `reconcile_lifecycle_status` after edit/delete + write-log, per `contracts/api-write.md`.

**Checkpoint**: `pytest tests/test_api_contract.py tests/test_isolation.py -q -k us1_or_status_or_event` green; status change visible through `/v1/me/jobs/{id}`.

---

## Phase 4: User Story 2 — Companies, contacts, interactions (P1)

**Goal**: manage the company relationship (status/notes/watch), contacts and interactions in the user space.

**Independent Test**: `PATCH /v1/me/companies/{id}`, contact create/edit, and interaction routes produce rows identical to direct `set_company_status`/`set_company_monitored`, `upsert_contact`, `update_contact_fields`, `log_interaction`.

### Tests

- [X] T013 [P] [US2] Contract tests in `tests/test_api_contract.py` (as T009, for US2 routes); blacklist hides the company from *this* user's feed only.
- [X] T014 [P] [US2] Isolation tests in `tests/test_isolation.py`: A's company relationship/contacts/interactions invisible to B; A writing B's contact id → 404.

### Implementation

- [X] T015 [US2] Request models in `api/schemas.py`: `CompanyRelationshipUpdate`, `ContactCreate`, `ContactUpdate`, `InteractionCreate` — `extra="forbid"`.
- [X] T016 [US2] Routes in `api/routers/me.py`: `PATCH /v1/me/companies/{company_id}`, `POST /v1/me/contacts`, `PATCH /v1/me/contacts/{contact_id}`, `POST /v1/me/contacts/{contact_id}/interactions`, `POST /v1/me/companies/{company_id}/interactions` — per `contracts/api-write.md`.

**Checkpoint**: `pytest tests/test_api_contract.py tests/test_isolation.py -q -k us2_or_company_or_contact_or_interaction` green.

---

## Phase 5: User Story 3 — Manage my profile (P2)

**Goal**: switch the active profile and edit profile criteria; no rescoring.

**Independent Test**: `PUT /v1/me/active-profile` and `PUT /v1/me/profiles/{id}` produce the same rows as `set_active_profile_id` / `upsert_profile`; a foreign profile id → 404.

### Tests

- [X] T017 [P] [US3] Contract + isolation tests in `tests/test_api_contract.py` / `tests/test_isolation.py`: active-profile switch and profile edit; foreign profile → 404; no score rows are written.

### Implementation

- [X] T018 [US3] Request models in `api/schemas.py`: `ActiveProfileUpdate`, `ProfileUpdate`.
- [X] T019 [US3] Routes in `api/routers/me.py`: `PUT /v1/me/active-profile`, `PUT /v1/me/profiles/{profile_id}` — ownership pre-check (`get_profile`) → 404, no rescoring.

**Checkpoint**: `pytest tests/test_api_contract.py tests/test_isolation.py -q -k us3_or_profile` green.

---

## Phase 6: User Story 4 — Least privilege & safety (P1)

**Goal**: catalogue fact write + prove scope/body/log enforcement (read-only → 403, write:me → 403 on catalogue, 64 KB → 413, structured log, guard green).

### Tests

- [X] T020 [P] [US4] Scope tests in `tests/test_api_auth.py`: read-only token → 403 on every write route; `write:me` → 403 on `PATCH /v1/companies/{id}`; `write:catalogue` → 403 on `write:me` routes.
- [X] T021 [P] [US4] Body-cap + validation tests in `tests/test_api_contract.py`: >64 KB body → 413; unknown request field → 422 (`extra="forbid"`); write-log emits one line (assert via `caplog`).

### Implementation

- [X] T022 [US4] Request model `CompanyFactUpdate` in `api/schemas.py` (fact fields only — no `notes`/`status`/`monitored`).
- [X] T023 [US4] Route `PATCH /v1/companies/{company_id}` in `api/routers/catalogue.py` → `update_company_fields` (scope `write:catalogue`), 404 pre-check via `get_company_facts`.

**Checkpoint**: `pytest tests/test_api_guard.py tests/test_api_auth.py tests/test_api_contract.py -q` green — guard fires on a forbidden call, scopes enforce 403, body cap enforces 413.

---

## Phase 7: User Story 5 — Release through the standard flow (P1)

**Goal**: snapshot + docs + staging round-trip + deploy (SHA check, parity, tracker/cron unaffected).

**GATED** — STOP before T027 (staging) and before T028 (merge/deploy). Release runs only on an explicit user "go".

- [X] T024 [US5] Regenerate `api/openapi.json` (deliberately) and make `tests/test_api_openapi.py` green — the new write routes appear with `security: [{HTTPBearer: []}]`.
- [X] T025 [US5] Update the API section of `CLAUDE.md` (write scopes + write routes + `write:catalogue` fact fields).
- [X] T026 [US5] Add the CSRF note to `docs/api-exposure.md` (bearer-only today; revisit with cookie sessions).
- [X] T027 [US5] Staging round-trip (quickstart §8) — **STOP before this step**:
  1. Stage the **exact SHA** with the **branch's** `scripts/staging.sh up <full-sha>` (never the branch name); confirm `/api/health` version == staged SHA.
  2. Mint a staging `write:me` token **on the staging volume** (never printed in logs).
  3. From the Mac, exercise **each** write route once with that token; confirm each effect is visible in the staging tracker, and a tracker-side write is visible through the API.
  4. Confirm tracker parity vs the **prod image** is byte-identical **before** any API write on staging.
- [X] T028 [US5] Deploy — **STOP before this step; deploy outside 07:00/11:00/16:00**:
  `./scripts/deploy.sh` → `/api/health` version == staged SHA; tracker + next cron unaffected; **no production write token created by default** (mint one only when a client needs it).

---

## Phase 8: Polish & validation

- [X] T029 Run `python -m pytest tests/ -q` (full suite green — 522 baseline + new write tests).
- [X] T030 Run `specs/042-api-write/quickstart.md` §1–§7 locally; confirm each curl behaviour (200/201/204/403/404/413/422).

---

## Dependencies & Execution Order

- **Phase 1 → 2**: Setup, then Foundational (T002–T008). T002 gates every write test (token minting).
- **Phase 3–6**: each user story starts after Foundational. US1 and US2 are independent (different routes/files); US3 is independent; US4's route is independent but its *scope/body/log* plumbing is the Foundational work.
- **Phase 7**: after US1–US4 route code + tests are in.
- **Phase 8**: after everything.

### Parallel opportunities

- T004/T005/T006/T007 are different files → parallel.
- Within a story, contract tests (T009/T013/T017) and isolation tests (T010/T014) are different files → parallel.
- After Foundational, US1/US2/US3 route work touches different parts of `api/routers/me.py` — implement sequentially within one file, but US4's `catalogue.py` route is a separate file (parallel with US1–US3).

## Implementation Strategy

**MVP** = Foundational + US1 (status + lifecycle) — deliverable and independently testable. Then US2, US3, US4 in priority order (US4's safety tests ride along the whole time), then the release flow.

## Notes

- `core/storage.py` is touched **only** at `API_TOKEN_SCOPES` (T002) — no storage-method change.
- Every route re-reads via an existing allow-listed read method (research R2); the contract tests assert **rows**, not response bodies.
- Commit after each phase; stop at any checkpoint to validate independently.
