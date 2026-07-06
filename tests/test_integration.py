"""Integration test: ingest small dir → search → verify top-10 relevance.

Uses SQLite FTS5 as the search backend.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from archivist.ingestion.extractors import iter_files
from archivist.ingestion.pipeline import ingest_file
from archivist.ingestion.tracker import Tracker
from archivist.search.sqlite_search import SQLiteSearch

TEST_DIR = Path("./test_docs")


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
        db_path = Path(td) / "archivist.db"

        files = list(iter_files(TEST_DIR))
        for fp in files:
            ingest_file(fp, tracker, chunk=False, db_path=db_path)

        sq = SQLiteSearch(db_path)
        results = sq.search("quarterly budget", limit=3)
        sq.close()
        tracker.close()

        assert len(results) >= 1
        top_content = results[0].get("content", "").lower()
        assert "budget" in top_content or "quarterly" in top_content
        assert results[0].get("score", 0) > 0
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)
