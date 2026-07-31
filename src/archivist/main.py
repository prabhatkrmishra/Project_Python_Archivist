"""FastAPI application factory for Archivist.

Creates and configures the FastAPI application with routes and middleware.
"""

from __future__ import annotations

import logging
import uuid

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars

from archivist.api.routes import router
from archivist.config import get_settings


def configure_logging(level: str = "INFO") -> None:
    """Configure structured logging for the application.

    Uses the stdlib logging integration so third-party loggers (uvicorn and
    friends) flow through the same pipeline; our own loggers get key/value
    fields, timestamps, and readable console output.

    Args:
        level: Logging level as a string, e.g. "INFO", "DEBUG".
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, format="%(message)s")
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _cors_origins(raw: str) -> list[str]:
    """Parse the CORS origins setting into a list.

    ``*`` keeps the permissive local-dev default; anything else is treated
    as a comma-separated allowlist (e.g. ``http://localhost:5173``) so a
    deployed instance can be locked down without code changes.

    Args:
        raw: Raw setting value.

    Returns:
        List of allowed origins.
    """
    if raw.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Archivist",
        description="Offline document search tool",
        version="0.1.0",
    )

    # The frontend is served on its own origin (e.g. localhost:3000) while
    # the API runs on 127.0.0.1:8000, so cross-origin requests need explicit
    # CORS headers or the browser silently blocks the response. Allowlisted
    # via ARCHIVIST_CORS_ORIGINS; "*" is the default for local development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(request, call_next):
        """Tag every log line with the request id so a single HTTP call is
        traceable end to end. A caller-supplied X-Request-ID wins so clients
        can correlate their own ids."""
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        clear_contextvars()
        bind_contextvars(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
