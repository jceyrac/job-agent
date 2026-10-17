# Quickstart — Task Queue, Worker and Live Progress (spec 043)

Validation guide. Implementation details live in `data-model.md`, `contracts/tasks-api.md` and
`tasks.md`; this file proves the feature end-to-end. Run locally first, then staging, then prod.

## Prerequisites

```bash
pip install -e ".[dev]"          # fresh venv
python -m pytest tests/ -q       # green baseline before starting
```

## §1 — Local: storage layer

```bash
python -m pytest tests/test_tasks.py -q
```
Covers: enqueue + dedup (a second identical enqueue returns the same row, no new one); claim is
atomic and ordered; events append/read with `after_seq`; finish/cancel transitions; a foreign
`user_id`'s task is invisible (`get_task` → `None`); `score` for a foreign profile raises
`ProfileNotFoundError`.

## §2 — Local: worker command map + lifecycle

```bash
python -m pytest tests/test_worker.py -q
```
Covers: `core/tasks.py` maps each type to the exact argv (assert the full argv list);
`recover_interrupted` flips `running`→`failed:interrupted`; timeout → `error="timeout"`; cancel
sets `cancel_requested` and the worker terminates.

## §3 — Local: API routes + SSE

```bash
python -m pytest tests/test_tasks_api.py -q
```
Covers: `POST` enqueues and answers `202`; `tasks:run` vs `tasks:pipeline` scoping (403 on
scrape without `tasks:pipeline`); `GET`/`GET {id}` user-scoping (foreign → 404); SSE stream
yields `id:`/`data:` lines and closes with a `status` event; JSON fallback paginates by
`after_seq`; cancel on queued (→`cancelled`) and running (→`cancel_requested`).

## §4 — Local: guard tests

```bash
python -m pytest tests/test_api_guard.py tests/test_tracker_no_subprocess.py -q
```
`api/` imports no `subprocess` nor the pipeline entry points; `tracker_views/` imports no
`subprocess`/`threading` (the resolved known violation).

## §5 — Local: end-to-end (manual)

1. Start a worker: `python -m core.worker` (foreground, against `data/jobs.db`).
2. In another shell: `streamlit run tracker.py`. Click **Fetch / Score / Extract** on Jobs and
   **Scrape / Monitoring / Scoring / Re-extract** on Settings. Each shows the queue progress,
   survives a page reload, and a second click shows the existing task instead of a new one.
3. `python -m core.main` while a task is queued → the task waits (lock) then runs; the tracker
   shows "waiting for the scheduled pipeline" during the wait.
4. Stop the worker, enqueue a task → the tracker shows "waiting for the worker".

## §6 — Staging (on verva, exact SHA)

```bash
scripts/staging.sh up <full-sha>        # now also starts a staging worker
# tracker :8502, API https://<verva>.ts.net:8443/api, worker on the staging volume
scripts/staging.sh run python -m scripts.health_check --db /app/data/jobs.db   # worker heartbeat reported
```

- **Parity first**: fingerprint the staging copy against the prod image (byte-identical) **before**
  any task runs — `scripts/fingerprint` + `compare_fingerprints`.
- **Each button** produces a task that completes with the same effect as today (e.g. a `score`
  task → the same new scores as a direct `core.score --profile …` on another copy of the DB).
- **SSE through the tailnet**: `curl -N -H "Authorization: Bearer $STAGING_TOKEN" \
  https://<verva>.<tailnet>.ts.net:8443/api/v1/me/tasks/<id>/events` → lines arrive
  incrementally (no proxy buffering).
- **Cancel**: on a queued task (→`cancelled`) and on a running task (terminated →`cancelled`).
- **Lock**: with a task queued, run `scripts/staging.sh run python -m core.main` → the task
  waits for the lock, then runs.
- `scripts/staging.sh down` removes the tracker, API **and worker** (no leftover container/image/
  volume/worktree/`tailscale serve` entry).

## §7 — Release checks (prod)

- `/api/health` `version` == the staged SHA.
- `worker` container up; `docker logs job-worker` shows the claim→run→finish loop.
- `docker exec job-tracker python -m scripts.health_check --url http://localhost:8501
  --db /app/data/jobs.db` reports worker heartbeat age.
- A cheap prod task (small `score` or `extract`) completes; the tracker buttons work.
- Next cron `full` run `success`; no overlapping pipeline run (lock exercised).
