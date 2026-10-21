# Quickstart — Agents through the API (spec 044)

Validation guide. Local runs use an in-memory FastAPI `TestClient` and a mocked LLM —
no real server, no network (FR-009). The staging flow (US5) is the only one that hits
a live API.

## Prerequisites

- `.venv` active; `pip install -e ".[dev]"`.
- A token with `read:me, write:me, tasks:run, ingest` for the live runs.

## §1 Server surface (unit/contract, in-memory)

```bash
python -m pytest tests/test_api_contract.py tests/test_api_auth.py -q
python -m pytest tests/test_tasks_api.py -q      # score_job rides the same surface
python -m pytest tests/test_api_guard.py -q      # allow-list: save_unscored + save_application
```

Proves:
1. `POST /v1/jobs` → `{job_id, created:true}` for a new posting, `created:false` for
   an existing one; `422` without title/company when no URL; `403` without `ingest`.
2. `score_job` enqueue → `202` + worker command `--job <id> --profile <pid>`; `404`
   for a foreign profile or unknown job; dedup returns the existing task.
3. `PUT /v1/me/jobs/{id}/application` writes the same row `save_application` writes
   (contract test, LLM mocked).

## §2 Boundary is enforced

```bash
python -m pytest tests/test_cv_agent_boundary.py -q
```

Proves `core/cv_agent/` imports no `core.storage`, `JobStorage`, `sqlite3`,
`core.scorer`, nor `core.paths.DB_PATH`; `core.llm`, `core.paths` (non-DB),
`core.models`, `core.geo_ch`, and profile parsing from `core.profiles` (not
`load_active_profile`) are allowed. A negative fixture fails for each forbidden form.

## §3 Agent flows (TestClient + mocked LLM)

```bash
python -m pytest tests/test_cv_agent_gate.py tests/test_cv_agent_paste.py -q
```

Proves `--job <id>` reads job/score/profile via the API; `--paste`/URL submits
(`POST /v1/jobs`), enqueues `score_job`, waits bounded, then continues; "skip" →
`archived`; approve → `PUT …/application` + `ready`; missing profile → clear error
before LLM spend.

## §4 No DB access from the agent (guard)

```bash
python -m pytest tests/test_cv_agent_boundary.py -q   # same as §2
grep -rn "JobStorage\|sqlite3\|DB_PATH\|core\.scorer" core/cv_agent/ | grep -v api_client.py || echo OK
```

## §5 Full suite

```bash
python -m pytest tests/ -q
```

## §6 Staging round-trip (US5 — after implementation, before merge)

On verva: `scripts/staging.sh up <full-sha>` (tracker + API + worker). From the Mac,
with `JOB_AGENT_API_URL` pointing at the staging API and a staging token:

1. `--job <id>` → produces a CV; `--approve` → application + `ready` visible in the
   staging tracker.
2. `--paste --auto` → posting submitted, `score_job` completes, the job appears scored
   in the staging tracker, flow continues to review.
3. "skip" → job `archived` in the staging tracker.
4. `scripts/staging.sh down` removes tracker, API **and worker**.
