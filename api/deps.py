"""api/deps.py — bearer-auth dependency (spec 041, US2 / FR-005/006/013/014).

The only module in `api/` that constructs a `JobStorage`:

- `get_db` — the single **unbound** site (auth resolution happens before the
  user is known).
- `get_user_db` — bound to the token user; the storage every router uses.

Every other `api/` module calls a dependency or an allow-listed read method
(guard-tested in `tests/test_api_guard.py`). No SQL here — the token accessors
(`resolve_api_token`, `touch_api_token`) are `JobStorage` methods.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.paths import DB_PATH
from core.storage import JobStorage

logger = logging.getLogger("api.auth")


@dataclass
class AuthContext:
    user_id: str
    scopes: list[str]
    token_id: str


# In-memory throttle maps (FR-005 / FR-014). Per-process only — acceptable for
# a single `api` container; the real rate limit arrives with public exposure.
_last_touch: dict[str, float] = {}
_failed_auth: dict[str, int] = {}

# OpenAPI security scheme: declares `HTTPBearer` in /api/docs so every /v1 route
# shows the lock. `auto_error=False` — a missing/non-Bearer header yields None and
# our own logic below decides the 401/403, never HTTPBearer's default error.
bearer_scheme = HTTPBearer(auto_error=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(request: Request) -> str:
    """DB path for this app instance (tests override `app.state.db_path`)."""
    return getattr(request.app.state, "db_path", DB_PATH)


def _log_failed_auth(request: Request, reason: str) -> None:
    client = request.client.host if request.client else "unknown"
    _failed_auth[client] = _failed_auth.get(client, 0) + 1
    logger.info(
        "auth_failed client_addr=%r route=%r reason=%r", client, request.url.path, reason
    )


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc)
    except ValueError:
        return True  # malformed expiry → treat as expired


def get_db(request: Request) -> JobStorage:
    """Unbound storage for auth resolution (the single unbound site)."""
    return JobStorage(_db_path(request))


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: JobStorage = Depends(get_db),
) -> AuthContext:
    """Resolve `Authorization: Bearer <token>` to an `AuthContext`, else 401.

    `credentials` comes from `bearer_scheme` (auto_error=False), so a missing or
    non-Bearer header yields None and we raise our own 401 — HTTPBearer's default
    error never runs.
    """
    token = (credentials.credentials if credentials else "").strip()
    if not token:
        _log_failed_auth(request, "missing")
        raise HTTPException(status_code=401, detail="Missing bearer token")

    row = db.resolve_api_token(token)
    if row is None:
        _log_failed_auth(request, "unknown")
        raise HTTPException(status_code=401, detail="Invalid token")
    if row["revoked_at"] is not None:
        _log_failed_auth(request, "revoked")
        raise HTTPException(status_code=401, detail="Token revoked")
    if _is_expired(row["expires_at"]):
        _log_failed_auth(request, "expired")
        raise HTTPException(status_code=401, detail="Token expired")

    token_id = row["id"]
    now = time.monotonic()
    if now - _last_touch.get(token_id, 0.0) >= 60.0:
        _last_touch[token_id] = now
        db.touch_api_token(token_id)

    return AuthContext(user_id=row["user_id"], scopes=row["scopes"], token_id=token_id)


def get_user_db(
    request: Request,
    current_user: AuthContext = Depends(get_current_user),
) -> JobStorage:
    """Bound storage — the data-access seam the routers use."""
    return JobStorage(_db_path(request), user_id=current_user.user_id)


def require_scope(scope: str):
    """Dependency factory: 403 unless `scope` is in the token's scopes."""
    def _dependency(
        current_user: AuthContext = Depends(get_current_user),
    ) -> AuthContext:
        if scope not in current_user.scopes:
            raise HTTPException(status_code=403, detail="Insufficient scope")
        return current_user

    return _dependency


# Task-queue scopes (spec 043): `tasks:run` for user-space extract/score;
# `tasks:pipeline` is an *additional* grant for catalogue-wide scrape work.
tasks_run = require_scope("tasks:run")
tasks_pipeline = require_scope("tasks:pipeline")
