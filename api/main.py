"""api/main.py — FastAPI app factory (spec 041, US1 / US3).

Read-only API over `core`, reachable only through the tailnet. `/health` is
outside `/v1` and requires no token; everything else is versioned and scoped.

The app runs with `root_path="/api"` (Tailscale strips `/api` before forwarding
to `127.0.0.1:8601`), so generated URLs (OpenAPI, docs) carry the prefix.
"""
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.storage import ProfileNotFoundError
from api.deps import get_user_db, require_scope
from api.logging_config import configure_logging
from api.middleware import RequestSizeLimitMiddleware
from api.routers import catalogue, me
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
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=_git_sha())

    # One router per domain (Constitution: "monolithe modulaire").
    app.include_router(catalogue.router, prefix="/v1", tags=["catalogue"])
    app.include_router(me.router, prefix="/v1/me", tags=["me"])

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

    return app


app = create_app()
