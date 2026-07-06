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
    """SQLite FTS5 search backend.

    Provides keyword search with BM25 ranking, perfect for local use.

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
                line_offset INTEGER DEFAULT 0,
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
        # Migrate existing databases that lack line_offset column
        try:
            self.conn.execute("SELECT line_offset FROM documents LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE documents ADD COLUMN line_offset INTEGER DEFAULT 0")
            self.conn.commit()

    def upsert(self, payload: dict) -> str:
        """Insert or replace a document.

        Args:
            payload: Document metadata including filepath, content, line_offset, etc.

        Returns:
            Document ID string.
        """
        point_id = payload.get("id") or str(uuid.uuid4())
        self.conn.execute(
            """INSERT OR REPLACE INTO documents
               (id, filepath, filename, content, line_offset, file_size,
                modified_at, ingested_at, file_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                point_id,
                payload["filepath"],
                payload.get("filename", ""),
                payload["content"],
                payload.get("line_offset", 0),
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

    def search(self, query: str, limit: int = 10, all_chunks: bool = False) -> list[dict]:
        """Search documents using FTS5 BM25 ranking.

        By default returns the best matching chunk per file to avoid
        duplicate results from the same file. Use all_chunks=True to
        return every matching chunk across all files (no limit).

        Args:
            query: Search query string.
            limit: Maximum number of results (ignored when all_chunks=True).
            all_chunks: If True, return all matching chunks instead of
                       deduplicating per-file.

        Returns:
            List of result dictionaries with id, filepath, content, line_offset, score.
        """
        fts_query = self._escape_fts(query)

        if all_chunks:
            # No LIMIT — return every matching chunk
            rows = self.conn.execute(
                """SELECT d.id, d.filepath, d.filename, d.content,
                          d.line_offset, d.file_size, d.modified_at,
                          d.ingested_at, d.file_hash,
                          bm25(documents_fts, 1.0, 1.0) as rank
                   FROM documents_fts f
                   JOIN documents d ON d.id = f.id
                   WHERE documents_fts MATCH ?
                   ORDER BY rank""",
                (fts_query,),
            ).fetchall()

            results = []
            for row in rows:
                raw_rank = row[9] if row[9] is not None else 0.0
                score = min(1.0, max(0.0, abs(raw_rank) / (1.0 + abs(raw_rank))))
                results.append({
                    "id": row[0],
                    "filepath": row[1],
                    "filename": row[2],
                    "content": row[3],
                    "line_offset": row[4],
                    "file_size": row[5],
                    "modified_at": row[6],
                    "ingested_at": row[7],
                    "file_hash": row[8],
                    "score": round(score, 4),
                })
            return results

        # Deduplicate: keep best-scoring chunk per filepath
        # Fetch more rows than needed to cover many unique files
        fetch_limit = max(limit * 100, 1000)
        rows = self.conn.execute(
            """SELECT d.id, d.filepath, d.filename, d.content,
                      d.line_offset, d.file_size, d.modified_at,
                      d.ingested_at, d.file_hash,
                      bm25(documents_fts, 1.0, 1.0) as rank
               FROM documents_fts f
               JOIN documents d ON d.id = f.id
               WHERE documents_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (fts_query, fetch_limit),
        ).fetchall()

        best_per_file: dict[str, dict] = {}
        for row in rows:
            raw_rank = row[9] if row[9] is not None else 0.0
            # BM25 rank is negative (more negative = better). Normalize to 0-1 where 1 = best.
            score = min(1.0, max(0.0, abs(raw_rank) / (1.0 + abs(raw_rank))))
            filepath = row[1]
            if filepath not in best_per_file or score > best_per_file[filepath]["score"]:
                best_per_file[filepath] = {
                    "id": row[0],
                    "filepath": filepath,
                    "filename": row[2],
                    "content": row[3],
                    "line_offset": row[4],
                    "file_size": row[5],
                    "modified_at": row[6],
                    "ingested_at": row[7],
                    "file_hash": row[8],
                    "score": round(score, 4),
                }

        results = sorted(best_per_file.values(), key=lambda r: -r["score"])
        return results[:limit]

    def delete(self, point_id: str):
        """Delete a document by ID.

        Args:
            point_id: Document ID to delete.
        """
        self.conn.execute("DELETE FROM documents_fts WHERE id = ?", (point_id,))
        self.conn.execute("DELETE FROM documents WHERE id = ?", (point_id,))
        self.conn.commit()

    def delete_by_file_hash(self, file_hash: str):
        """Delete all chunks belonging to a file.

        Args:
            file_hash: SHA-256 hash of the file to delete all chunks for.
        """
        ids = [r[0] for r in self.conn.execute(
            "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchall()]
        if ids:
            placeholders = ",".join("?" * len(ids))
            self.conn.execute(f"DELETE FROM documents_fts WHERE id IN ({placeholders})", ids)
            self.conn.execute(f"DELETE FROM documents WHERE id IN ({placeholders})", ids)
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
        """Escape FTS5 special characters and build query.

        Uses prefix matching so partial token matches work (e.g. searching
        for "ShadowTracker" matches "ShadowTrackerExtra" in the index).

        Args:
            query: Raw search query.

        Returns:
            FTS5-compatible query string.
        """
        terms = [re.escape(t.lower()) for t in query.split() if t.strip()]
        if not terms:
            return '""'
        # Use prefix matching: "shadowtracker*" matches "shadowtrackerextra"
        return " ".join(f"{t}*" for t in terms)
