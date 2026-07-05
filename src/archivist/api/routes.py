"""API route handlers for Archivist.

FastAPI endpoints for document ingestion, search, and management.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile

from archivist.config import get_settings
from archivist.ingestion.extractors import normalize_for_display
from archivist.ingestion.tracker import Tracker
from archivist.search.sqlite_search import SQLiteSearch
from archivist.utils.text import extract_snippet

router = APIRouter(prefix="/api/v1", tags=["archivist"])
settings = get_settings()


@router.get("/search")
async def search(
    q: str = Query(..., description="Search query"),
    size: int = Query(10, ge=1, le=100, description="Number of results"),
):
    """Search ingested documents.

    Returns documents matching the query with relevance scores.
    """
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        results = sq.search(q, limit=size)
    finally:
        sq.close()

    return {
        "query": q,
        "results": [
            {
                "id": r["id"],
                "filepath": r["filepath"],
                "score": r["score"],
                "snippet": extract_snippet(r["content"], q),
            }
            for r in results
        ],
    }


@router.get("/status")
async def status():
    """Get index statistics."""
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        stats = sq.stats()
    finally:
        sq.close()

    tracker = Tracker(settings.tracker_db)
    tracker_stats = tracker.stats()
    tracker.close()

    return {**stats, **tracker_stats}


@router.post("/ingest/file")
async def ingest_file(file: UploadFile = File(...)):
    """Ingest a single file."""
    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    sq = SQLiteSearch(settings.sqlite_db)
    try:
        sq.upsert({
            "filepath": file.filename or "unknown",
            "filename": file.filename or "unknown",
            "content": normalize_for_display(text)[:50_000],
            "file_size": len(content),
            "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "file_hash": "",
        })
    finally:
        sq.close()

    return {"status": "ok", "filename": file.filename}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    """Delete a document by ID."""
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        sq.delete(doc_id)
    finally:
        sq.close()
    return {"status": "deleted", "id": doc_id}
