# Contract — `api_tokens` storage accessors (FR-004, FR-013)

All accessors live on `JobStorage` (`core/storage.py`). No SQL in `api/`. The
migration `api_tokens` is idempotent and recorded in `migrations`.

## Table

See `data-model.md` §1. Key invariant: the raw token is never stored, returned by
`list`, or logged — only `sha256(raw).hexdigest()`.

## Accessors

### `create_api_token(self, name, scopes, user_id=None, expires_at=None) -> str`

- `scopes`: `list[str]`; each element validated against `{read:catalogue,
  read:me}` (reject unknown → `ValueError`).
- `user_id`: owner; defaults to `self.user_id`.
- `expires_at`: optional ISO 8601 string (from `--expires-in`); `None` = no expiry.
- Generates `token = secrets.token_urlsafe(32)`; stores `token_hash =
  sha256(token.encode()).hexdigest()`, `id = uuid4().hex`, `scopes = json.dumps(...)`,
  `created_at = now`.
- **Returns the raw token** (the caller prints it exactly once). The stored row
  never contains it.

### `list_api_tokens(self, user_id=None) -> list[dict]`

- `user_id=None` → all tokens; otherwise the user's tokens.
- Returned dict keys: `id`, `user_id`, `name`, `scopes`, `created_at`,
  `last_used_at`, `revoked_at`, `expires_at`. **No `token_hash`, no secret.**

### `revoke_api_token(self, token_id) -> None`

- Sets `revoked_at = now` where `id = token_id`. Unknown id → `ValueError`
  (surfaced as exit 1 by the CLI). Idempotent: revoking an already-revoked token
  is a no-op that still reports success.

### `resolve_api_token(self, token) -> dict | None`

- Computes `sha256(token.encode()).hexdigest()` and looks up the row.
- Returns `{id, user_id, name, scopes, revoked_at, expires_at, last_used_at}`
  (`scopes` decoded to `list[str]`), or `None` when unknown.
- Does **not** itself reject revoked/expired — the auth dependency decides `401`
  (so the caller can log a distinct reason, FR-014). Purely a read.

### `touch_api_token(self, token_id) -> None`

- Sets `last_used_at = now`. Called at most once/minute/token by the auth
  dependency (throttling lives in `api/deps.py`, not here).

## Guard / tests

- `tests/test_storage.py` (in-memory): round-trip create→resolve→touch→list→
  revoke→resolve-after-revoke; hash-never-equals-token; scope validation;
  migration idempotency (second open changes nothing).
- The raw token is never present in any returned dict or stored column (test).
