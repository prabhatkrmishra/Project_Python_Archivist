"""Tests for SQLite FTS5 backend."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from archivist.search.sqlite_search import SQLiteSearch


@pytest.fixture
def tmp_db(tmp_path: Path):
    return tmp_path / "test.db"


def test_upsert_and_search(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    sq.upsert({
        "filepath": "/docs/report.txt",
        "filename": "report.txt",
        "content": "quarterly budget analysis shows revenue growth",
        "file_size": 1024,
        "modified_at": "2026-01-01T00:00:00Z",
        "ingested_at": "2026-01-01T00:00:00Z",
        "file_hash": "abc123",
    })
    results = sq.search("budget")
    assert len(results) == 1
    assert results[0]["filepath"] == "/docs/report.txt"
    assert results[0]["score"] >= 0
    sq.close()


def test_search_no_match(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    sq.upsert({
        "filepath": "/docs/a.txt",
        "filename": "a.txt",
        "content": "hello world",
        "file_size": 100,
        "modified_at": "2026-01-01T00:00:00Z",
        "ingested_at": "2026-01-01T00:00:00Z",
        "file_hash": "x",
    })
    results = sq.search("zzz_no_match")
    assert len(results) == 0
    sq.close()


def test_delete(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    sq.upsert({
        "filepath": "/docs/b.txt",
        "filename": "b.txt",
        "content": "test content",
        "file_size": 50,
        "modified_at": "2026-01-01T00:00:00Z",
        "ingested_at": "2026-01-01T00:00:00Z",
        "file_hash": "y",
    })
    results = sq.search("test")
    assert len(results) == 1
    sq.delete(results[0]["id"])
    results = sq.search("test")
    assert len(results) == 0
    sq.close()


def test_delete_all(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    for i in range(5):
        sq.upsert({
            "filepath": f"/docs/{i}.txt",
            "filename": f"{i}.txt",
            "content": f"document number {i}",
            "file_size": 100,
            "modified_at": "2026-01-01T00:00:00Z",
            "ingested_at": "2026-01-01T00:00:00Z",
            "file_hash": f"hash{i}",
        })
    results = sq.search("document")
    assert len(results) == 5
    sq.delete_all()
    results = sq.search("document")
    assert len(results) == 0
    sq.close()


def test_stats(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    stats = sq.stats()
    assert stats["points_count"] == 0
    assert stats["backend"] == "sqlite-fts5"
    sq.close()


def test_unicode_content(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    sq.upsert({
        "filepath": "/docs/unicode.txt",
        "filename": "unicode.txt",
        "content": "café résumé naïve über",
        "file_size": 100,
        "modified_at": "2026-01-01T00:00:00Z",
        "ingested_at": "2026-01-01T00:00:00Z",
        "file_hash": "uni",
    })
    results = sq.search("café")
    assert len(results) == 1
    sq.close()


def test_large_content(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    big_content = "word " * 10000
    sq.upsert({
        "filepath": "/docs/big.txt",
        "filename": "big.txt",
        "content": big_content,
        "file_size": 50000,
        "modified_at": "2026-01-01T00:00:00Z",
        "ingested_at": "2026-01-01T00:00:00Z",
        "file_hash": "big",
    })
    results = sq.search("word")
    assert len(results) == 1
    sq.close()


def test_line_offset_stored_and_returned(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    sq.upsert({
        "filepath": "/docs/chunk.py",
        "filename": "chunk.py",
        "content": "def hello():\n    return 'world'",
        "line_offset": 500,
        "file_size": 1024,
        "modified_at": "2026-01-01T00:00:00Z",
        "ingested_at": "2026-01-01T00:00:00Z",
        "file_hash": "chunk1",
    })
    results = sq.search("hello")
    assert len(results) == 1
    assert results[0]["line_offset"] == 500
    sq.close()


def test_line_offset_zero_default(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    sq.upsert({
        "filepath": "/docs/noinfo.txt",
        "filename": "noinfo.txt",
        "content": "test content here",
        "file_size": 100,
        "modified_at": "2026-01-01T00:00:00Z",
        "ingested_at": "2026-01-01T00:00:00Z",
        "file_hash": "nooffset",
    })
    results = sq.search("test")
    assert len(results) == 1
    assert results[0]["line_offset"] == 0
    sq.close()


def test_delete_by_file_hash(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    # Insert 3 chunks for same file
    for i in range(3):
        sq.upsert({
            "filepath": "/docs/multichunk.py",
            "filename": "multichunk.py",
            "content": f"chunk {i} content",
            "line_offset": i * 500,
            "file_size": 5000,
            "modified_at": "2026-01-01T00:00:00Z",
            "ingested_at": "2026-01-01T00:00:00Z",
            "file_hash": "multi123",
        })
    # Dedup: only 1 result per file
    results = sq.search("chunk")
    assert len(results) == 1
    assert results[0]["filepath"] == "/docs/multichunk.py"

    # Delete by hash
    sq.delete_by_file_hash("multi123")

    # All gone
    results = sq.search("chunk")
    assert len(results) == 0
    sq.close()


def test_delete_by_file_hash_only_removes_matching(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    sq.upsert({
        "filepath": "/docs/a.py",
        "filename": "a.py",
        "content": "alpha content",
        "file_size": 100,
        "modified_at": "2026-01-01T00:00:00Z",
        "ingested_at": "2026-01-01T00:00:00Z",
        "file_hash": "hash_a",
    })
    sq.upsert({
        "filepath": "/docs/b.py",
        "filename": "b.py",
        "content": "beta content",
        "file_size": 200,
        "modified_at": "2026-01-01T00:00:00Z",
        "ingested_at": "2026-01-01T00:00:00Z",
        "file_hash": "hash_b",
    })

    sq.delete_by_file_hash("hash_a")
    results = sq.search("alpha")
    assert len(results) == 0
    results = sq.search("beta")
    assert len(results) == 1
    sq.close()


def test_search_all_chunks_returns_multiple_per_file(tmp_db: Path):
    sq = SQLiteSearch(tmp_db)
    for i in range(3):
        sq.upsert({
            "filepath": "/docs/big.py",
            "filename": "big.py",
            "content": f"function number {i} here",
            "line_offset": i * 500,
            "file_size": 5000,
            "modified_at": "2026-01-01T00:00:00Z",
            "ingested_at": "2026-01-01T00:00:00Z",
            "file_hash": "same_hash",
        })
    # Default: deduped to 1
    results = sq.search("function")
    assert len(results) == 1

    # all_chunks: returns all 3
    results = sq.search("function", all_chunks=True)
    assert len(results) == 3
    offsets = {r["line_offset"] for r in results}
    assert offsets == {0, 500, 1000}
    sq.close()
