# Tasks: API v1 — read-only (roadmap step 5 — Phase C)

**Input**: Design documents in `specs/041-api-read/` — `plan.md`, `spec.md`,
`research.md`, `data-model.md`, `contracts/`, `quickstart.md`.

**Tests**: REQUIRED — the spec mandates contract tests (FR-008), a guard test
(FR-009), an isolation-suite extension (FR-010), an OpenAPI snapshot (FR-008),
and storage unit tests (Development Workflow). Every phase ends with its tests
green before the next phase starts.

**Organization**: grouped by user story (all four are P1) with a shared storage
foundation first. Stories build in dependency order US1 → US2 → US3 → US4.

**Authorized core edit**: `core/storage.py` is in the "Stable core" list, but this
roadmap step explicitly concerns it (research §1/§3/§8) — the `api_tokens` table,
its accessors, and the five new read methods are in scope. No other stable file is
touched.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: new dependency, package registration, and package skeleton.

- [X] T001 [P] Add `fastapi` and `uvicorn` to `requirements.txt`, pinned to versions ≥ 14 days old (FR-002; Constitution authorises these for `api/`).
- [X] T002 [P] Add `api` to the packages list in `pyproject.toml` (FR-001).
- [X] T003 [P] Create the `api/` package skeleton: `api/__init__.py` and `api/routers/__init__.py` (FR-001).

---

## Phase 2: Foundational (Storage Layer — blocking)

**Purpose**: the persistence additions every user story needs. All edits are in
`core/storage.py` (authorized this step) and its tests. No user story may start
until this phase is green.

- [X] T004 Add the idempotent `api_tokens` migration to `core/storage.py` (DDL incl. the `expires_at` column + `idx_api_tokens_user` index, recorded in `migrations`) per `data-model.md` §1 (FR-004, FR-013).
- [X] T005 Add the five token accessors to `core/storage.py` — `create_api_token`, `list_api_tokens`, `revoke_api_token`, `resolve_api_token`, `touch_api_token` — with the exact signatures and semantics in `contracts/api_tokens.md` (SHA-256 hash only; `list` never returns `token_hash`; `resolve` returns `expires_at`/`revoked_at` and is a pure read, no 401 logic) (FR-004, FR-013).
- [X] T006 Add the three catalogue read methods to `core/storage.py` — `get_catalogue_jobs`, `get_catalogue_job`, `get_company_facts` — with the verbatim keys and WHERE filters in `data-model.md` §3.1–3.3 (shared columns only, no user-space field or aggregate).
- [X] T007 Add `get_user()` to `core/storage.py` per `data-model.md` §3.4 (returns the bound user's `users` row: `id`, `display_name`, `active_profile_id`, `onboarding_completed_at`).
- [X] T008 Add `get_user_scores_for_job(job_id)` to `core/storage.py` per `data-model.md` §3.5 — `job_scores` filtered to the bound user's profiles via `search_profiles.user_id = self.user_id`, keys `profile_id, score, reason, scored_by, scored_at`. The API uses this, never the cross-user `get_scores_for_job` (FR-010, spec 039 rule).
- [X] T009 Add unit tests to `tests/test_storage.py`: token round-trip (create→resolve→touch→list→revoke→resolve-after-revoke), hash-never-equals-token, scope validation raises on unknown scope, migration idempotency (second open changes nothing), catalogue methods return no user-space key, and `get_user_scores_for_job` returns only the bound user's profiles' scores.
- [X] T010 Classify the new methods in `tests/test_isolation.py` per research §8 — `create_api_token`/`list_api_tokens`/`revoke_api_token`/`resolve_api_token`/`touch_api_token` → cross-user, `get_user`/`get_user_scores_for_job` → user-space, `get_catalogue_jobs`/`get_catalogue_job`/`get_company_facts` → catalogue — and confirm the coverage meta-test passes. Add an isolation test proving `get_user_scores_for_job` never returns another user's profile score.

**Checkpoint**: storage foundation ready; `tests/test_storage.py` + `tests/test_isolation.py` green. User-story work can begin.

---

## Phase 3: User Story 1 — A running, private API service (Priority: P1) 🎯 MVP

**Goal**: an `api` service in the stack, reachable only through Tailscale, with a
token-free `/health` endpoint.

**Independent Test** (US1): after deploy, `GET https://<verva>.<tailnet>.ts.net/api/health`
returns `200 {"status":"ok","version":"<git sha>"}` with a valid cert; LAN port 8601
is refused; `docker port` shows a loopback-only binding.

- [X] T011 [US1] Create `api/main.py` — a `FastAPI` app factory with `title`, `version=<git sha>`, `root_path="/api"`, `docs_url="/docs"`, `openapi_url="/openapi.json"`, plus `GET /health` returning only `{"status":"ok","version":<sha>}` (no auth). Version comes from env/build, never a live DB read (research §6).
- [X] T012 [P] [US1] Add the `api` service to `docker-compose.yml` per research §7: `build: .`, `command: uvicorn api.main:app --host 127.0.0.1 --port 8601`, `ports: ["127.0.0.1:8601:8601"]`, `volumes: [job_data:/app/data]`, `env_file: .env`, `JOB_AGENT_DATA_DIR=/app/data`, `JOB_AGENT_REQUIRE_DB=1`, `restart: unless-stopped`.
- [X] T013 [P] [US1] Add a `check_api_health(url)` function to `scripts/health_check.py` (GET `/health`, assert JSON `status == "ok"`); do not reuse the Streamlit-specific `/_stcore/health` check. Read the base URL from `JOB_AGENT_API_URL` with the tailnet default (FR-016).
- [X] T014 [US1] Update `scripts/deploy.sh`: `docker compose build tracker agent api`, then `docker compose up -d api` **after** the seed step (FR-003; preserves seed-first DB open to avoid a first-ever migration race).
- [X] T015 [P] [US1] Document the tailnet HTTPS `/api` exposure in `docs/infrastructure.md`: MagicDNS + HTTPS-cert prerequisite, `tailscale serve --bg --https=443 --set-path /api http://127.0.0.1:8601`, `tailscale serve status`, and undo; tracker keeps its `tcp 8501` exposure unchanged (research §6, FR-016).
- [X] T016 [US1] Add a `/health` contract test to `tests/test_api_contract.py`: `200`, keys exactly `{status, version}`, `version` a string, no `Authorization` header required.

**Checkpoint**: `/health` answers locally and the compose/deploy wiring is in place.

---

## Phase 4: User Story 2 — Scoped bearer tokens bound to a user (Priority: P1)

**Goal**: create/list/revoke tokens bound to a user and scopes; bearer auth resolves
a token to a user with `401`/`403`/`404` semantics and no token in any log.

**Independent Test** (US2): a `read:me` token reads `/v1/me/*` but gets `403` on
`/v1/jobs`; after revocation it gets `401`; the raw token never appears in the DB,
logs, or error messages.

- [X] T017 [US2] Create `scripts/api_token.py` (run `python -m scripts.api_token`) with `create`/`list`/`revoke` per `contracts/api_token_cli.md`: `--name`, `--scopes`, `--user`, `--expires-in` (FR-013), `--json` on list; raw token printed exactly once; `list` omits `token_hash`; exit `2` for invalid args, `1` for unknown revoke id. Uses `JobStorage` only, no SQL.
- [X] T018 [US2] Create `api/deps.py` per `contracts/auth.md`: `get_db` (the single unbound `JobStorage(DB_PATH)` site), `get_current_user` (bearer extract → `resolve_api_token` → reject missing/unknown/revoked/expired as `401` → `AuthContext(user_id, scopes, token_id)`, throttling `touch_api_token` ≤ once/minute/token), `get_user_db` (bound `JobStorage(DB_PATH, user_id=…)`), and `require_scope(scope)` (`403` on missing scope). Include the FR-014 failed-auth logger (`{timestamp, client_addr, route, reason}`, no token material, counted per client address in memory).
- [X] T019 [US2] Add the error envelope to `api/schemas.py` (`{"error","message"}`) and register exception handlers in `api/main.py` so every non-2xx is generic JSON — no stack traces or internal paths (FR-015).
- [X] T020 [US2] Add auth tests to `tests/test_api_auth.py`: 401 (missing/unknown/revoked/**expired**), 403 (scope), scope constants, expiry via `--expires-in` (FR-013), the `last_used_at` throttle, the CLI (create prints once, list omits hash, revoke), and the FR-006/FR-014 no-token-in-logs + failed-auth-log assertions.

**Checkpoint**: tokens can be created/resolved/revoked and the auth dependency enforces scopes without leaking secrets.

---

## Phase 5: User Story 3 — Read the catalogue and my space (Priority: P1)

**Goal**: versioned read endpoints on the catalogue and the token user's space,
returning exactly what `JobStorage` returns, with contract + guard + OpenAPI +
isolation coverage.

**Independent Test** (US3): contract tests (`TestClient`, in-memory DB) for every
endpoint; each payload equals its `JobStorage` source field-for-field; the OpenAPI
document matches the committed snapshot.

- [X] T021 [US3] Create `api/schemas.py` resource response models — one explicit pydantic model per endpoint payload, field-for-field over the dict keys in `contracts/endpoints_catalogue.md` and `contracts/endpoints_me.md` (plus the list envelope `{items,total,limit,offset}`), all `extra="forbid"` (US3-4). Query params are typed/validated by pydantic (FR-015). (Extends the file created in T019.)
- [X] T022 [US3] Create `api/serializers.py` — dict/`sqlite3.Row` → pydantic model conversion with `extra="forbid"` at the boundary; dates/enums passed through as the strings `JobStorage` returns (research §9).
- [X] T023 [P] [US3] Create `api/routers/catalogue.py` per `contracts/endpoints_catalogue.md`: `GET /v1/jobs` (filters `company_id`/`source`/`posted_after`, list envelope, API-side pagination), `GET /v1/jobs/{job_id}`, `GET /v1/companies/{company_id}` — all `read:catalogue`, 404 for absent ids, no user-space key.
- [X] T024 [P] [US3] Create `api/routers/me.py` per `contracts/endpoints_me.md`: `/v1/me`, `/v1/me/feed` (API-side `status`/`min_score` filter + pagination over `get_all_for_tracker`), `/v1/me/jobs/{job_id}` (composed; scores from `get_user_scores_for_job`, **never** API-side filtering), `/v1/me/stats`, `/v1/me/dashboard`, `/v1/me/applications`, `/v1/me/companies`, `/v1/me/companies/{company_id}`, `/v1/me/contacts`, `/v1/me/contacts/{contact_id}` — all `read:me`, other-user/absent resources → `404` never `403`.
- [X] T025 [US3] Mount both routers in `api/main.py` (`app.include_router`, one router per domain) and wire `require_scope` per the route→scope map in `contracts/auth.md`.
- [X] T026 [P] [US3] Create `tests/test_api_contract.py` — per-endpoint contract test seeding an in-memory DB and asserting each payload equals its `JobStorage` source(s) field-for-field; catalogue payloads contain no user-space key; `404` for absent ids; wrong-scope token → `403`. (Includes the `/health` test from T016.)
- [X] T027 [P] [US3] Create `tests/test_api_guard.py` (FR-009) — source scan that fails if `api/` calls a `JobStorage` method outside the read allow-list (which must include `get_user_scores_for_job` and **exclude** `get_scores_for_job`), imports `sqlite3`, or constructs a `JobStorage` not bound to the token user, except `deps.py`'s unbound `get_db` and the two auth accessors (`resolve_api_token`, `touch_api_token`).
- [X] T028 [US3] Create `tests/test_api_openapi.py` and commit the `api/openapi.json` snapshot (FR-008): regenerate the schema and diff against the committed file, failing on any drift.
- [X] T029 [US3] Extend `tests/test_isolation.py` to the API (FR-010, research §8): two users + two tokens + two profiles against a `TestClient` on an in-memory DB — token B on A's contact/company → `404`, B's feed/stats/dashboard/applications show only B's data, `/v1/me/jobs/{id}` scores are B's profiles only (via `get_user_scores_for_job`).

**Checkpoint**: every v1 endpoint returns its `JobStorage` source, guarded and snapshot-tested, with per-user isolation proven.

---

## Phase 6: User Story 4 — Released through the standard flow (Priority: P1)

**Goal**: staging runs the API too, and the standard release flow ships it with
rollback = stop/remove the `api` service.

**Independent Test** (US4): `staging.sh up <sha>` runs the API on 8602/8443; staging
token curls match the staging tracker's figures; prod `/api/health` 200 with a valid
cert, LAN 8601 refused, tracker + next cron unaffected.

- [ ] T030 [US4] Update `scripts/staging.sh` per FR-011 / research §7: run the API on `127.0.0.1:8602`, expose `tailscale serve --bg --https=8443 --set-path /api http://127.0.0.1:8602`, and have `down` remove both the api container and the 8443 serve entry (verification output includes the 8443 entry).
- [ ] T031 [US4] **Staging verification** (SC-004): `staging.sh up <exact SHA>` (never the branch name), create a staging token on the staging volume, then from the Mac `curl -s https://<verva>.<tailnet>.ts.net:8443/api/health` with a **valid certificate (no `-k`)**, plus `/api/v1/me/feed`, `/api/v1/me/jobs/<id>`, `/api/v1/me/contacts`; confirm the figures equal the staging tracker's; then `staging.sh down`.
- [ ] T032 [US4] **🛑 STOP — review before merge.** Present the staging result and the full green test suite; do **not** merge until the user approves.
- [X] T033 [US4] Create `docs/api-exposure.md` (FR-017): record the prerequisites for any future public exposure (IdP/passkeys + session cookies, rate limiting/brute-force, token rotation, security headers/size limits, audit retention, dependency policy, security review, Constitution VII amendment) and the candidate entry points. Nothing implemented here.
- [X] T034 [P] [US4] Update `CLAUDE.md`: commands (serve the API, token CLI), infrastructure (api service, tailnet `/api`, `JOB_AGENT_API_URL`), and an API section (FR-012, FR-016).
- [ ] T035 [US4] **🛑 STOP — before deploy.** After `git merge --ff-only <full-sha>` onto `main`, confirm the exact SHA and a pre-deploy DB backup exist; do **not** run `deploy.sh` until the user approves.
- [ ] T036 [US4] **Prod release** (SC-005): `./scripts/deploy.sh`, then verify `git -C /opt/job-agent rev-parse HEAD` equals the merged SHA, `https://<verva>.<tailnet>.ts.net/api/health` returns `200` with a **valid certificate (no `-k`)**, the **LAN port 8601 is refused**, the tracker health check is OK, and the next cron run is `success`.
- [ ] T037 [US4] After prod release passes, mark roadmap step 5 done in `docs/roadmap-api.md` (FR-012; only at closing).

**Checkpoint**: staging and prod both serve the API over the tailnet; docs and roadmap reflect the shipped step.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: end-to-end validation before the release is considered done.

- [X] T038 Run the `quickstart.md` validation end-to-end on the dev Mac against a DB copy: contract/auth/guard/OpenAPI tests, local `uvicorn` serve, token create, parity script (API feed == `JobStorage` feed), full `python -m pytest tests/ -q` green.
- [X] T039 Confirm `tests/test_storage.py` and `tests/test_isolation.py` pass at 100% and the full suite is green (Constitution VI; Development Workflow).

---

## FR-013 → FR-017 traceability

| FR | Task(s) | What it covers |
|---|---|---|
| FR-013 (token expiry) | T004, T005, T017, T018, T020 | `expires_at` column; `resolve` returns it; `create --expires-in`; auth → `401` on expired |
| FR-014 (failed-auth log, no secrets) | T018, T020 | failed-auth logger (`{ts, client_addr, route, reason}`), per-client count, no token material |
| FR-015 (generic errors, no bodies) | T019, T021 | error envelope `{error,message}`, no stack traces/paths; pydantic query validation; GET-only |
| FR-016 (`JOB_AGENT_API_URL`, no hardcoded host) | T013, T015, T034 | health_check reads env; infra docs + CLAUDE.md document the config |
| FR-017 (`docs/api-exposure.md`) | T033 | prerequisites recorded before any public exposure |

No FR is unaccounted for — all five map to concrete tasks (previously implicit, now explicit).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup; **blocks all user stories**.
- **US1 (Phase 3)**: after Foundational. No other story dependency (health + infra only).
- **US2 (Phase 4)**: after Foundational. Independent of US1.
- **US3 (Phase 5)**: after US2 (needs `get_current_user`/`get_user_db` from `api/deps.py`) and after US1 (mounts routers into `api/main.py`).
- **US4 (Phase 6)**: after US1–US3 (wraps the deploy/staging/docs of a complete feature).
- **Polish (Phase 7)**: after all stories.

### User Story Dependencies

- **US1**: after Foundational; nothing else.
- **US2**: after Foundational; nothing else (auth is self-contained over `JobStorage`).
- **US3**: after US2 (auth) and US1 (app factory). The one real cross-story dependency.
- **US4**: after US1 + US3 (release wraps the service + endpoints).

### Within a Story

- Storage methods before their tests (Phase 2); accessors before auth (US2); schemas/serializers before routers (US3); routers mounted before contract/guard/OpenAPI/isolation tests (US3).
- Each phase's tests must be green before the next phase starts.

### Parallel Opportunities

- Phase 1: T001, T002, T003 all [P] (different files).
- US1: T012, T013, T015 [P] (compose, health_check, infra docs are independent files).
- US3: T023/T024 [P] (two router files), T026/T027 [P] (two test files), each after their inputs exist.

---

## Implementation Strategy

### MVP First (US1)

1. Phases 1 + 2 (setup + storage foundation).
2. Phase 3 (US1) → a `/health`-only service is the first deployable increment.

### Incremental Delivery

1. Setup + Foundational → storage foundation green.
2. US1 → running `/health` service (deployable).
3. US2 → tokens + auth (still deployable, no data routes yet).
4. US3 → full read API with contract/guard/OpenAPI/isolation coverage.
5. US4 → staging + prod release via the standard flow; roadmap step 5 closed.

Each story adds value without breaking the previous one; the tracker is untouched
throughout (it keeps reading `JobStorage` directly).

---

## Notes

- `[P]` = different files, no dependency on an incomplete task.
- `[US#]` maps a task to its user story for traceability.
- Field-level truth lives in `contracts/` and `data-model.md` — tasks reference
  them rather than restating the key lists, so implementation must read both.
- No SQL in `api/`; no LLM/long work in a handler; one `JobStorage` per request
  (unbound for auth, bound to the token user for data).
- Scores are user-scoped in `JobStorage` (`get_user_scores_for_job`), never
  filtered in the API (spec 039 rule); `get_scores_for_job` stays cross-user and
  is **not** exposed.
- Commit after each task or logical group; stop at any checkpoint to validate.
