# Research — spec 041 (API v1 read-only)

Phase 0 findings. Every source location was re-verified against `c04998c` +
the uncommitted `docs/roadmap-api.md` step-5 edit (2026-10-15). Decisions are
marked **Decision**. The spec's three clarifications (Q1 HTTPS/`/api`, Q2 full
endpoint table, Q3 tailnet-only with FR-013–017 foundations) are treated as
resolved and are not re-opened.

---

## 1. `read:catalogue` needs three new read methods — no existing method returns user-free facts

**Finding.** The spec's `read:catalogue` routes promise "no user-space field
ever present" (US3-3), but every job/company reader on `JobStorage` returns
user-space data:

- `get_all_for_tracker(profile_id, …)` joins `job_scores` (per-profile) and
  `job_tracking` (per-user): returns `score`, `reason`, `tracked_status`,
  `status`, `notes`.
- `get_jobs_for_company(company_id)` returns the same user-space fields plus
  `best_profile_id`.
- `get_job_for_prepare(job_id)` returns `status` (the user's tracking status).
- `get_company_by_id(company_id)` returns `status`/`notes`/`monitored` coalesced
  from `user_companies` (user space), plus user-space aggregates
  `job_count`/`contact_count`/`interaction_count`/`last_interaction_at`.

There is **no** method that returns the shared-catalogue job facts or the
shared company facts alone.

**Decision.** Add three read-only methods to `core/storage.py` (in scope —
roadmap step 5 concerns it; no SQL moves into `api/`):

- `get_catalogue_jobs(*, company_id=None, source=None, posted_after=None) -> list[dict]`
- `get_catalogue_job(job_id) -> dict | None`
- `get_company_facts(company_id) -> dict | None`

Their exact keys are fixed in `data-model.md` §3 and `contracts/endpoints_catalogue.md`.
They return only shared columns (job own-columns + extracted fields + company
fact columns), never `score`/`tracked_status`/`status`/`notes`/`monitored` or any
user-space aggregate. A guard/contract test asserts no user-space key is present
in their output (US3-3).

---

## 2. Pagination and feed filters are API-side — the underlying queries are unchanged

**Finding.** The spec's list contract (`limit` default 100, max 500; `offset`;
`{items,total,limit,offset}`, US3-1) does not match the underlying methods:

- `get_all_for_tracker(profile_id, exclude_archived=False, limit=None)` has a SQL
  `LIMIT` but **no `offset`, `status`, or `min_score`** parameter.
- `get_companies`, `get_all_contacts`, `get_all_applications` have **no
  `limit`/`offset` at all**.
- The spec's Edge Case says, verbatim: *"Large feed (~3 700 jobs): pagination on
  the API side; the underlying query is unchanged in v1 (performance work only if
  measured as a problem)."*

**Decision.** The API layer owns pagination and the feed's `status`/`min_score`
filters uniformly, in Python, over the full result of the `JobStorage` call:

1. Call the method with no pagination (`limit=None` where the method has it).
2. Apply API-side filters: `/v1/me/feed` filters the returned rows by `status`
   (the derived `status` field — including the `"unscored"` post-process) and
   `min_score` (rows with `score is None` are excluded when `min_score` is set).
3. Slice `[offset:offset+limit]`; `total = len(filtered)`; `limit`/`offset` echoed
   back. Clamp `limit` to `[1, 500]` (default 100).

No `JobStorage` query changes. The catalogue `GET /v1/jobs` filters
(`company_id`, `source`, `posted_after`) live in the *new* `get_catalogue_jobs`
method (a new method, so a WHERE clause is fine there), but its pagination is
still API-side for uniformity.

---

## 3. Token scheme: `secrets` + SHA-256, a user-space table, five accessors

**Finding.** The spec pins the table shape (`api_tokens (id, user_id, name,
token_hash, scopes, created_at, last_used_at, revoked_at)` + `expires_at` from
FR-013) and the "raw token printed once, hash stored" rule (US2-1). Storage must
go through `JobStorage` (no SQL in `api/`, Constitution DB-access constraint).

**Decision.**

- **Generation** (`scripts/api_token.py`): `secrets.token_urlsafe(32)` — 43 chars,
  ≥ 32 bytes of entropy, URL-safe. The raw token is printed exactly once and never
  persisted.
- **Storage**: `token_hash = hashlib.sha256(token.encode()).hexdigest()` (64 hex
  chars), stored `UNIQUE`. Scopes stored as a JSON string (list of the two v1
  scope constants).
- **Table** (idempotent migration `api_tokens`, recorded in `migrations`):

  ```sql
  CREATE TABLE api_tokens (
    id           TEXT PRIMARY KEY,           -- uuid4().hex (32 chars)
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    scopes       TEXT NOT NULL,              -- JSON list, e.g. ["read:catalogue"]
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT,
    expires_at   TEXT                        -- FR-013; NULL = no expiry
  );
  CREATE INDEX idx_api_tokens_user ON api_tokens(user_id);
  ```

- **Accessors** (all on `JobStorage`):

  | Method | Signature | Purpose |
  |---|---|---|
  | `create_api_token` | `(self, name, scopes: list[str], user_id=None, expires_at=None) -> str` | generate + store hash, return raw token; `user_id` defaults to `self.user_id` |
  | `list_api_tokens` | `(self, user_id=None) -> list[dict]` | rows without secrets (`token_hash` → redacted/omitted) |
  | `revoke_api_token` | `(self, token_id) -> None` | set `revoked_at` |
  | `resolve_api_token` | `(self, token) -> dict | None` | hash + lookup; returns `{id, user_id, name, scopes, revoked_at, expires_at, last_used_at}`; `None` on unknown |
  | `touch_api_token` | `(self, token_id) -> None` | set `last_used_at = now` |

  `resolve_api_token`/`touch_api_token` are the two storage methods the API's
  auth path may call (see §4); `create`/`list`/`revoke` are CLI-only (the API
  never creates or revokes tokens in v1).

---

## 4. Auth dependency: one unbound storage for resolution, one bound for data

**Finding.** The auth step must look the token up before the user is known, so it
cannot use a `user_id`-bound storage. The spec's guard test (FR-009) forbids
`api/` from building a `JobStorage` "without the token's `user_id`" — which must
therefore be read as "without binding it to the token's user for *data* access",
with the auth resolver explicitly allow-listed.

**Decision.** Two short-lived `JobStorage` instances per request, both created in
`api/deps.py` (nowhere else):

1. `get_db(request)` → `JobStorage(DB_PATH)` (unbound) — request-scoped via
   FastAPI's default `Depends` caching.
2. `get_current_user(request, db)` → extract `Authorization: Bearer`, hash,
   `db.resolve_api_token(token)`; reject missing/unknown/revoked/expired (`401`),
   scope-missing (`403`); throttle `touch_api_token` at most once/minute/token via
   an in-memory `{token_id: last_ts}` map (FR-005). Returns an `AuthContext`
   (`user_id`, `scopes`, `token_id`).
3. `get_user_db(current_user)` → `JobStorage(DB_PATH, user_id=current_user.user_id)`
   — the bound storage the routers use.

The guard test allows exactly: `deps.py`'s `get_db` (the single unbound site) and
the two auth accessors (`resolve_api_token`, `touch_api_token`); every other
`JobStorage(` in `api/` must carry a `user_id=` derived from the token context.
No other module in `api/` constructs a `JobStorage`.

---

## 5. `/v1/me` and `/v1/me/jobs/{id}` compose several methods; scores are user-scoped in `JobStorage`

**Finding.** No single method returns "user + active profile + profiles"
(`get_active_profile_id` and `get_all_profiles` exist; there is no `get_user()`).
`get_scores_for_job(job_id)` returns scores for **all** profiles (not
user-filtered) — it is a cross-user read by nature and would leak another user's
score if the API exposed it. Isolation therefore stays in `JobStorage` (spec 039
rule), never in the API layer.

**Decision.**

- Add one small read method `get_user() -> dict | None` returning the
  `users` row for `self.user_id` (`id`, `display_name`, `active_profile_id`,
  `onboarding_completed_at`). `/v1/me` = `get_user()` + `get_active_profile_id()`
  + `get_all_profiles()`.
- Add `get_user_scores_for_job(job_id) -> list[dict]` returning the same keys as
  `get_scores_for_job` (`profile_id, score, reason, scored_by, scored_at`) but
  only for the bound user's profiles (`search_profiles.user_id = self.user_id`).
  The API calls this for `/v1/me/jobs/{id}`; it never calls `get_scores_for_job`
  (which remains a cross-user read, unclassified today — see §8).
- `/v1/me/jobs/{id}` composes `get_job_for_prepare(id)` (job facts + the user's
  `status`) **+** `get_user_scores_for_job(id)` (scores for the bound user's
  profiles only) **+** `get_lifecycle_summary(id)`
  **+** `get_lifecycle_events(id)` **+** `get_application(id)` (nullable).
  A job is shared catalogue, so a missing job → `404`; a job with no user
  tracking still returns the shared facts with the user's empty space (no
  existence disclosure issue — jobs are not owned).
- `/v1/me/companies/{id}` = `get_company_by_id(id)` + `get_company_contacts(id)`
  + `get_company_interactions(id)` + `get_company_relationship_summary(id)`.
- `/v1/me/contacts/{id}` = `get_contact(id)` + `get_contact_interactions(id)`.

User-space `None`-returns (`get_contact`, `get_application` for another user)
naturally map to `404` (never `403`), satisfying US3-2.

---

## 6. Tailscale HTTPS under `/api`, and `root_path` handling

**Finding.** The tracker today uses `tailscale serve --bg --tcp=8501`. The API's
durable exposure (Q1) is `tailscale serve --bg --https=443 --set-path /api
http://127.0.0.1:8601`: Tailscale terminates TLS, serves `https://<verva>.<tailnet>.ts.net/api/…`,
and strips `/api` before forwarding to `127.0.0.1:8601`. Generated URLs (OpenAPI,
`docs`) must carry the `/api` prefix; forwarded headers must be trusted only from
the loopback proxy.

**Decision.**

- `api/main.py` builds `FastAPI(title=…, version=<git sha>, root_path="/api",
  docs_url="/docs", openapi_url="/openapi.json")`. `uvicorn` runs without
  `--root-path` (the app sets it) so it works identically on staging (8443) and
  prod (443) — the prefix is always `/api`.
- Forwarded headers: only loopback may set them. The `api` container is bound
  `127.0.0.1:8601`, so the only upstream is the tailscaled proxy on the same host;
  the app does **not** enable `--forwarded-allow-ips=*`. Documented in
  `docs/infrastructure.md`.
- `/health` is **outside** `/v1` and requires no token (US1-4): returns
  `{"status":"ok","version":"<git sha>"}` only.

---

## 7. Infra edits: compose, deploy.sh, staging.sh, health_check.py

**Finding (verbatim from source).** `docker-compose.yml` has `tracker`
(`127.0.0.1:8501:8501`, `job_data`, `JOB_AGENT_DATA_DIR=/app/data`,
`JOB_AGENT_REQUIRE_DB=1`, `restart: unless-stopped`) and `agent`
(`profile: manual`). `deploy.sh` builds `docker compose build tracker agent`,
then `docker compose up -d tracker`, **then** seeds (`python -m core.seed`) —
seed is the last DB-open before the user-facing services, which is exactly the
ordering the spec's edge case requires (avoid a first-ever migration race).
`staging.sh` runs one detached container on `127.0.0.1:8502:8501` +
`tailscale serve --bg --tcp=8502`. `health_check.py` hardcodes the
`/_stcore/health` suffix (Streamlit-specific).

**Decision.**

- **compose**: add

  ```yaml
    api:
      build: .
      container_name: job-api
      command: uvicorn api.main:app --host 127.0.0.1 --port 8601
      ports:
        - "127.0.0.1:8601:8601"
      volumes:
        - job_data:/app/data
      env_file:
        - .env
      environment:
        - JOB_AGENT_DATA_DIR=/app/data
        - JOB_AGENT_REQUIRE_DB=1
      restart: unless-stopped
  ```

- **deploy.sh**: `docker compose build tracker agent api`; keep `docker compose
  up -d tracker`; after the seed step add `docker compose up -d api`.
- **staging.sh**: add `API_PORT=8602`; a second detached container
  `-p 127.0.0.1:${API_PORT}:8601` over the same `$VOLUME` running
  `uvicorn api.main:app --host 127.0.0.1 --port 8601`; expose
  `tailscale serve --bg --https=8443 --set-path /api http://127.0.0.1:${API_PORT}`;
  `down` removes the api container and the `8443` serve entry.
- **health_check.py**: add an API check that GETs `/health` and asserts a JSON
  `status == "ok"` (a separate `check_api_health(url)` function — the existing
  `check_health` is Streamlit-`/_stcore/health`-specific and must not be reused
  for a plain JSON endpoint). From the tailnet it checks
  `https://<tailnet name>/api/health`; inside the host `http://127.0.0.1:8601/health`.

---

## 8. New storage methods must be classified in the isolation suite

**Finding.** `tests/test_isolation.py` keeps `USER_SPACE_METHODS` /
`CROSS_USER_METHODS` tuples and a coverage meta-test that fails on any public
`JobStorage` method that is unclassified **or** that references a user-space token
without being classified user-space. Adding methods to `JobStorage` without
classifying them breaks that suite.

**Decision.** The new methods are classified as follows: the five token accessors
(`create_api_token`, `list_api_tokens`, `revoke_api_token`, `resolve_api_token`,
`touch_api_token`) → **cross-user** (auth/management infrastructure keyed by
`token_id`/`user_id`, not the bound user's data view — the CLI is the only
creator/revoker). The catalogue read methods (`get_catalogue_jobs`,
`get_catalogue_job`, `get_company_facts`) → **catalogue** (shared); `get_user` and
`get_user_scores_for_job` → **user-space** (both reference `self.user_id`; the
latter also references `search_profiles`). The existing `get_scores_for_job` stays
as-is (cross-user by nature, still called by the single-user tracker) and is
**not** exposed by the API. FR-010 extends the suite with a two-token fixture
asserting each token sees only its user's data through the API (`TestClient`
against an in-memory DB).

---

## 9. Serialisation, dates, enums, and the OpenAPI snapshot

**Finding.** `JobStorage` returns plain `dict`s (from `sqlite3.Row`); dates are
`"YYYY-MM-DD HH:MM:SS"` strings, statuses/`work_mode`/`geo_zone`/etc. are strings
with `COALESCE` fallbacks already applied by the methods.

**Decision.**

- Response models are explicit pydantic models (one per payload), field-for-field
  over the method's dict keys; **no passthrough of unknown fields** (US3-4) —
  `model_config = ConfigDict(extra="forbid")` at the serialiser boundary so an
  accidental new internal column fails the contract test instead of leaking.
- Dates and enums are returned **as the strings `JobStorage` already returns**
  (no parsing/reformatting in v1) — stable values, byte-compatible with the
  tracker (SC-004).
- The OpenAPI schema is committed at `api/openapi.json` and a snapshot test
  regenerates + diffs it, failing on any drift (FR-008); changing the schema
  requires updating the snapshot deliberately.

---

## 10. FR-013–017: foundations for a later public exposure, nothing public now

**Decision.** All five are build-time foundations only, no public exposure, per the
2026-10-15 decision:

- **FR-013** — `expires_at` on `api_tokens`, `create --expires-in <days>`; expired
  → `401` in `resolve_api_token`/auth. Default (operator tokens): no expiry.
- **FR-014** — failed-auth attempts logged (timestamp, client address, route,
  reason) with **no token material**, counted per client address in an in-memory
  map; **no rate limiting** (that arrives with public exposure).
- **FR-015** — errors are `{"error":"<code>","message":"<short text>"}` (generic,
  no stack traces/paths); request bodies are not accepted (read-only); query
  params validated by pydantic.
- **FR-016** — the base URL is read from `JOB_AGENT_API_URL` (with the tailnet
  default); nothing hard-codes the host.
- **FR-017** — `docs/api-exposure.md` records the prerequisites for any future
  public opening (IdP/passkeys + session cookies, rate limiting/brute-force,
  token rotation, security headers/size limits, audit retention, dependency
  policy, security review, Constitution VII amendment) and the candidate entry
  points. Nothing here is implemented.

---

## Open items resolved

- Q1 (HTTPS `/api` on tailnet) → §6.
- Q2 (full endpoint table) → §1–§5 + contracts.
- Q3 (tailnet-only, foundations) → §10.
- Catalogue-method gap → §1 (three new read methods + `get_user`).
- Pagination/filter gap → §2 (API-side, query unchanged).
