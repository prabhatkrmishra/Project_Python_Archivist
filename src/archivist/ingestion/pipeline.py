"""Document ingestion pipeline.

Orchestrates the extraction, vectorization, and storage of documents.
Uses SQLite FTS5 as the default and only search backend.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from archivist.ingestion.extractors import (
    sha256_file,
    normalize_text,
    normalize_for_display,
    chunk_text,
    should_chunk,
    iter_files,
    extract_text,
)
from archivist.ingestion.tracker import Tracker
from archivist.config import get_settings


settings = get_settings()


def ingest_file(
    path: Path,
    tracker: Tracker,
    chunk: bool = True,
    db_path: str | Path | None = None,
) -> int:
    """Extract and store a single file in SQLite FTS5.

    Args:
        path: File to ingest.
        tracker: SQLite tracker instance for idempotency.
        chunk: Chunk large files by page/section.
        db_path: Override SQLite database path (for tests).

    Returns:
        Number of vectors created (usually 1).
    """
    path = path.resolve()
    if tracker.is_indexed(path):
        return 0

    raw = extract_text(path)
    display_text = normalize_for_display(raw)
    content = display_text[:50_000]  # FTS5 size limit

    if not content.strip():
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        tracker.record(path, file_hash)
        return 0

    from archivist.search.sqlite_search import SQLiteSearch
    sq = SQLiteSearch(db_path or settings.sqlite_db)

    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    sq.upsert({
        "filepath": str(path),
        "filename": path.name,
        "content": content,
        "file_size": path.stat().st_size,
        "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)),
        "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "file_hash": file_hash,
    })
    sq.close()
    tracker.record(path, file_hash)
    return 1
