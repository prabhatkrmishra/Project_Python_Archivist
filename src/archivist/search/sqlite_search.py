"""SQLite FTS5 search backend.

Fast keyword search using SQLite's built-in Full-Text Search 5 extension.
Zero external services required — ideal for local development and small
to medium document collections.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path


class SQLiteSearch:
    """Drop-in replacement for Qdrant using SQLite FTS5.

    Provides keyword search with BM25 ranking, perfect for local use
    where Qdrant is not available or necessary.

    Args:
        db_path: Path to SQLite database file.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_tables()

    def _init_tables(self):
        """Create documents table and FTS5 virtual table."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filepath TEXT NOT NULL,
                filename TEXT,
                content TEXT NOT NULL,
                file_size INTEGER,
                modified_at TEXT,
                ingested_at TEXT,
                file_hash TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                id UNINDEXED,
                filepath,
                content,
                tokenize='porter unicode61'
            );
        """)

    def upsert(self, payload: dict) -> str:
        """Insert or replace a document.

        Args:
            payload: Document metadata including filepath, content, etc.

        Returns:
            Point ID string.
        """
        point_id = payload.get("id") or str(uuid.uuid4())
        self.conn.execute(
            """INSERT OR REPLACE INTO documents
               (id, filepath, filename, content, file_size, modified_at, ingested_at, file_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                point_id,
                payload["filepath"],
                payload.get("filename", ""),
                payload["content"],
                payload.get("file_size", 0),
                payload.get("modified_at", ""),
                payload.get("ingested_at", ""),
                payload.get("file_hash", ""),
            ),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO documents_fts(id, filepath, content) VALUES (?, ?, ?)",
            (point_id, payload["filepath"], payload["content"]),
        )
        self.conn.commit()
        return point_id

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search documents using FTS5 BM25 ranking.

        Args:
            query: Search query string.
            limit: Maximum number of results.

        Returns:
            List of result dictionaries with id, filepath, content, score.
        """
        fts_query = self._escape_fts(query)
        rows = self.conn.execute(
            """SELECT d.id, d.filepath, d.filename, d.content,
                      d.file_size, d.modified_at, d.ingested_at, d.file_hash,
                      bm25(documents_fts, 1.0, 1.0) as rank
               FROM documents_fts f
               JOIN documents d ON d.id = f.id
               WHERE documents_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (fts_query, limit),
        ).fetchall()

        results = []
        for row in rows:
            raw_rank = row[8] if row[8] is not None else 0.0
            score = min(1.0, max(0.0, 1.0 / (1.0 + abs(raw_rank))))
            results.append({
                "id": row[0],
                "filepath": row[1],
                "filename": row[2],
                "content": row[3],
                "file_size": row[4],
                "modified_at": row[5],
                "ingested_at": row[6],
                "file_hash": row[7],
                "score": round(score, 4),
            })
        return results

    def delete(self, point_id: str):
        """Delete a document by ID.

        Args:
            point_id: Document ID to delete.
        """
        self.conn.execute("DELETE FROM documents_fts WHERE id = ?", (point_id,))
        self.conn.execute("DELETE FROM documents WHERE id = ?", (point_id,))
        self.conn.commit()

    def delete_all(self):
        """Clear all documents from the database."""
        self.conn.execute("DELETE FROM documents_fts")
        self.conn.execute("DELETE FROM documents")
        self.conn.commit()

    def stats(self) -> dict:
        """Return collection statistics.

        Returns:
            Dictionary with points_count and backend info.
        """
        count = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        return {"points_count": count, "backend": "sqlite-fts5"}

    def close(self):
        """Close the database connection."""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def _escape_fts(query: str) -> str:
        """Escape FTS5 special characters and build OR query.

        Args:
            query: Raw search query.

        Returns:
            FTS5-compatible query string.
        """
        terms = [re.escape(t) for t in query.split() if t.strip()]
        if not terms:
            return '""'
        return " OR ".join(terms)
