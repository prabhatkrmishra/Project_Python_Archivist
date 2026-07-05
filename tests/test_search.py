"""Tests for Qdrant search client and CLI search command."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from archivist.ingestion.extractors import iter_files
from archivist.ingestion.pipeline import ingest_file
from archivist.ingestion.tracker import Tracker
from archivist.search.qdrant_client import ensure_collection, search, delete_points, get_stats
from archivist.vectorizer.hashing_tfidf import vectorize
from qdrant_client import QdrantClient

TEST_DIR = Path("./test_docs")
COLLECTION = "archivist_docs"


def setup_module(module):
    TEST_DIR.mkdir(exist_ok=True)
    (TEST_DIR / "budget.txt").write_text("quarterly budget report 2024 financial summary")
    (TEST_DIR / "project.txt").write_text("project alpha milestones and timeline")
    (TEST_DIR / "random.txt").write_text("random notes about lunch and dinner")


def teardown_module(module):
    import shutil
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)


def _setup_qdrant(tmp_path: Path) -> tuple[QdrantClient, Tracker]:
    tracker = Tracker(tmp_path / "tracker.db")
    client = QdrantClient(path=str(tmp_path / "qdrant"), check_compatibility=False)
    ensure_collection(client, COLLECTION)
    for fp in iter_files(TEST_DIR):
        ingest_file(fp, tracker, chunk=False, client=client)
    return client, tracker


def test_search_returns_results():
    td = tempfile.mkdtemp(prefix="archivist_search_")
    try:
        client, tracker = _setup_qdrant(Path(td))
        q_vec = vectorize("quarterly budget")
        hits = search(client, COLLECTION, q_vec, limit=3)
        client.close()
        tracker.close()

        assert len(hits) >= 1
        assert hits[0].score > 0
        top_content = (hits[0].payload or {}).get("content", "").lower()
        assert "budget" in top_content or "quarterly" in top_content
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_search_limit():
    td = tempfile.mkdtemp(prefix="archivist_search_")
    try:
        client, tracker = _setup_qdrant(Path(td))
        q_vec = vectorize("project")
        hits = search(client, COLLECTION, q_vec, limit=1)
        client.close()
        tracker.close()

        assert len(hits) == 1
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_search_no_results():
    td = tempfile.mkdtemp(prefix="archivist_search_")
    try:
        client, tracker = _setup_qdrant(Path(td))
        q_vec = vectorize("xyznonexistentword12345")
        hits = search(client, COLLECTION, q_vec, limit=5)
        client.close()
        tracker.close()

        assert len(hits) == 0
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_delete_points():
    td = tempfile.mkdtemp(prefix="archivist_search_")
    try:
        client, tracker = _setup_qdrant(Path(td))
        q_vec = vectorize("budget")
        hits = search(client, COLLECTION, q_vec, limit=1)
        point_id = hits[0].id

        delete_points(client, COLLECTION, [point_id])
        hits_after = search(client, COLLECTION, q_vec, limit=1)
        client.close()
        tracker.close()

        assert len(hits_after) == 0 or hits_after[0].id != point_id
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_get_stats():
    td = tempfile.mkdtemp(prefix="archivist_search_")
    try:
        client, tracker = _setup_qdrant(Path(td))
        stats = get_stats(client, COLLECTION)
        client.close()
        tracker.close()

        assert stats["points_count"] >= 3
        assert "status" in stats
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)
