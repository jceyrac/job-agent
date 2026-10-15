# Contract — `scripts/api_token.py` CLI (FR-004, FR-013)

Run as a module: `python -m scripts.api_token <command> …`. It is the **only**
caller of `create_api_token` / `list_api_tokens` / `revoke_api_token` (the API
never creates or revokes tokens in v1). It uses `JobStorage` (no SQL).

## Commands

### `create`

```bash
python -m scripts.api_token create --name <name> --scopes read:catalogue,read:me [--user <id>] [--expires-in <days>]
```

- `--name`: required, operator label.
- `--scopes`: required, comma-separated subset of `{read:catalogue, read:me}`;
  an unknown scope → `stderr` + exit `2`.
- `--user <id>`: token owner; defaults to the default user (unbound `JobStorage`
  default, `config.default_user_id`).
- `--expires-in <days>`: optional; sets `expires_at = now + days`.
- Prints the raw token **once** to stdout (a clear one-line message), then a
  confirmation line with the token `id`. The token is not written anywhere.

### `list`

```bash
python -m scripts.api_token list [--user <id>]
```

- Prints a table (or JSON with `--json`) of `id, user_id, name, scopes,
  created_at, last_used_at, revoked_at, expires_at`. **No hash/secret.**

### `revoke`

```bash
python -m scripts.api_token revoke <id>
```

- Sets `revoked_at`; unknown `id` → `stderr` + exit `1`; success → exit `0`.

## Errors

- Exit `2` for invalid arguments (unknown scope, bad day count).
- Exit `1` for a missing/unknown target id on `revoke`.
- Never prints a token except the single `create` output.
