"""FastAPI application factory for Archivist API."""
from __future__ import annotations

from fastapi import FastAPI

from .routes import router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="Archivist API", version="0.1.0")
    app.include_router(router)
    return app
