"""FastAPI application factory for Archivist.

Creates and configures the FastAPI application with routes and middleware.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

    # Frontend is served on its own origin (e.g. localhost:3000) while the
    # API runs on 127.0.0.1:8000, so cross-origin requests need explicit
    # CORS headers or the browser silently blocks the response (this is
    # what was breaking file/folder/archive uploads and search from the UI).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
