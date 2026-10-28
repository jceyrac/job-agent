"""api/main.py — FastAPI app factory (spec 041, US1 / US3).

Read-only API over `core`, reachable only through the tailnet. `/health` is
outside `/v1` and requires no token; everything else is versioned and scoped.

The app runs with `root_path="/api"` (Tailscale strips `/api` before forwarding
to `127.0.0.1:8601`), so generated URLs (OpenAPI, docs) carry the prefix.
"""
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.storage import (
    PURGE_STALE_DAYS,
    JobStorage,
    ProfileNotFoundError,
    JobNotFoundError,
)
from api.deps import get_db, get_user_db, require_scope
from api.logging_config import configure_logging
from api.middleware import RequestSizeLimitMiddleware
from api.routers import catalogue, ingest, me, tasks
from api.schemas import ApiModel

_ERROR_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    413: "payload_too_large",
    422: "validation_error",
}


class HealthResponse(ApiModel):
    status: str
    version: str
    purge_last_success: str | None = None
    warnings: list[str] = []


def _purge_warning(last_success: str | None, *, now=None,
                   stale_days: int = PURGE_STALE_DAYS) -> str | None:
    """Return a warning string when the last purge is missing or stale."""
    if not last_success:
        return "no successful purge recorded"
    try:
        last = datetime.fromisoformat(last_success)
    except ValueError:
        return "purge.last_success is unparseable"
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)  # assume UTC, like the writer
    now = now or datetime.now(timezone.utc)
    if now - last > timedelta(days=stale_days):
        return f"last successful purge >{stale_days}d ago"
    return None


def _git_sha() -> str:
    """Version = the image's git SHA, baked in at build time via the `GIT_SHA`
    build arg (the image has no `.git`). Unset → "unknown"."""
    return os.environ.get("GIT_SHA") or "unknown"


def create_app() -> FastAPI:
    # Wire the `api.*` loggers (write audit + failed-auth) to stdout. Runs before
    # uvicorn applies its own LOGGING_CONFIG; both are `disable_existing_loggers=
    # False`, so uvicorn's loggers stay unchanged and this logger survives.
    configure_logging()

    app = FastAPI(
        title="job_agent API",
        version=_git_sha(),
        root_path="/api",
        docs_url="/docs",
        openapi_url="/openapi.json",
        swagger_ui_parameters={"persistAuthorization": False},
    )
    app.add_middleware(RequestSizeLimitMiddleware)

    @app.get("/health", response_model=HealthResponse)
    def health(db: JobStorage = Depends(get_db)) -> HealthResponse:
        try:
            last_success = db.get_purge_last_success()
        except Exception:
            # DB unavailable → still 200/ok, but surface it as a warning.
            return HealthResponse(status="ok", version=_git_sha(),
                                  warnings=["purge status unavailable"])
        warning = _purge_warning(last_success)
        return HealthResponse(status="ok", version=_git_sha(),
                              purge_last_success=last_success,
                              warnings=[warning] if warning else [])

    # One router per domain (Constitution: "monolithe modulaire").
    app.include_router(catalogue.router, prefix="/v1", tags=["catalogue"])
    app.include_router(me.router, prefix="/v1/me", tags=["me"])
    app.include_router(tasks.router, prefix="/v1/me/tasks", tags=["tasks"])
    app.include_router(ingest.router, prefix="/v1/ingest", tags=["ingest"])

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": _ERROR_CODES.get(exc.status_code, "error"),
                "message": str(exc.detail),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exc_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": "validation_error", "message": "Invalid request"},
        )

    @app.exception_handler(ProfileNotFoundError)
    async def _profile_not_found_handler(request: Request, exc: ProfileNotFoundError):
        # A profile_id that isn't the caller's is indistinguishable from missing
        # (Constitution VII — no existence oracle). Map to 404, never 403.
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": "Profile not found"},
        )

    @app.exception_handler(JobNotFoundError)
    async def _job_not_found_handler(request: Request, exc: JobNotFoundError):
        # A score_job for an unknown job is rejected at enqueue (spec 044).
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "message": "Job not found"},
        )

    return app


app = create_app()
