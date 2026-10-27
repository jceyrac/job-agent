---
description: "Task list for feature implementation — Agents through the API (spec 044)"
---

# Tasks: Agents through the API — the cv_agent stops touching the database (step 6b)

**Input**: Design documents from `/specs/044-agents-via-api/`
(`plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/agents-api.md`,
`quickstart.md`)

**Prerequisites**: plan.md, spec.md (5 user stories, all P1), research.md (R1–R7),
data-model.md, contracts/

**Tests**: REQUIRED — the spec mandates them (SC-001, SC-002, FR-005, FR-009):
contract tests for the two new routes, the `score_job` task, the boundary guard, and
the API guard allow-list. Agent tests run against a FastAPI `TestClient` (in-memory DB)
with a mocked LLM.

**Format**: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

**Purpose**: confirm the runtime assumption from the plan (no new dependency).

- [X] T001 Confirm `httpx>=0.27.0` is present in `requirements.txt` and importable in `.venv` (used by the new `core/cv_agent/api_client.py`); no new dependency to add.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: server-side shared building blocks that US1 and US2 both depend on.
**⚠️ No user-story route work until this phase is complete.**

- [X] T002 [P] Add `ingest` to `API_TOKEN_SCOPES` in `core/storage.py`
- [X] T003 [P] Add `ingest` to the `--scopes` choices/help in `scripts/api_token.py`
- [X] T004 [P] Add `ingest = require_scope("ingest")` in `api/deps.py`
- [X] T005 [P] Add `score_job` to `core/tasks.py` (`TASK_TYPES`, `TASK_TIMEOUTS` → 600 s, `command_for` → `--job <id> --profile <id>`)
- [X] T006 [P] Add API models `JobCreate`, `JobCreated`, `ApplicationUpdate`, `ScoreJobParams` in `api/schemas.py` (`extra="forbid"`)
- [X] T007 Add single-job `--job <id>` mode to `core/score.py`, reusing `extract_one` + `score_one` from `core/job_actions.py` (no new scoring logic)
- [X] T008 [P] Update the API guard allow-list in `tests/test_api_guard.py`: move `save_unscored` and add `save_application` to `ALLOW_LIST`; keep `save_scored` in `FORBIDDEN_METHODS`

**Checkpoint**: `ingest` scope + `score_job` type + `--job` mode + models + guard
allow-list are in place and unit tests pass.

---

## Phase 3: User Story 1 — Submit a posting and get it scored by the server (P1) 🎯 MVP

**Goal**: `POST /v1/jobs` (ingest) and the `score_job` task, so the server extracts
and scores a submitted posting and no client writes a score.

**Independent Test**: `python -m pytest tests/test_api_ingest.py tests/test_tasks_api.py -q`

### Tests for User Story 1 (write FIRST, ensure they FAIL)

- [X] T009 [P] [US1] Contract test for `POST /v1/jobs` in `tests/test_api_ingest.py` — new job → `{job_id, created:true}` (id == `JobPosting.id`), existing job → `created:false` (unchanged), `422` without title/company when no URL, `403` without `ingest`, no LLM/`save_scored` call
- [X] T010 [P] [US1] Test for `score_job` enqueue in `tests/test_tasks_api.py` — `202`, worker command `--job <id> --profile <id>`, dedup returns the existing active task, `404` for a foreign profile or unknown job, `403` without `tasks:run`

### Implementation for User Story 1

- [X] T011 [US1] Implement `POST /v1/jobs` in `api/routers/catalogue.py` (compute `JobPosting.id`, `get_catalogue_job` for existence, `save_unscored` if new)
- [X] T012 [US1] Implement `score_job` enqueue validation in `core/storage.py` `enqueue_task` (require `job_id` + `profile_id`, `_require_profile` ownership, job existence → `404`)

**Checkpoint**: US1 independently functional — a posting can be submitted and scored
via the task queue.

---

## Phase 4: User Story 2 — Record the application through the API (P1)

**Goal**: `PUT /v1/me/jobs/{job_id}/application` writes the same row as the tracker's
`save_application`.

**Independent Test**: `python -m pytest tests/test_api_contract.py -q`

### Tests for User Story 2 (write FIRST)

- [X] T013 [P] [US2] Contract test for `PUT /v1/me/jobs/{id}/application` in `tests/test_api_contract.py` — response is the stored application; row equals what `save_application` writes on a copy of the same DB; `404` for a job not visible to the user

### Implementation for User Story 2

- [X] T014 [US2] Implement `PUT /v1/me/jobs/{job_id}/application` in `api/routers/me.py` (`get_job_for_prepare` 404 guard, then `save_application`, return `get_application`)

**Checkpoint**: US2 independently functional — the application is recorded via the API.

---

## Phase 5: User Story 3 — The cv_agent is an API client (P1)

**Goal**: the cv_agent works exactly as today from the Mac, but reads/writes only via
the API (`api_client.py`), and its output lands in production.

**Independent Test**: `python -m pytest tests/test_cv_agent_gate.py tests/test_cv_agent_paste.py -q`

### Implementation for User Story 3

- [X] T015 [US3] Create `core/cv_agent/api_client.py` — httpx client: base URL from `JOB_AGENT_API_URL`, token from `JOB_AGENT_API_TOKEN` or a local credentials file (mode `0600`), explicit timeouts, error mapping (401/403/404/5xx/network), token never logged
- [X] T016 [US3] Replace every `JobStorage` use in `core/cv_agent/cli.py` — active profile via `GET /v1/me`, drop `upsert_profile`/seeding, clear error when no active profile exists
- [X] T017 [US3] Replace every `JobStorage` use in `core/cv_agent/nodes.py` — job/score read via `GET /v1/me/jobs/{id}`, paste/URL → `POST /v1/jobs` + `score_job` with bounded wait, skip → `PUT …/status` `archived`, approve → `PUT …/application` + `PUT …/status` `ready`, output folder from `CV_OUTPUT_DIR`
- [X] T018 [P] [US3] Re-point the agent tests in `tests/test_cv_agent_gate.py` / `tests/test_cv_agent_paste.py` at a FastAPI `TestClient` (in-memory DB) with a mocked LLM; cover US3 scenarios 2–9 (job read, submit+score, skip, approve, missing profile, unreachable API/bad scope, `--auto`/`--resume`)

**Checkpoint**: the agent is a pure API client; its guard suite and agent tests are green.

---

## Phase 6: User Story 4 — The boundary is enforced (P1)

**Goal**: prove the agent has no DB access and the API guard covers the new writes.

**Independent Test**: `python -m pytest tests/test_cv_agent_boundary.py tests/test_api_guard.py -q`

### Implementation for User Story 4

- [X] T019 [US4] Tighten `tests/test_cv_agent_boundary.py` — drop `core.scorer` and `core.storage` from the module allow-list; add name-level forbids (`core.paths.DB_PATH`, `core.profiles.load_active_profile`, `import sqlite3`) with a negative fixture per forbidden form (SC-001)

**Checkpoint**: `core/cv_agent/` has no DB access and the API allow-list permits exactly
the new writes (`save_unscored`, `save_application`) — not scoring.

---

## Phase 7: User Story 5 — Validated on staging, then used for real (P1) 🔒 RELEASE-GATED

**Goal**: prove the whole feature end-to-end on staging, then in production.

> **⛔ NOT executed during `/speckit.implement`.** This phase runs only after the
> implementation is committed and the user gives an explicit "go". STOP before merge
> and before deploy (see release notes).

### Staging validation (on the exact SHA)

- [X] T020 [US5] On verva: `scripts/staging.sh up <full-sha>` (tracker + API + worker); from the Mac with a staging token (`read:me, write:me, tasks:run, ingest`) and `JOB_AGENT_API_URL` → staging: run `--job` + `--approve` (application + `ready` in staging tracker), `--paste --auto` (posting + `score_job` completes + scored in staging tracker), and a "skip" (`archived` in staging tracker); then `scripts/staging.sh down` removes tracker, API **and worker**

### Release (each gate requires the user's explicit "go")

- [X] T021 [US5] **STOP before merge** — report staging results to the user; no merge without an explicit "go"
- [X] T022 [US5] `git merge --ff-only <full-sha>` onto `main` (refuse non-ff); push
- [X] T023 [US5] **STOP before deploy** — no deploy without an explicit "go"; deploy only outside 07:00/11:00/16:00 UTC
- [X] T024 [US5] `./scripts/deploy.sh` on verva; confirm `git -C /opt/job-agent rev-parse HEAD` == merged SHA; `tailscale serve status` still lists `tcp 8501` and `https 8443 /api`
- [X] T025 [US5] Post-deploy checks: `/api/health` reports the SHA, worker heartbeat fresh, tracker reachable, next cron run OK, and the health_check (T028) reaches the API via `http://api:8601` from inside the containers — "next cron run OK" deferred to a separate fix (pre-existing cron "database is locked" in `purge_stale_jobs`, unrelated to 044)
- [ ] T026 [US5] First real agent run against **production** by the user — application visible in the prod tracker with status `ready`, no manual update

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T027 [P] Regenerate the OpenAPI snapshot in `tests/test_api_openapi.py` (and its snapshot file) for the two new routes + `ingest` scope + `score_job`
- [X] T028 [P] Fix the in-container health check to reach the API by service name — set `JOB_AGENT_API_URL=http://api:8601` in the compose environment of the containers that run `health_check`, document the host-side alternative, and add a test asserting the in-container default is the service name (ships with 044; not a 043 regression)
- [X] T029 [P] Docs — `CLAUDE.md` (cv_agent commands + `JOB_AGENT_API_URL`/`JOB_AGENT_API_TOKEN`/`CV_OUTPUT_DIR`), the cv_agent usage notes, `docs/roadmap-api.md` step 6b wording per Q2, and mark the spec-036 DEV-DB limitation resolved
- [X] T030 Run `python -m pytest tests/ -q` and the `quickstart.md` §1–§5 validation; confirm the parity/guard suites stay green

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)**: none — immediate.
- **Foundational (P2)**: depends on Setup — **BLOCKS all user stories**.
- **US1 (P3)** and **US2 (P4)**: depend on Foundational (routes need `ingest`, `score_job`, models, guard allow-list). Independently testable.
- **US3 (P5)**: depends on US1+US2 (the agent calls the new endpoints). The `api_client.py` module (T015) has no code dependency and may be started in parallel with US1/US2, but the agent tests (T018) need the routes.
- **US4 (P6)**: independent — only `tests/test_cv_agent_boundary.py`; must land with or before US3's `nodes.py`/`cli.py` edits so the tightened guard covers them.
- **US5 (P7)**: release-gated — after all code tasks are committed and the user gives "go".
- **Polish (P8)**: after all desired stories.

### Within each user story

- Tests FIRST and FAILING → implementation → checkpoint.
- Foundational `--job` mode (T007) before `score_job` enqueue validation (T012).
- Agent `api_client.py` (T015) before `cli.py`/`nodes.py` (T016/T017); guard tightening (T019) alongside them.

### Parallel Opportunities

- Foundational T002–T006 and T008 touch different files → parallel.
- US1 tests (T009, T010) parallel; US2 test (T013) parallel with them.
- US3 `api_client.py` (T015) can proceed in parallel with US1/US2 once Foundational is done.

---

## Parallel Example

```bash
# Foundational — independent files, one pass:
Task: "Add `ingest` to API_TOKEN_SCOPES in core/storage.py"
Task: "Add `score_job` to TASK_TYPES/TASK_TIMEOUTS/command_for in core/tasks.py"
Task: "Add API models JobCreate/JobCreated/ApplicationUpdate/ScoreJobParams in api/schemas.py"
Task: "Update the API guard allow-list in tests/test_api_guard.py"

# US1 tests together (fail first):
Task: "Contract test for POST /v1/jobs in tests/test_api_ingest.py"
Task: "Test for score_job enqueue in tests/test_tasks_api.py"
```

---

## Implementation Strategy

### MVP First (US1)

1. Setup + Foundational (T001–T008) → foundation ready.
2. US1 (T009–T012) → a posting can be submitted and scored server-side.
3. **STOP and VALIDATE** `pytest tests/test_api_ingest.py tests/test_tasks_api.py`.

### Incremental Delivery

1. Foundational → US1 → US2 → US3 → US4 → Polish, each green at its checkpoint.
2. US5 (staging) is a separate release gate, run only after the user says "go".

---

## Notes

- `[P]` = different files, no dependency; `[Story]` maps to a spec user story.
- Commit after each task or logical group (message ends with the Co-Authored-By line).
- The release flow (US5) **STOPs before merge and before deploy** — no merge/deploy
  without an explicit user "go"; the staging token is created by the user on the
  staging volume and never printed in logs; deploy outside 07:00/11:00/16:00; no
  production write token is created by default.
