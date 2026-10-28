# Quickstart — Ingestion through the API (spec 045)

How to validate the feature end-to-end. Implementation detail lives in `tasks.md`;
the endpoint contract in `contracts/ingest-api.md`; the payload shape in
`data-model.md`.

## Prerequisites

- Python 3.11 venv with dev extras (`pip install -e ".[dev]"`).
- Local read-only API on `127.0.0.1:8601` (`uvicorn api.main:app --port 8601`), or a
  `TestClient` for contract tests.
- A local `data/jobs.db` (empty on the Mac) — the identity test uses copies.

## Local gates

```bash
ruff check .                     # E9/F63/F7/F82 clean
python -m pytest tests/ -q       # full suite green
```

## Scenario 1 — Scrapers are pure (SC-001)

```bash
python -m pytest tests/test_scraper_purity.py -q
```

Expected: the guard fails any module under `core/scrapers/` that imports `core.storage`,
`JobStorage`, `load_active_profile`, or calls `get_config`/`set_config`. Each scraper is
unit-tested with an injected plan and a fake HTTP layer.

## Scenario 2 — Replay identity (SC-002 / US4-2)

Synthetic-fixture regression test:

```bash
python -m pytest tests/test_ingest_identity.py -q
```

Real-scrape identity gate (the one that gates switching the flag on): on staging,
record a real scrape (broad + monitored, ~600 postings) to a fixture, then replay:

```bash
python -m tests.run_ingest_replay --fixture <captured.json>
```

Expected: replaying the fixture through the direct path (DB copy A) and the API path
(DB copy B) yields identical job ids/fields, company rows, `last_seen` touches,
dropped-engaged set, and counts. The synthetic fixture alone is **not** sufficient —
only this real-scrape replay gates switching `INGEST_VIA_API` on.

## Scenario 3 — Flag off is unchanged

```bash
python -m core.scrape        # ingest_via_api=off → today's direct path
```

Expected: identical output to before the change.

## Scenario 4 — Flag on through the API (SC-003, staging)

```bash
# staging: ingest_via_api=on; token in .env; API at http://api:8601
python -m core.scrape
python -m core.worker        # scrape task also works through the API
```

Expected: the run completes; `GET /v1/ingest/plan` → `POST /v1/ingest/batch` →
`POST /v1/ingest/events`; the tracker shows the same new jobs the replay predicted.

## Scenario 5 — Production switch (SC-004)

- Validate the **exact SHA** on staging, run the real-scrape identity gate (Scenario 2)
  + the flag-on run (Scenario 4). **STOP** for go.
- Merge `main` ff-only from the exact SHA.
- Deploy with `ingest_via_api=off`; post-deploy checks (`tailscale serve status` lists
  `tcp 8501` + `https 8443 /api`; server HEAD equals the merged SHA). **STOP** for go.
- Flip `ingest_via_api=on`; the **next two** scheduled runs `success` with usual volumes.
- Rollback = flip back to `off` (no redeploy). Deploys outside 07:00/11:00/16:00 UTC.

## Scenario 6 — API unreachable fails loudly (US5-2)

With `ingest_via_api=on` and the API down, the scrape exits non-zero with a clear error
in the cron log and health check — no silent fallback to the direct path.

## Definition of done

Guard green (SC-001) · replay identity (SC-002) · staging flag-on run completes (SC-003)
· prod flag-on runs success with usual volumes (SC-004) · next nightly cron OK · tracker
smoke test (feed, status change, job detail) · parity script green.
