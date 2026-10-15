# Contract — bearer auth (US2, FR-005, FR-006, FR-013, FR-014, FR-015)

## Dependencies (all in `api/deps.py`, nowhere else)

- `get_db(request)` → `JobStorage(DB_PATH)` **unbound** (user_id=None); request-
  scoped via FastAPI's default `Depends` caching.
- `get_current_user(request, db=Depends(get_db))` → `AuthContext`:
  - Extracts `Authorization: Bearer <token>`.
  - Missing/malformed header, unknown token, revoked, or expired → `401`.
  - `db.resolve_api_token(token)` → row; decode `scopes`.
  - Returns `AuthContext(user_id, scopes, token_id)`.
  - Throttles `db.touch_api_token(token_id)` at most once/minute/token using an
    in-memory `{token_id: monotonic_ts}` map (FR-005).
- `get_user_db(current_user=Depends(get_current_user))` →
  `JobStorage(DB_PATH, user_id=current_user.user_id)` — the bound storage the
  routers use.
- `require_scope(scope)` → a dependency factory: given the route's required scope,
  returns `current_user` if `scope in current_user.scopes`, else `403`.

## Route → scope map

| Routes | Required scope |
|---|---|
| `GET /v1/jobs`, `/v1/jobs/{job_id}`, `/v1/companies/{company_id}` | `read:catalogue` |
| `GET /v1/me` and everything under `/v1/me/*` | `read:me` |
| `GET /health` | none (open) |

## Status semantics

| Condition | Status | Body |
|---|---|---|
| Missing/empty/unknown/revoked/expired token | `401` | `{"error":"unauthorized","message":"..."}` |
| Valid token, missing route scope | `403` | `{"error":"forbidden","message":"..."}` |
| Resource absent or not the user's | `404` | `{"error":"not_found","message":"..."}` |
| Invalid query params | `422` | FastAPI default (pydantic), error envelope added via handler |

No `Authorization` header, token, or token hash may appear in any response body or
error message.

## Logging invariants (FR-006, FR-014)

- The uvicorn access log logs the request line + status only — **not** headers —
  so the `Authorization` header is never logged. The app does not add header
  logging.
- Failed-auth attempts are logged as `{timestamp, client_addr, route, reason}`
  (reason ∈ `missing`, `unknown`, `revoked`, `expired`), counted per client
  address in an in-memory map. **No token material, no hash.**
- No error path prints the token or a stack trace (FR-015).
- Test: run a request with a real token; capture the log stream; assert the token
  string and `Authorization` are absent (FR-006).

## Expiry (FR-013)

- `expires_at` set via `create --expires-in <days>`; `resolve_api_token` returns
  it, and `get_current_user` compares `expires_at <= now` → `401` (reason
  `expired`). Default operator tokens have no expiry.
