"""FastAPI application factory for Archivist.

Creates and configures the FastAPI application with routes and middleware.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from archivist.api.routes import router
from archivist.config import get_settings


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

    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
