"""FastAPI application factory for Archivist.

Creates and configures the FastAPI application with routes and middleware.
"""

from __future__ import annotations

from fastapi import FastAPI

from archivist.api.routes import router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title="Archivist",
        description="Offline document search tool",
        version="0.1.0",
    )

    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
