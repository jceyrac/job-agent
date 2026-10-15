# Feature Specification: API v1 — read-only (roadmap step 5 — Phase C)

**Feature Branch**: `041-api-read`

**Created**: 2026-10-15

**Status**: Clarified 2026-10-15 — ready for `/speckit.plan`

**Input**: Roadmap `docs/roadmap-api.md`, step 5 (step 2b — task queue — is attached to step 6, decision 2026-10-15). Constitution: Architecture Constraints (monolithe modulaire, un router par domaine, FastAPI/uvicorn/pydantic autorisés pour `api/`), VII (Tailscale only, scoped tokens, least privilege), X (catalogue vs user space), XI (progressive migration). "First brick of the new architecture: a read-only HTTP API over `core`, built on the Phase-B model, reachable only through Tailscale, authenticated with scoped tokens. The Streamlit tracker is not touched."

---

## Clarifications

### Session 2026-10-15

- Q1: Port and exposure → A: **HTTPS on the tailnet name, under a path** — the durable option. The API listens on `127.0.0.1:8601` (unchanged), and Tailscale terminates TLS and proxies `https://<verva>.<tailnet>.ts.net/api/` → `http://127.0.0.1:8601` (`tailscale serve --bg --https=443 --set-path /api http://127.0.0.1:8601`). Valid certificate managed and renewed by Tailscale, stable DNS name instead of an IP, and the future `web/` front can be served from the same origin (root path) so the browser never needs CORS. One-time prerequisite: MagicDNS and HTTPS certificates enabled in the Tailscale admin console. Accepted side effect: the machine's tailnet DNS name appears in public Certificate Transparency logs (no service is reachable from the internet).
- Q2: Endpoint scope of v1 → A: **the full table** of US3.
- Q3 (raised 2026-10-15): drop Tailscale and publish on the user's own domain? → A: **not now**. The pain point is needing the Tailscale client on every device; acceptable while the API is private and single-user. The API stays tailnet-only (Constitution VII) but is built so that opening it later is a deliberate, separate step: FR-013 to FR-017.

---

## Context (verified against source at `c04998c`, 2026-10-15)

- **Model ready**: catalogue (`jobs`, extracted fields, company facts) vs user space (`users`, `user_companies`, `job_tracking`, `job_applications`, `status_history`, `contacts`, `interactions`, `api` will add `api_tokens`); `JobStorage(db_path, user_id=…)` binds a user (spec 039); isolation suite exists (`tests/test_isolation.py`).
- **Every DB access goes through `JobStorage`** (spec 037, guard-tested). The API adds no SQL.
- **Read methods available** (non-exhaustive, the plan maps each endpoint): `get_all_for_tracker`, `get_stats`, `get_dashboard_data`, `get_all_applications`, `get_application`, `get_lifecycle_summary`, `get_lifecycle_events`, `get_scores_for_job`, `get_job_for_prepare`, `get_companies`, `get_company_by_id`, `get_company_relationship_summary`, `get_company_contacts`, `get_company_interactions`, `get_jobs_for_company`, `get_all_contacts`, `get_contact`, `get_contact_interactions`, `get_active_profile_id`, `get_all_profiles`, `get_last_run`.
- **Infra**: compose services `tracker` (published `127.0.0.1:8501`, exposed on the tailnet with `tailscale serve --tcp=8501`, spec 033) and `agent` (cron, profile `manual`). Single image; `pip install -e .`. No `api/` directory yet; FastAPI/uvicorn not in `requirements.txt`.
- **Staging** (`scripts/staging.sh`) runs only a tracker on 8502.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — A running, private API service (Priority: P1)

As the operator, I want an `api` service in the stack, reachable only through Tailscale, with a health endpoint, so that clients (the future `web/` front, agents, scripts) have a stable entry point.

**Independent Test**: After deploy, from a tailnet device `GET https://<verva>.<tailnet>.ts.net/api/health` returns `200 {"status":"ok","version":"<git sha>"}` with a valid certificate (no `-k`); from verva's LAN IP port 8601 is refused; `docker port` shows a loopback-only binding.

**Acceptance Scenarios**:

1. **Given** the compose file, **Then** a service `api` runs `uvicorn api.main:app` from the same image, mounts `job_data`, sets `JOB_AGENT_DATA_DIR`/`JOB_AGENT_REQUIRE_DB=1`, publishes `127.0.0.1:8601:8601` only, `restart: unless-stopped`.
2. **Given** verva, **Then** the tailnet exposure is `tailscale serve --bg --https=443 --set-path /api http://127.0.0.1:8601` (persistent), the app is configured with `root_path="/api"` so generated URLs and `/api/docs` work behind the proxy, and the setup (prerequisites, command, `tailscale serve status`, undo) is documented in `docs/infrastructure.md`. The tracker keeps its current `tcp 8501` exposure (unchanged).
3. **Given** `deploy.sh`, **Then** it builds and restarts `api` together with `tracker`; `scripts/health_check.py` also checks `GET /health` of the API.
4. **Given** `/health`, **Then** it requires no token and returns no data beyond status and version.

---

### User Story 2 — Scoped bearer tokens bound to a user (Priority: P1)

As the operator, I want to create, list and revoke API tokens bound to a user and to scopes, so that each client gets least privilege and a leaked token can be revoked.

**Independent Test**: Create a token with scope `read:me` for the default user; it can read `/v1/me/*` but gets `403` on `/v1/jobs`; after revocation it gets `401`; the raw token never appears in the DB, logs or error messages.

**Acceptance Scenarios**:

1. **Given** `python -m scripts.api_token create --name <client> --scopes read:catalogue,read:me [--user <id>]`, **Then** a random token (≥ 32 bytes, URL-safe) is printed **once**; only its SHA-256 hash is stored, in a new user-space table `api_tokens (id, user_id, name, token_hash, scopes, created_at, last_used_at, revoked_at)`.
2. **Given** `list` / `revoke <id>`, **Then** tokens are listed without secrets and revoked tokens are rejected immediately.
3. **Given** a request, **Then** `Authorization: Bearer <token>` is required on every `/v1/*` route; missing/unknown/revoked → `401`; valid but lacking the route's scope → `403`.
4. **Given** a valid token, **Then** the request runs with `JobStorage(db_path, user_id=<token.user_id>)` — the user comes from the token only, never from a parameter.
5. **Given** logs, **Then** neither the `Authorization` header nor the token appears in access or error logs (Constitution VII).
6. **Scopes in v1**: `read:catalogue`, `read:me`. Write, tasks and ingest scopes arrive with steps 6–7.

---

### User Story 3 — Read the catalogue and my space (Priority: P1)

As a client, I want versioned, documented read endpoints on the catalogue and on the token user's space, returning exactly what `JobStorage` returns, so that a new front and agents can be built on them.

**Independent Test**: Contract tests (FastAPI `TestClient`, in-memory DB) for every endpoint; each payload equals the corresponding `JobStorage` method's output (same rows, same fields after serialisation); the generated OpenAPI document matches a committed snapshot.

**Endpoints (v1, read-only)** — final list confirmed in the plan, see Q2:

| Route | Scope | Source (`JobStorage`) |
|---|---|---|
| `GET /v1/jobs` (filters: `company_id`, `source`, `posted_after`; `limit`/`offset`) | `read:catalogue` | catalogue job facts + extracted fields, no user data |
| `GET /v1/jobs/{job_id}` | `read:catalogue` | job facts + extracted fields |
| `GET /v1/companies/{company_id}` | `read:catalogue` | company facts only |
| `GET /v1/me` | `read:me` | user, active profile id, profiles |
| `GET /v1/me/feed` (filters: `profile_id` default active, `status`, `min_score`, `exclude_archived`; `limit`/`offset`) | `read:me` | `get_all_for_tracker` |
| `GET /v1/me/jobs/{job_id}` | `read:me` | job + my scores + tracking status + lifecycle summary/events + application |
| `GET /v1/me/stats`, `GET /v1/me/dashboard` | `read:me` | `get_stats`, `get_dashboard_data` |
| `GET /v1/me/applications` | `read:me` | `get_all_applications` |
| `GET /v1/me/companies`, `GET /v1/me/companies/{id}` | `read:me` | companies with my relationship; detail with my contacts and interactions summary |
| `GET /v1/me/contacts`, `GET /v1/me/contacts/{id}` | `read:me` | my contacts; detail with interactions |

**Acceptance Scenarios**:

1. **Given** any list endpoint, **Then** it accepts `limit` (default 100, max 500) and `offset`, and returns `{items, total, limit, offset}`.
2. **Given** a resource of another user (contact, application, …), **Then** the response is `404`, never `403` (no existence disclosure).
3. **Given** catalogue endpoints, **Then** no user-space field is ever present in their payloads (test).
4. **Given** each endpoint, **Then** an explicit pydantic response model defines its fields; unknown internal fields are not leaked.
5. **Given** the API package, **Then** a guard test fails if `api/` calls a `JobStorage` method outside the read allow-list, imports `sqlite3`, or builds a `JobStorage` without the token's `user_id`.
6. **Given** two users and two tokens, **Then** the isolation suite is extended to the API: each token only ever sees its user's data.

---

### User Story 4 — Released through the standard flow (Priority: P1)

**Acceptance Scenarios**:

1. **Given** `staging.sh up <exact SHA>`, **Then** staging also runs the API on `127.0.0.1:8602`, exposed as `https://<verva>.<tailnet>.ts.net:8443/api/` (same `serve --https` pattern on port 8443, removed by `down`); a staging token is created on the staging volume; from the Mac, `curl` checks on `/api/health`, `/api/v1/me/feed`, `/api/v1/me/jobs/<id>`, `/api/v1/me/contacts` succeed with a valid certificate and match the staging tracker's figures; tracker parity vs prod image identical.
2. **Given** the deploy, **Then** SHA = staged SHA; `api` container up on `127.0.0.1:8601` only; `https://<verva>.<tailnet>.ts.net/api/health` 200 with a valid certificate; LAN port 8601 refused; tracker and cron unaffected (health check OK, next scheduled run `success`).
3. **Given** a problem, **Then** rollback = stop and remove the `api` service (no migration to undo except the new `api_tokens` table, which is harmless to the previous image).

---

### Edge Cases

- Concurrent access: the API reads while the cron writes — SQLite WAL, one `JobStorage` per request; no long transactions in handlers.
- `JobStorage.__init__` runs migrations: the API must not race the tracker on a first-ever migration — after deploy, the tracker/seed step opens the DB first (`deploy.sh` order: seed before starting `api`).
- Large feed (~3 700 jobs): pagination on the API side; the underlying query is unchanged in v1 (performance work only if measured as a problem).
- Dates and enums serialised as ISO 8601 strings and stable string values.
- `/docs` (OpenAPI UI): reachable on the tailnet only, exposes the schema, no data.
- No CORS in v1 (no browser client yet).

---

## Requirements *(mandatory)*

### Functional Requirements

**Service (US1)**

- **FR-001**: New package `api/` (`api/main.py`, one router per domain: `catalogue`, `me`), importing `core` only; added to `pyproject.toml` packages.
- **FR-002**: Dependencies `fastapi`, `uvicorn` added to `requirements.txt` with versions at least 14 days old, justified in the plan (Constitution: already authorised for `api/`).
- **FR-003**: Compose service `api` as in US1-1; `deploy.sh` builds/restarts it after the seed step; tailnet HTTPS exposure under `/api` per US1-2 (path prefix handled with `root_path`, forwarded headers trusted only from the loopback proxy); `health_check.py` checks `https://<tailnet name>/api/health` when run from the tailnet and `http://127.0.0.1:8601/health` inside the host.

**Auth (US2)**

- **FR-004**: `api_tokens` table (user space, created by an idempotent migration), SHA-256 token hashes only; `scripts/api_token.py` (`create`, `list`, `revoke`).
- **FR-005**: Bearer auth dependency resolving token → user + scopes; `401`/`403` semantics as in US2-3; `last_used_at` updated at most once per minute per token (no write per request).
- **FR-006**: No token or `Authorization` header in any log line (uvicorn access log configured accordingly; test).

**Read endpoints (US3)**

- **FR-007**: The v1 endpoints of the US3 table, versioned under `/v1`, pydantic response models, pagination contract.
- **FR-008**: Contract tests per endpoint (payload == `JobStorage` output); OpenAPI snapshot test (`api/openapi.json` committed; a change requires updating the snapshot deliberately).
- **FR-009**: Guard test: `api/` calls only allow-listed read methods, no `sqlite3`, `JobStorage` always bound to the token user.
- **FR-010**: Isolation suite extended to the API (two users, two tokens).

**Release (US4)**

- **FR-011**: `scripts/staging.sh` also starts the API on `127.0.0.1:8602`, exposed with `tailscale serve --bg --https=8443 --set-path /api http://127.0.0.1:8602`, and `down` removes both (verification output includes the 8443 serve entry).
- **FR-012**: Standard release flow; `CLAUDE.md` (commands, infrastructure, API section) and `docs/infrastructure.md` updated; roadmap step 5 marked done at closing.

**Foundations for a later public exposure (decision 2026-10-15: stay tailnet-only for now, but build so that opening later is a deliberate step, not a rewrite)**

- **FR-013**: Tokens support an optional expiry (`expires_at`, set with `create --expires-in <days>`); expired tokens get `401`. Default: no expiry for the operator's own tokens.
- **FR-014**: Failed authentication attempts are logged (timestamp, client address, route, reason) without any token material, and counted per client address in memory; no blocking yet (rate limiting comes with public exposure).
- **FR-015**: Error responses are generic JSON (`{"error": "<code>", "message": "<short text>"}`), never stack traces or internal paths; request bodies are not accepted in v1 (read-only), and query parameters are validated by pydantic.
- **FR-016**: Nothing hard-codes the API host: clients and scripts read the base URL from configuration (`JOB_AGENT_API_URL`), so moving from the tailnet name to a public domain later is a configuration change.
- **FR-017**: `docs/api-exposure.md` records what must exist **before** any public exposure (not implemented here): human login with an identity provider or passkeys and HTTP-only secure session cookies for the browser front, rate limiting and brute-force protection, token rotation policy, security headers and request size limits, audit log retention, dependency update policy, a security review, and the Constitution VII amendment. Candidate entry points: own domain (`jobs-api.jeromeceyrac.com`) behind the existing reverse proxy, or an identity-aware proxy (e.g. Cloudflare Access).

### Non-goals

- Any write endpoint, task queue, SSE (step 6, which also absorbs step 2b).
- Agents through the API (step 6b), ingestion through the API (step 7).
- A client: the tracker keeps using `JobStorage` directly; `web/` comes in Phase D.
- CORS, rate limiting, multi-user onboarding, token management UI.
- Performance tuning of existing queries.

### Key Entities

- **API token**: belongs to a user; has a name, scopes, a hash, creation/last-use/revocation times.

---

## Success Criteria *(mandatory)*

- **SC-001**: Every v1 endpoint has a contract test asserting equality with its `JobStorage` source; OpenAPI snapshot test green.
- **SC-002**: Auth: 401/403/404 semantics proven by tests; a token never reads another user's data (API isolation suite); no token in logs (test).
- **SC-003**: Guard test green and failing on a write call, a `sqlite3` import, or an unbound `JobStorage` in `api/`.
- **SC-004**: Staging: API answers on the tailnet with figures equal to the staging tracker's; tracker parity identical.
- **SC-005**: Prod: `https://<verva>.<tailnet>.ts.net/api/health` 200 with a valid certificate, LAN port 8601 refused, tracker and next cron run unaffected.

---

## Questions answered in clarify (2026-10-15) — kept for the record

- **Q1 — Port and exposure.** Options considered: `tcp=8601` (same as the tracker) vs HTTPS under `/api` on the tailnet name. Chosen: HTTPS (see Clarifications).
- **Q2 — Endpoint scope of v1.** Options considered: full table vs minimal v1. Chosen: full table.
