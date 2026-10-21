# Research — Agents through the API (spec 044)

The spec was clarified before planning (Q1 server-side scoring via `score_job`,
Q2 no DB access from the agent, Q3 a new `ingest` scope), so the only open
decisions are implementation-shape choices. Each is resolved below.

## R1 — Agent HTTP client library

- **Decision**: `core/cv_agent/api_client.py` uses **`httpx`** (synchronous), with
  an explicit timeout and a bearer header; errors map to clear messages (401/403 →
  "bad token/scope", 404 → "job/profile not found", 5xx/network → "API unreachable").
  The token is read from `JOB_AGENT_API_TOKEN` or a local credentials file (mode
  `0600`) and never logged.
- **Rationale**: `httpx>=0.27.0` is already a runtime dependency, has clean
  JSON + timeout + error handling, and is the same client the FastAPI `TestClient`
  (FR-009) is built on — one library on both sides of the tests.
- **Alternatives considered**: `requests` (also a dep, equally fine but no
  streaming for the future SSE follow); `urllib.request` (stdlib, used by
  `scripts/health_check.py`, but more boilerplate for JSON/bearer/errors).

## R2 — `score_job` task runs the existing single-job path

- **Decision**: `score_job` maps to
  `[sys.executable, "-u", "-m", "core.score", "--job", job_id, "--profile", profile_id]`.
  A new `--job` flag in `core/score.py` resolves the profile, then calls the
  existing `extract_one` + `score_one` from `core/job_actions.py` for that one job —
  exactly the per-job functions the tracker and `prepare_one` already use. No new
  scoring/extraction logic; timeout 10 min (single job + LLM).
- **Rationale**: `core/job_actions.score_one` already implements "extract-if-needed
  then evaluate" for one job; the batch `core.score` already reuses it. The `--job`
  flag is a thin single-job entry point over that path, not a rewrite.
- **Alternatives considered**: a new worker command calling `score_one` directly
  (would bypass the `core.score` CLI and duplicate profile resolution); extending the
  existing `score` task type with an optional `job_id` param (blurs the batch/single
  distinction and the dedup key).

## R3 — `POST /v1/jobs` lives in the catalogue router, `ingest` scope

- **Decision**: `POST /v1/jobs` (scope `ingest`) in `api/routers/catalogue.py`. It
  computes `JobPosting.id` from the request, checks existence via `get_catalogue_job`
  (404 → `{job_id, created:false}` unchanged), else constructs the `JobPosting` and
  calls `save_unscored` (returns `{job_id, created:true}`). `save_unscored` moves from
  `FORBIDDEN_METHODS` to `ALLOW_LIST` in `tests/test_api_guard.py`; `save_scored` stays
  forbidden (no client writes a score).
- **Rationale**: jobs are shared catalogue (Constitution X); the catalogue router owns
  `/v1/jobs`. `ingest` is a separate, narrower grant than `write:catalogue` (which stays
  company-facts-only) and will be reused by step 7.
- **Alternatives considered**: a new `ingest` router (more surface for one route);
  folding submission under `write:catalogue` (too broad — a job-submitting token must
  not also edit company facts).

## R4 — `PUT /v1/me/jobs/{job_id}/application` reuses `save_application`

- **Decision**: `PUT /v1/me/jobs/{job_id}/application` (scope `write:me`) in
  `api/routers/me.py`, mirroring `set_job_status`: 404 if `get_job_for_prepare(job_id)`
  is None, else `save_application(job_id, analysis, cover_letter)` and return
  `get_application(job_id)`. `save_application` joins the API guard `ALLOW_LIST`.
- **Rationale**: the tracker already stores the application through this method; the
  route is the API equivalent with byte-identical effect (contract-tested).
- **Alternatives considered**: a dedicated `applications` router (unnecessary — the
  resource is `/me/jobs/{id}/application`, one method).

## R5 — Boundary guard tightening (name-level, not just module-level)

- **Decision**: `tests/test_cv_agent_boundary.py` drops `core.scorer` and
  `core.storage` from `ALLOWED_CORE_MODULES`, keeps `core.llm`, `core.paths`,
  `core.models`, `core.geo_ch`, `core.profiles`, and adds a **name-level** rule:
  within allowed modules the agent must not reference `core.paths.DB_PATH` nor
  `core.profiles.load_active_profile`, and no `core/cv_agent/` file may `import
  sqlite3`. A negative fixture covers each forbidden form.
- **Rationale**: Q2 is strict — "no DB access from the agent". A module allow-list
  alone can't forbid `DB_PATH` from the otherwise-allowed `core.paths`, or
  `load_active_profile` from `core.profiles`; the LangGraph checkpointer (allowed,
  stays local) is `langgraph.checkpoint.sqlite`, not a raw `sqlite3` import.
- **Alternatives considered**: split `core.paths`/`core.profiles` into DB and non-DB
  halves (larger refactor, violates V); leave the module allow-list as-is (fails Q2).

## R6 — Profile seeding is removed; missing profile → clear error

- **Decision**: `--job`/`--paste` require the active profile to exist on the server;
  `GET /v1/me` returns it. If absent, the agent stops before any LLM spend with an
  explicit message (no `upsert_profile` from the agent).
- **Rationale**: a client must not write profile criteria (a user-space write it
  doesn't own today); seeding was a DEV convenience now out of scope.
- **Alternatives considered**: keep a client-side `upsert_profile` via the API
  (violates Q2's spirit — the agent would write profile data it only needs to read).

## R7 — Output folder comes from `CV_OUTPUT_DIR`, not the DB

- **Decision**: the agent reads the CV output folder from the `CV_OUTPUT_DIR` env
  (default unchanged), replacing `db.get_config("cv.output_dir")`.
- **Rationale**: it is a Mac-side rendering setting, not shared data — never belonged
  in the DB; reading it from env is consistent with `core.paths`'s location rules.
- **Alternatives considered**: keep reading `cv.output_dir` from the DB via a new API
  route (over-engineering for a local setting).
