"""Integration test: ingest small dir → search → verify top-10 relevance.

Uses Qdrant with temp-disk storage so ingest and search share the same data.
No running Qdrant server required.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from archivist.ingestion.extractors import iter_files
from archivist.ingestion.pipeline import ingest_file
from archivist.ingestion.tracker import Tracker
from archivist.search.qdrant_client import ensure_collection, search
from archivist.vectorizer.hashing_tfidf import vectorize
from qdrant_client import QdrantClient

TEST_DIR = Path("./test_docs")
COLLECTION = "archivist_docs"


def setup_module(module):
    TEST_DIR.mkdir(exist_ok=True)
    (TEST_DIR / "a.txt").write_text("quarterly budget report 2024")
    (TEST_DIR / "b.txt").write_text("project alpha milestones and timeline")
    (TEST_DIR / "c.txt").write_text("annual budget and financial summary")
    (TEST_DIR / "d.txt").write_text("random notes about lunch")


def teardown_module(module):
    import shutil
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)


def test_ingest_and_search():
    td = tempfile.mkdtemp(prefix="archivist_test_")
    try:
        tracker = Tracker(Path(td) / "tracker.db")
        qdrant_path = Path(td) / "qdrant_data"
        qdrant_path.mkdir()

        client = QdrantClient(path=str(qdrant_path), check_compatibility=False)
        ensure_collection(client, COLLECTION)

        files = list(iter_files(TEST_DIR))
        for fp in files:
            ingest_file(fp, tracker, chunk=False, client=client)

        q_vec = vectorize("quarterly budget")
        hits = search(client, COLLECTION, q_vec, limit=3)
        client.close()
        tracker.close()

        assert len(hits) >= 1
        top_content = (hits[0].payload or {}).get("content", "").lower()
        assert "budget" in top_content or "quarterly" in top_content
        assert hits[0].score > 0
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)
