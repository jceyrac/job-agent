"""api/write_log.py — structured write audit line (spec 042, FR-007).

One line per successful write: token id, user id, route, resource id and status —
never the body, never the token value. Observability only (no storage write).
"""
import logging

from fastapi import Request

from api.deps import AuthContext

logger = logging.getLogger("api.write")


def log_write(
    request: Request,
    auth: AuthContext,
    *,
    resource_id: str | int | None = None,
    status: int = 200,
) -> None:
    logger.info(
        "write_ok token_id=%s user_id=%s route=%s %s resource_id=%s status=%s",
        auth.token_id,
        auth.user_id,
        request.method,
        request.url.path,
        resource_id if resource_id is not None else "-",
        status,
    )
