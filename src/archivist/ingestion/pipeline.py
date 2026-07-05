"""Document ingestion pipeline.

Orchestrates the extraction, vectorization, and storage of documents.
Supports both SQLite FTS5 (default) and Qdrant backends.
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
    use_bm25: bool = False,
    chunk: bool = True,
    client=None,
    qdrant_url: str | None = None,
    qdrant_api_key: str | None = None,
    qdrant_collection: str | None = None,
) -> int:
    """Extract, vectorize, and store a single file.

    Args:
        path: File to ingest.
        tracker: SQLite tracker instance for idempotency.
        use_bm25: Use Qdrant native BM25 instead of HashingVectorizer.
        chunk: Chunk large files by page/section.
        client: Optional QdrantClient (for tests / batching).
        qdrant_url: Override Qdrant URL.
        qdrant_api_key: Override Qdrant API key.
        qdrant_collection: Override collection name.

    Returns:
        Number of vectors created (usually 1).
    """
    from qdrant_client import QdrantClient as QC
    from archivist.search.qdrant_client import ensure_collection, build_point, upsert_points
    from archivist.vectorizer.hashing_tfidf import vectorize

    path = path.resolve()
    if tracker.is_indexed(path):
        return 0

    raw = extract_text(path)
    text = normalize_text(raw)
    display_text = normalize_for_display(raw)
    texts = chunk_text(path, display_text) if (chunk and should_chunk(path, display_text)) else [display_text]
    n_vectors = len(texts)

    owns_client = client is None
    url = qdrant_url or str(settings.qdrant_url)
    collection = qdrant_collection or settings.qdrant_collection
    if owns_client:
        client = QC(url=url, api_key=qdrant_api_key or settings.qdrant_api_key)
    ensure_collection(client, collection)

    points = []
    for chunk_text_content in texts:
        vec = vectorize(chunk_text_content, use_bm25=use_bm25)
        payload = {
            "filepath": str(path),
            "filename": path.name,
            "content": chunk_text_content,
            "file_size": path.stat().st_size,
            "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime)),
            "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "file_hash": sha256_file(path),
        }
        points.append(build_point(vec, payload))

    upsert_points(client, collection, points)
    tracker.record(path, points[0].id)

    if owns_client:
        client.close()
    return n_vectors
