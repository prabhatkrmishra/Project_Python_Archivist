"""API authentication dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from archivist.config import Settings, get_settings


def require_api_key(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Require a valid ``X-API-Key`` header when a key is configured.

    When ``ARCHIVIST_API_KEY`` is unset the API stays open (the local-tool
    default). When set, every request under this router must carry the
    matching key or it is rejected with 401.

    Args:
        request: Incoming request (used to read the header).
        settings: Application settings.

    Raises:
        HTTPException: If the key is required but missing or wrong.
    """
    if settings.api_key is None:
        return
    provided = request.headers.get("X-API-Key")
    if provided != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "API-Key"},
        )
