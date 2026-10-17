# Research — API v1 write endpoints (spec 042)

Phase 0 output. Every open design point is resolved below with a decision,
rationale, and rejected alternatives. All storage-method references were
verified against `core/storage.py` at `5f2891c`.

---

## R1 — Enforce a 64 KB request-body limit (`413`)

**Decision**: a small ASGI middleware `api/middleware.py`
(`RequestSizeLimitMiddleware`) that reads `Content-Length` (and, when absent or
lied about, counts the body as it is read) and returns `413 {"error":
"payload_too_large", "message": "Request body too large"}` above 64 KB. No new
dependency — pure Starlette/ASGI.

**Rationale**: FastAPI/Starlette has no built-in body-size cap. The spec FR-003
requires a 64 KB cap → `413`. A middleware is the only place that sees every
write uniformly and rejects *before* pydantic parses the body.

**Alternatives rejected**:
- Per-route dependency reading `request.body()` — duplicated on every route, and
  would already have buffered the body.
- `uvicorn`/reverse-proxy `--limit` — not available on the loopback path and not
  in the app's control (the app must own the contract per FR-003).

---

## R2 — "Not visible to the token's user" → 404 (per-route pre-check)

The API must map "foreign or missing resource" to **404** before any write, so a
write can never touch another user's data. Each route therefore runs an
allow-listed **read** pre-check first, then the write.

| Route | 404 pre-check (existing read method) |
|---|---|
| `PUT /v1/me/jobs/{id}/status` | `get_job_for_prepare(job_id)` is `None` (job absent from catalogue) |
| `POST /v1/me/jobs/{id}/events` | `get_job_for_prepare(job_id)` is `None` |
| `PATCH/DELETE /v1/me/jobs/{id}/events/{eid}` | `event_id` not in `get_lifecycle_events(job_id)` (user-bound) |
| `PATCH /v1/me/companies/{id}` | `get_company_by_id(company_id)` is `None` |
| `POST /v1/me/contacts` | `get_company_by_id(company_id)` is `None` (company is required) |
| `PATCH /v1/me/contacts/{id}` | `get_contact(contact_id)` is `None` (user-bound) |
| `POST /v1/me/contacts/{id}/interactions` | `get_contact(contact_id)` is `None` |
| `POST /v1/me/companies/{id}/interactions` | `get_company_by_id(company_id)` is `None` |
| `PUT /v1/me/active-profile` | profile missing/foreign (see R6) |
| `PUT /v1/me/profiles/{pid}` | `get_profile(pid)` is `None` or raises `ProfileNotFoundError` |
| `PATCH /v1/companies/{id}` | `get_company_facts(company_id)` is `None` |

**Rationale**: jobs and company *facts* are shared-catalogue, so their "404"
means "absent from the catalogue"; contacts/events/profiles are user-space, so
their "404" means "absent *for this user*" (a foreign id is indistinguishable —
Constitution VII, no existence oracle). This matches spec 041's read semantics.

**Alternatives rejected**:
- Relying on the write method to raise — `update_lifecycle_event` /
  `delete_lifecycle_event` raise `ValueError("Unknown interaction id")`, which
  is indistinguishable from a bad argument and would map to 422, not 404.
- A single "existence" helper — the predicate differs per space (catalogue vs
  user), so per-route pre-checks are clearer and use existing methods only.

---

## R3 — Event edit/delete and reconciliation (endpoint shape)

**Decision**: the event edit/delete routes are **job-scoped**:
`PATCH /v1/me/jobs/{job_id}/events/{event_id}` and
`DELETE /v1/me/jobs/{job_id}/events/{event_id}` — consistent with the create
route (`POST /v1/me/jobs/{job_id}/events`). Each calls the storage mutation
(`update_lifecycle_event` / `delete_lifecycle_event`) then
`reconcile_lifecycle_status(job_id)`, exactly as the tracker does
(`tracker_views/job_detail.py`).

**Rationale**: the tracker reconciles the tracking status after every event
edit/delete, because the mutation itself does not (verified in `core/storage.py`
— `update_lifecycle_event`/`delete_lifecycle_event` touch only `interactions`).
`reconcile_lifecycle_status` is job-scoped, so the route needs the `job_id`; the
spec's flat `/v1/me/events/{event_id}` carries none. This is the only way to get
"same effects as the tracker" (FR-002) with **zero storage changes**.

**Alternatives rejected**:
- Flat endpoint + a new `get_lifecycle_event(event_id)` read accessor in
  `core/storage.py` — satisfies the literal spec but adds a Stable-core method
  for one route.
- Flat endpoint + `job_id` in the body — awkward (DELETE-with-body) and still a
  spec deviation.

---

## R4 — Company-facts write must exclude user-relationship fields

**Decision**: `PATCH /v1/companies/{company_id}` (`write:catalogue`) accepts
only the **fact** columns of `_ALLOWED_COMPANY_FIELDS` and explicitly rejects
`notes` (which `update_company_fields` routes to `user_companies`, the user
space). The pydantic request model enumerates the fact fields; `status` and
`monitored` are not `_ALLOWED_COMPANY_FIELDS` at all (they go through
`set_company_status` / `set_company_monitored` under `write:me`).

**Fact fields** (from `_ALLOWED_COMPANY_FIELDS`, minus `notes`):
`name`, `website`, `company_country`, `industry_sector`, `company_size`,
`x_handle`, `careers_url`, `ats_provider`, `ats_identifier`,
`scraping_method`, `research_notes`.

**Rationale**: Constitution X — a `write:catalogue` token must not be able to
write user-space columns. `notes` is the one field in the allow-list that is
user-space; the others are shared facts.

**Alternatives rejected**: allow `notes` under `write:catalogue` — would let a
catalogue-only token write user-space data, violating X and VII.

---

## R5 — Error mapping (storage exceptions → HTTP)

| Condition | Source | HTTP |
|---|---|---|
| Missing bearer / unknown / revoked / expired token | `deps.get_current_user` | 401 (existing) |
| Missing scope | `require_scope` | 403 (existing) |
| Foreign or missing resource | read pre-check (R2) | 404 |
| Invalid enum (status/event/type/direction/outcome) | pydantic enum | 422 |
| Invalid column/field | pydantic `extra="forbid"` or storage `ValueError` | 422 |
| Body > 64 KB | middleware | 413 |
| Duplicate `linkedin_url` | — | *(none — R7 drops the 409)* |

`ValueError` from a storage write (e.g. `set_status` on a bad status that passed
pydantic, `update_company_fields` on an unknown column) is caught and mapped to
`422` with a generic message — the route never lets an internal message leak the
allowed-values string verbatim, but the pydantic model already constrains the
values so this is a defensive backstop.

---

## R6 — Profile ownership (`PUT /v1/me/active-profile`, `PUT /v1/me/profiles/{id}`)

**Decision**: before `set_active_profile_id(profile_id)` / `upsert_profile(...)`,
the route calls `get_profile(profile_id)`. A profile owned by another user raises
`ProfileNotFoundError` (already mapped to `404` by `api/main.py`); a missing
profile returns `None` (also `404`). `set_active_profile_id` then runs only on a
confirmed own profile.

**Rationale**: reuse the exact profile-ownership guard already exercised by the
read API (spec 041, `_require_profile`). No new ownership logic.

---

## R7 — Contact `409` on duplicate `linkedin_url` is dropped

**Decision**: `POST /v1/me/contacts` mirrors the tracker exactly — it calls
`upsert_contact`, which **merges** (resolves by `linkedin_url` > `email` >
`(company, name)`) and returns the existing or new contact id. There is **no
409**.

**Rationale**: the underlying method never raises on a duplicate; the UNIQUE
constraint `(user_id, linkedin_url)` is resolved by the pre-insert lookup, not by
an insert that fails. The tracker's effect is a merge. A 409 would be new
behaviour (spec non-goal) and would break "same effects" (FR-002).

**Alternatives rejected**: a strict-create pre-check (look up `linkedin_url`,
return 409 if owned) — introduces behaviour the tracker does not have. If
strict-create semantics are ever wanted, they are a separate, deliberate spec.

---

## R8 — Response shapes

**Decision**: writes return the **updated resource** (re-read via an existing
allow-listed read method) or `204` for deletes, per FR-003:

| Route | Response |
|---|---|
| `PUT /v1/me/jobs/{id}/status` | 200 → job detail via `get_job_for_prepare` (carries the new `status`) |
| `POST /v1/me/jobs/{id}/events` | 200 → the event list via `get_lifecycle_events(job_id)` |
| `PATCH /v1/me/jobs/{id}/events/{eid}` | 200 → event list (post-reconcile) |
| `DELETE /v1/me/jobs/{id}/events/{eid}` | 204 |
| `PATCH /v1/me/companies/{id}` | 200 → `get_company_by_id` + `get_company_relationship_summary` |
| `POST /v1/me/contacts` | 200 → `get_contact(new_id)` (upsert returns only the id, no created/merged signal) |
| `PATCH /v1/me/contacts/{id}` | 200 → `get_contact(id)` |
| `POST …/interactions` | 201 → the interaction row (or 204) |
| `PUT /v1/me/active-profile` | 204 |
| `PUT /v1/me/profiles/{pid}` | 200 → `get_profile(pid)` |
| `PATCH /v1/companies/{id}` | 200 → `get_company_facts(id)` |

**Rationale**: the contract tests assert **resulting DB rows** (API vs direct
`JobStorage`), not the response body — so bodies reuse existing serializers and
add no new read logic. Keeping the body as a re-read also makes the write
round-trip self-verifying.

---

## R9 — Structured write log (FR-007)

**Decision**: a small helper in `api/` (used by every write route, or a
`logging` call in a shared dependency) emits one line per write:
`write_ok/write_denied token_id=<id> user_id=<id> route=<method path>
resource_id=<id> status=<code>` — never the body, never the token value.

**Rationale**: FR-007 requires an audit line; it is observability, not storage.
`token_id`/`user_id` come from `AuthContext` (already resolved); `resource_id`
from the path parameter.

---

## R10 — Scopes and the token CLI (FR-007)

**Decision**: extend `API_TOKEN_SCOPES` in `core/storage.py` to
`{"read:catalogue", "read:me", "write:me", "write:catalogue"}`. The token CLI
(`scripts/api_token.py`) already validates against the imported constant, so it
accepts the new scopes with no logic change; only its `--help`/docstring text is
updated to name the write scopes (cosmetic).

**Rationale**: the frozenset is the single source of truth for valid scopes and
feeds both `create_api_token` (storage) and the CLI. Forking the list elsewhere
would risk minting tokens the storage layer then rejects.
