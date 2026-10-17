"""api/logging_config.py — central logging config for the API.

uvicorn's default `LOGGING_CONFIG` only configures `uvicorn.*`. Any `api.*`
logger (`api.write` audit line, spec 042 FR-007; `api.auth` failed-auth line,
spec 041 FR-014) has no handler and no level, so its INFO records propagate to
the root logger (default WARNING) and are silently dropped.

`configure_logging()` attaches a single stdout handler to the `api` logger so
every `api.*` record is emitted exactly once — `propagate=False` guarantees it
never also reaches a root handler. uvicorn's loggers are left untouched: we
never name them here, and `disable_existing_loggers=False` means uvicorn's own
later `dictConfig` keeps this logger too.
"""
import logging.config

_LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "api": {
            # Message bodies are already `key=value`; this prefixes a timestamp,
            # level and logger name for a greppable audit line.
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "api": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "api",
        },
    },
    "loggers": {
        "api": {
            "level": "INFO",
            "handlers": ["api"],
            "propagate": False,
        },
    },
}

_configured = False


def configure_logging() -> None:
    """Wire the `api.*` loggers to stdout once per process (idempotent).

    Called by `api.main.create_app()`. A second call is a no-op so tests that
    rebuild the app don't stack handlers.
    """
    global _configured
    if _configured:
        return
    logging.config.dictConfig(_LOGGING_CONFIG)
    _configured = True
