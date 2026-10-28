# Research — Ingestion through the API (spec 045)

Phase 0 decisions. Each entry: decision, rationale, alternatives rejected.

## R1 — Platform principal (Q3)

- **Decision**: Add `users.is_service INTEGER NOT NULL DEFAULT 0` (migration) and a
  `platform` row — `id='platform'`, `display_name='Platform ingestion'`,
  `is_service=1`, no `active_profile_id`. Ingest tokens bind to it via
  `create_api_token(..., user_id='platform')`. User-space enumeration never returns a
  `is_service=1` row.
- **Rationale**: Q3 decided a non-human principal; the flag makes "never reads/writes
  user space" enforceable by a guard rather than by convention (Constitution VII/X).
- **Alternatives**: convention-only `platform` row (no flag) — less explicit, harder to
  guard; issuing to the default user — mixes a person and a service (rejected).

## R2 — Ingestion scope extension (Q2)

- **Decision**: Extend `config.ingestion_scope` JSON with `search_locations` (list[str])
  and `greenhouse_boards` (list[str]). `get_ingestion_scope()` returns them with the
  same safe-default coercion; `set_ingestion_scope()` accepts them; a one-time seed
  copies the current active profile's `effective_search_locations()` and
  `greenhouse_boards` into the scope so day one is identical. `scripts/ingestion_scope.py`
  gains `--set-locations` / `--set-boards` (prose path).
- **Rationale**: closes the Constitution I gap the spec found (LinkedIn/Indeed locations
  and Greenhouse boards still profile-derived after spec 040).
- **Alternatives**: keep them profile-derived and pass through the plan — keeps the gap
  (rejected by Q2).

## R3 — Scraper purification contract

- **Decision**: `BaseScraper` drops `storage`; its constructor takes the injected plan
  slice (search locations / boards / slugs / enabled state). `is_enabled()` is decided
  by the orchestrator from the plan; auto-disable is reported back as an event
  (`POST /v1/ingest/events`). A guard test fails any module under `core/scrapers/` that
  imports `core.storage`/`JobStorage`/`load_active_profile` or calls `get_config`/
  `set_config` (SC-001 / FR-001).
- **Rationale**: Constitution VIII "à terme" no DB access except the ingestion endpoint;
  pure functions run anywhere and are unit-testable with a fake HTTP layer.
- **Alternatives**: keep storage for config reads only — leaves the gap and the guard
  untestable.

## R4 — Server-side batch transaction

- **Decision**: new `JobStorage.ingest_batch(postings) -> dict` — load the engaged-key
  set once in memory (`get_engaged_job_keys()`, already the union across all users), then
  per ~50-posting `BEGIN IMMEDIATE` sub-chunk open one transaction and, per posting, compute
  `JobPosting.id`, apply engaged dedup + `last_seen` touch, `upsert_company`, and
  `save_unscored` (new) or `touch_many` (existing). Returns
  `{new, existing, dropped_engaged}` plus per-posting outcomes.
- **Rationale**: one writer, one set of rules (FR-004 "no new rules"); short
  transactions avoid the WAL read→write-upgrade `SQLITE_BUSY` (hotfix lesson); keeps
  "no SQL in `api/`" (the router calls the method).
- **Alternatives**: reuse `save_unscored` one call per posting — N autocommit writes,
  no per-chunk transaction, slower under the always-on worker.

## R5 — Body limit & chunking

- **Decision**: `RequestSizeLimitMiddleware` raises the cap to 5 MB for
  `POST /v1/ingest/batch` only (route-specific exemption); the orchestrator chunks to
  ≤ that and ≤ ~100 postings per request.
- **Rationale**: ~600 postings with descriptions ≈ a few MB; the ≤ ~100-posting request
  bound keeps per-request memory low and the ~50-posting sub-chunk keeps each
  `BEGIN IMMEDIATE` short.
- **Alternatives**: raise the global limit — weakens the 64 KB guard for every other
  route.

## R6 — Flag semantics (FR-005)

- **Decision**: config key `ingest_via_api`, read via `get_config("ingest_via_api")`,
  default `"off"`. `off` = today's direct path untouched; `on` = plan + batch. The
  spec's `INGEST_VIA_API` spelling normalises to the codebase's lowercase dotted config
  key.
- **Rationale**: Constitution XI flag with rollback without redeploy; consistent with
  `ingestion_scope`, `freshness_days`, `scraper.<slug>.enabled`.
- **Alternatives**: env var only — deviates from the `config` store and `get_config`.

## R7 — Router & endpoints

- **Decision**: new `api/routers/ingest.py` (one router per domain):
  `GET /v1/ingest/plan`, `POST /v1/ingest/batch`, `POST /v1/ingest/events`, all gated by
  the existing `require_scope("ingest")` (spec 044 already defined the scope and
  `POST /v1/jobs`). Reuse `api/schemas`, `api/serializers`, `api/write_log`.
- **Rationale**: Constitution "monolithe modulaire"; the `ingest` scope already exists.
- **Alternatives**: fold into `catalogue.py` — mixes domains and read/write scopes.

## R8 — Plan payload

- **Decision**: `GET /v1/ingest/plan` returns a single plan dict:
  `title_families`, `exclude`, `remote_or_hybrid`, `search_locations`, `greenhouse_boards`
  (from scope), `freshness_days`, `enabled_sources` (scraper slug → bool from config),
  `monitoring_gates` (per-ATS `monitoring.ats.<provider>.enabled`),
  `monitored_targets` (union of watched companies with `ats_provider`/`ats_identifier`),
  `board_slugs` (lever/workable slugs + greenhouse watching boards from
  `get_watching_companies_by_method`).
- **Rationale**: everything a run needs, no profile read, no user-space data (engaged-union
  stays server-side for dedup).
- **Alternatives**: return only the scope — orchestrator would still need direct DB reads
  for gates/targets.

## R9 — Record/replay identity (FR-006 / US4-2)

- **Decision**: a dev/test harness captures one real scrape's postings to a JSON fixture
  (postings only, no DB), then replays the fixture through the direct path (DB copy A) and
  the API path (DB copy B via `TestClient`), asserting identical job ids/fields, company
  rows, `last_seen` touches, dropped-engaged set, and counts.
- **Rationale**: the proof required before switching production (Constitution VI/XI);
  deterministic and re-runnable.
- **Alternatives**: manual A/B on staging only — not reproducible, not a regression test.
