"""Document ingestion pipeline.

Orchestrates the extraction, vectorization, and storage of documents.
Uses SQLite FTS5 as the default and only search backend.
Supports multi-chunk ingestion for large files.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from archivist.ingestion.extractors import (
    normalize_for_display,
    chunk_text,
    should_chunk,
    extract_text,
    cumulative_line_offsets,
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

    Large files are split into multiple chunks (1500 lines each) and stored
    as separate FTS5 documents with line_offset metadata for accurate
    line-number display in search results.

    Args:
        path: File to ingest.
        tracker: SQLite tracker instance for idempotency.
        chunk: Chunk large files by page/section/lines.
        db_path: Override SQLite database path (for tests).

    Returns:
        Number of chunks created (usually 1 for small files).
    """
    path = path.resolve()
    if tracker.is_indexed(path):
        return 0

    raw = extract_text(path)
    display_text = normalize_for_display(raw)

    if not display_text.strip():
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        tracker.record(path, file_hash)
        return 0

    from archivist.search.sqlite_search import SQLiteSearch
    sq = SQLiteSearch(db_path or settings.sqlite_db)

    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    # Delete old chunks if re-ingesting the same file
    sq.delete_by_file_hash(file_hash)

    # Chunk the content
    if chunk and should_chunk(path, display_text):
        chunks = chunk_text(path, display_text)
    else:
        chunks = [display_text]

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stat = path.stat()

    offsets = cumulative_line_offsets(chunks)
    for i, chunk_content in enumerate(chunks):
        doc_id = f"{file_hash}_{i:04d}"
        line_offset = offsets[i]
        sq.upsert({
            "doc_id": doc_id,
            "filepath": str(path),
            "filename": path.name,
            "content": chunk_content,
            "line_offset": line_offset,
            "file_size": stat.st_size,
            "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
            "ingested_at": timestamp,
            "file_hash": file_hash,
        })

    sq.close()
    tracker.record(path, file_hash)
    return len(chunks)
