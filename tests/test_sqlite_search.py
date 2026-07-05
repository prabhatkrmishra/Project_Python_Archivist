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
    assert results[0]["score"] > 0
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
