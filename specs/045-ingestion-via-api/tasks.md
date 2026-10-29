# Tasks: Ingestion through the API (roadmap step 7)

**Input**: Design documents from `/specs/045-ingestion-via-api/`

**Prerequisites**: `plan.md`, `spec.md` (5 user stories, all P1), `research.md` (R1–R9), `data-model.md`, `contracts/ingest-api.md`, `quickstart.md`.

**Tests**: Requested by the spec (FR-001 guard, FR-003 contract, FR-004 storage, FR-006 identity, FR-008 isolation/guard). Test tasks are written first and expected to fail before their implementation.

**Cross-spec dependency**: none — 045 builds on the shipped `ingest` scope + `POST /v1/jobs` (spec 044) and the `core/prepare.py` hotfix that unblocked 044b's clean `ruff` baseline.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: No new dependency — confirm the branch starts clean.

- [x] T001 Baseline — confirm `ruff check .` and `python -m pytest tests/` are green on `045-ingestion-via-api` before any change (records the clean start point).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Storage seam for the scope extension and the platform principal — both stories read them; nothing user-facing changes yet.

- [x] T002 [P] Extend `get_ingestion_scope()` / `set_ingestion_scope()` in `core/storage.py` to carry `search_locations` (list[str]) and `greenhouse_boards` (list[str]), typed with the existing safe-default coercion (absent/malformed → `[]`) — FR-002.
- [x] T003 [P] Add a one-time seed migration in `core/storage.py` that copies the active profile's `effective_search_locations()` and `greenhouse_boards` into `config.ingestion_scope` so day one is identical — FR-002.
- [x] T004 [P] Extend `scripts/ingestion_scope.py` with `--set-locations` and `--set-boards` (prose path, comma/newline-split like `--set-titles`) — FR-002.
- [x] T005 [P] Add `users.is_service INTEGER NOT NULL DEFAULT 0` migration and seed the `platform` principal row (`id='platform'`, `display_name='Platform ingestion'`, `is_service=1`, no `active_profile_id`) in `core/storage.py` — FR-007 / Q3.
- [x] T006 [P] Extend `scripts/api_token.py` to create an ingest token for the platform principal (`--user platform --scopes ingest`; `ingest` scope already exists) — FR-007.
- [x] T007 [P] Update `tests/test_storage.py` and `tests/test_ingestion_scope.py` for the scope extension and `is_service` (round-trip, defaults, seed idempotency).

**Checkpoint**: scope carries locations/boards; the platform principal exists; no user-facing change.

---

## Phase 3: User Story 1 — Scrapers are pure functions (Priority: P1) 🎯 MVP

**Goal**: every scraper receives targets/locations/boards/slugs/enabled state from the orchestrator and returns postings — no `JobStorage`, no profile read.

**Independent Test**: `tests/test_scraper_purity.py` — the guard fails any module under `core/scrapers/` importing `core.storage`/`JobStorage`/`load_active_profile` or calling `get_config`/`set_config`; each scraper passes a unit test with an injected plan + fake HTTP layer (SC-001).

### Tests for User Story 1

> **NOTE: write these first, ensure they FAIL before implementation.**

- [x] T008 [P] [US1] Write `tests/test_scraper_purity.py` — source-scan guard + per-scraper unit tests for `boards/linkedin.py`, `boards/indeed.py`, `greenhouse.py`, `ats/lever.py`, `ats/workable.py` (injected plan + fake HTTP, no DB) — FR-001 / SC-001.

### Implementation for User Story 1

- [x] T009 [US1] Refactor `core/scrapers/base.py` — drop `storage`; constructor takes the injected plan slice (locations/boards/slugs/enabled); remove `is_enabled()`/`disable()` storage reads (enabled is decided by the orchestrator; auto-disable is reported back as an event) — FR-001.
- [x] T010 [P] [US1] Refactor `core/scrapers/boards/linkedin.py` to take search locations from the injected plan (no `load_active_profile`) — FR-001.
- [x] T011 [P] [US1] Refactor `core/scrapers/boards/indeed.py` to take search locations from the injected plan (no `load_active_profile`) — FR-001.
- [x] T012 [P] [US1] Refactor `core/scrapers/greenhouse.py` to take boards from the injected plan (no `load_active_profile`/`get_greenhouse_boards`) — FR-001.
- [x] T013 [P] [US1] Refactor `core/scrapers/ats/lever.py` and `core/scrapers/ats/workable.py` to take slugs from the injected plan (no `get_lever_slugs`/`get_workable_slugs`) — FR-001.
- [x] T014 [US1] Update `core/scrape.py` (`_run_broad_scrape` + `_run_monitored_only`) to compute targets/locations/boards/slugs/enabled from its existing direct `JobStorage` reads and inject them into scrapers — behaviour-neutral, app stays operational (the flag arrives in US4).

**Checkpoint**: scrapers are pure and unit-tested; the direct path still runs identically.

---

## Phase 4: User Story 2 — The orchestrator gets its plan from the API (Priority: P1)

**Goal**: `GET /v1/ingest/plan` returns everything a run needs with no profile read and no user-space data.

**Independent Test**: contract test — the plan equals the direct path's reads field-for-field, gated by `ingest` scope only.

### Tests for User Story 2

- [x] T015 [P] [US2] Add contract tests for `GET /v1/ingest/plan` in `tests/test_api_ingest.py` (field-for-field vs direct reads; 401 without token, 403 without `ingest`) — FR-003.

### Implementation for User Story 2

- [x] T016 [US2] Add the `IngestPlan` response schema in `api/schemas.py` (`extra="forbid"`, fields per `data-model.md`).
- [x] T017 [US2] Add `api/routers/ingest.py` with `GET /v1/ingest/plan` (assembles scope + `freshness_days` + enabled sources + monitoring gates + monitored targets + board slugs via `JobStorage`; no profile/user-space).
- [x] T018 [US2] Register the router in `api/main.py` under prefix `/v1/ingest`.

**Checkpoint**: the plan endpoint serves the run plan; US1 scrapers could consume it.

---

## Phase 5: User Story 3 — The API is the single writer for ingestion (Priority: P1)

**Goal**: `POST /v1/ingest/batch` deduplicates, upserts companies and saves jobs server-side in short `BEGIN IMMEDIATE` transactions; `POST /v1/ingest/events` writes auto-disables and scrape-only run records.

**Independent Test**: contract test — idempotent batch, per-posting outcomes, engaged dedup + `last_seen` touch, a failing posting doesn't fail the batch, 413 over the 5 MB limit.

### Tests for User Story 3

- [x] T019 [P] [US3] Add contract tests for `POST /v1/ingest/batch` and `POST /v1/ingest/events` in `tests/test_api_ingest.py` (idempotency, per-posting outcomes, engaged dedup + touch, failing posting ≠ failing batch, 413 over 5 MB) — FR-003.

### Implementation for User Story 3

- [x] T020 [US3] Add `JobStorage.ingest_batch(postings)` in `core/storage.py` — load the engaged-key union once, then commit in ~50-posting `BEGIN IMMEDIATE` sub-chunks: `JobPosting.id` computation, engaged dedup + `last_seen` touch, `upsert_company`, `save_unscored` (new) / `touch_many` (existing); return `{new, existing, dropped_engaged, outcomes}` — FR-004.
- [x] T021 [P] [US3] Add `tests/test_storage.py` cases for `ingest_batch` (idempotency, dedup, counts, chunk boundaries).
- [x] T022 [US3] Add the batch/event request+response schemas in `api/schemas.py`.
- [x] T023 [US3] Add `POST /v1/ingest/batch` and `POST /v1/ingest/events` in `api/routers/ingest.py` (call `ingest_batch`; structured write-log; no SQL) — FR-003/FR-004.
- [x] T024 [US3] Raise the request-size limit to 5 MB for `/v1/ingest/batch` only in `api/middleware.py` — FR-003.

**Checkpoint**: the API is a full writer for ingestion; batch + events are contract-tested.

---

## Phase 6: User Story 4 — Switch with a flag, prove identity, keep a way back (Priority: P1)

**Goal**: `ingest_via_api` (default off) selects the path; a record/replay harness proves direct and API paths identical; rollback is the flag.

**Independent Test**: a synthetic-fixture identity test (regression) AND a real-scrape record/replay on staging (broad + monitored, ~600 postings) — the latter is the gate for switching the flag on (SC-002 / FR-006).

### Tests for User Story 4

- [x] T025 [P] [US4] Write `tests/test_ingest_identity.py` — capture a synthetic scrape fixture (postings only), replay through direct path vs API path on DB copies, assert identical job ids/fields, company rows, `last_seen` touches, dropped-engaged set, and counts — FR-006 / SC-002.

### Implementation for User Story 4

- [x] T026 [US4] Add the `ingest_via_api` flag branch in `core/scrape.py` — `off` = current direct path; `on` = `GET /v1/ingest/plan` → run → `POST /v1/ingest/batch` (chunked, ≤ ~100 postings / ≤ 5 MB per request) + `POST /v1/ingest/events`; fail loudly (non-zero exit) when the API is unreachable or the token invalid — no silent fallback (US5-2) — FR-005.
- [x] T027 [US4] Add the record/replay harness `tests/run_ingest_replay.py` (capture a scrape to fixture; replay both paths; diff) — FR-006.

### Validation for User Story 4 (release-time gate)

- [x] T028 [US4] Record a **real** scrape on staging (broad + monitored, ~600 postings) to a fixture, then replay it through the direct path (DB copy A) and the API path (DB copy B); assert identical results. This real-scrape replay is the gate for switching `ingest_via_api` on — the synthetic fixture (T025) alone is not sufficient — FR-006 / SC-002.

**Checkpoint**: the flag switches paths; identity is provable (synthetic + real); off is unchanged.

---

## Phase 7: User Story 5 — A platform identity for ingestion (Priority: P1)

**Goal**: the platform principal's ingest-only token reaches `agent`/`worker` via env; it can never read/write user space.

**Independent Test**: isolation test — a `platform` token hits `/v1/ingest/*` but `read:me`/`write:me` return 403; `is_service=1` rows are excluded from user enumeration.

### Tests for User Story 5

- [x] T029 [P] [US5] Write the isolation test in `tests/test_isolation.py` (platform token → ingest only; service principal excluded from user-space reads) — FR-007/FR-008.

### Implementation for User Story 5

- [x] T030 [US5] Provide the platform token to the `agent` and `worker` containers via compose env (`.env`, never the repo) and document the in-network API base `http://api:8601` in `docs/infrastructure.md` — FR-007.

**Checkpoint**: the platform identity is provisioned and isolated; a real run can authenticate.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [x] T031 [P] Extend `tests/test_api_guard.py` allow-list for `api/routers/ingest.py` and update the OpenAPI snapshot in `tests/test_api_openapi.py` — FR-008.
- [x] T032 [P] Extend the spec-040 guard `tests/test_ingestion_scope.py` to ban profile-derived `effective_search_locations`/`greenhouse_boards` in scrapers and update the Greenhouse "may keep its scope read" note (now injected) — FR-002.
- [x] T033 [P] Constitution PATCH (`docs: amend constitution to v2.1.1 (ingestion via API)`): record that the Principle I gap (profile-derived locations/boards) is closed and Principle VIII's "à terme no DB access" is realised for ingestion, with a Sync Impact Report — FR-009.
- [x] T034 [P] Update `CLAUDE.md`, `docs/infrastructure.md` and `docs/roadmap-api.md` (step 7 status) — FR-009.
- [x] T035 Run `ruff check .` + `python -m pytest tests/` green, then `/speckit.analyze` (SC-005).

---

## Phase 9: Release & Production Switch (FR-010)

**Purpose**: ship behind the flag and switch only after explicit go. Deploys stay outside 07:00/11:00/16:00 UTC. `INGEST_VIA_API=off` is the rollback at every step.

- [x] T036 Staging on the **exact SHA**: `scripts/staging.sh up <full-sha>` (never the branch name); run the real-scrape identity gate (T028) + the US4-3 flag-on run (`core.main` / monitored-only + worker `scrape` task); capture results. Deploy outside 07:00/11:00/16:00 UTC.
- [x] T037 **STOP** — report the staging identity proof + flag-on results to Jean Claude; await an explicit **go** before merging.
- [x] T038 Merge to `main` ff-only from the exact SHA (`git merge --ff-only <full-sha>`; refuse non-ff).
- [x] T039 Deploy to prod with `INGEST_VIA_API=off` (`./scripts/deploy.sh`); the first post-deploy runs stay on the direct path. Deploy outside 07:00/11:00/16:00 UTC.
- [x] T040 Post-deploy checks: `tailscale serve status` still lists `tcp 8501` + `https 8443 /api`; `git -C /opt/job-agent rev-parse HEAD` equals the merged SHA.
- [x] T041 **STOP** — report the post-deploy checks to Jean Claude; await an explicit **go** before switching the flag.
- [x] T042 Switch `INGEST_VIA_API=on` in prod config (no redeploy).
- [x] T043 Verify the **next two scheduled runs** are `success` with usual volumes; rollback = switch back to `off` (no redeploy).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: after Setup — blocks all user stories.
- **US1 (Phase 3)**: after Foundational (needs the scope extension from T002/T003).
- **US2/US3 (Phases 4–5)**: after Foundational; independent of each other and of US1.
- **US4 (Phase 6)**: after US1 + US2 + US3 (the flag ties them together); T028 (real-scrape gate) also needs staging, so it lands at release time.
- **US5 (Phase 7)**: after Foundational (T005/T006); parallelizable with US1–US4.
- **Polish (Phase 8)**: after all stories.
- **Release (Phase 9)**: after Polish; sequential with STOP gates.

### Within Each User Story

- Tests first (T008, T015, T019, T025, T029), expected to fail, then implementation.
- US1: base scraper (T009) before the five scraper modules (T010–T013), then the orchestrator injection (T014).

### Parallel Opportunities

- Foundational T002–T007 touch different files/concerns → largely parallel (T007 depends on T002/T005 landing first).
- US1 scraper modules T010–T013 are different files → parallel after T009.
- US2 and US3 are independent → parallel; US5 parallel with everything after T005/T006.
- Polish T031–T034 are different files → parallel.

---

## Implementation Strategy

### MVP First (User Story 1)

1. Setup + Foundational (scope extension + platform principal).
2. US1 (scrapers pure + orchestrator injection) — **STOP and VALIDATE**: guard green, direct path identical.
3. Then US2 (plan), US3 (batch/events), US4 (flag + identity), US5 (identity), Polish.
4. Release: ship behind the flag, prove identity on staging, switch only after explicit go.

### Incremental Delivery

Each story is independently verifiable. US1 removes the DB access; US2/US3 add the endpoints; US4 gates the switch behind the flag with the identity proof; US5 provisions the identity; Polish aligns docs/guards. The app stays on the direct path until the release phase flips the flag.

---

## Notes

- `[P]` = different files, no dependencies.
- No new dependency; no schema change beyond `users.is_service` + the scope JSON.
- "No SQL in `api/`" and "DB via `JobStorage` only" hold: the ingest router calls `JobStorage.ingest_batch`; the guard (T031) enforces it.
- `ingest_batch` must commit in ~50-posting `BEGIN IMMEDIATE` sub-chunks (never a read→write upgrade) — the 2026-10-27 hotfix lesson.
- The synthetic fixture test (T025) is a regression test, not the switch gate; only the real-scrape staging replay (T028) gates switching on.
- Release tasks (T036–T043) are sequential with explicit **STOP** points at T037 and T041 awaiting Jean Claude's go; every deploy is outside 07:00/11:00/16:00 UTC.
