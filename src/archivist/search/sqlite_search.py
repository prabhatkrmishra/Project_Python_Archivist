"""SQLite FTS5 search backend.

Fast keyword search using SQLite's built-in Full-Text Search 5 extension.
Zero external services required — ideal for local development and small
to medium document collections.

Uses external-content FTS5 with triggers for ~45% storage savings.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path


class SQLiteSearch:
    """SQLite FTS5 search backend.

    Provides keyword search with BM25 ranking, perfect for local use.

    Schema uses INTEGER PRIMARY KEY for documents and external-content FTS5
    with content_rowid='id' so FTS5 rowids match documents exactly.

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
        """Create documents table, external-content FTS5, and sync triggers."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT NOT NULL UNIQUE,
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
                doc_id UNINDEXED,
                filepath,
                content,
                content='documents',
                content_rowid='id',
                tokenize='porter unicode61 remove_diacritics 2',
                detail=full
            );
        """)
        self._ensure_triggers()

    def _ensure_triggers(self):
        """Create triggers to keep external-content FTS5 in sync.

        Uses FTS5's 'delete' command to safely remove old entries before
        the row disappears from the content table. This is required for
        external-content FTS5 — bare DELETE FROM documents_fts would cause
        'database disk image is malformed' because FTS5 tries to fetch
        content from a row that no longer exists.
        """
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, doc_id, filepath, content)
                VALUES (new.id, new.doc_id, new.filepath, new.content);
            END
        """)
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, doc_id, filepath, content)
                VALUES('delete', old.id, old.doc_id, old.filepath, old.content);
            END
        """)
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, doc_id, filepath, content)
                VALUES('delete', old.id, old.doc_id, old.filepath, old.content);
                INSERT INTO documents_fts(rowid, doc_id, filepath, content)
                VALUES (new.id, new.doc_id, new.filepath, new.content);
            END
        """)
        self.conn.commit()

    def upsert(self, payload: dict) -> int:
        """Insert a document.

        Args:
            payload: Document metadata including doc_id, filepath, content, etc.

        Returns:
            Integer row ID.
        """
        doc_id = payload["doc_id"]
        self.conn.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id, filepath, filename, content, line_offset, file_size,
                modified_at, ingested_at, file_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id,
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
        self.conn.commit()
        row_id = self.conn.execute(
            "SELECT id FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()[0]
        return row_id

    def search(self, query: str, limit: int = 10, all_chunks: bool = False) -> list[dict]:
        """Search documents using FTS5 BM25 ranking.

        By default returns the best matching chunk per file to avoid
        duplicate results from the same file. Use all_chunks=True to
        return every matching chunk across all files.

        Always returns the full matching set (deduplicated when
        all_chunks=False) so callers can compute an accurate total count
        and slice their own page out of it. `limit` only controls how
        many raw FTS rows are scanned when building the per-file
        dedup pool; it does not truncate the returned results.

        Args:
            query: Search query string.
            limit: Used to size the internal raw-row fetch pool for
                deduplication (ignored when all_chunks=True).
            all_chunks: If True, return all matching chunks instead of
                       deduplicating per-file.

        Returns:
            List of result dictionaries with doc_id, filepath, content, line_offset, score.
        """
        fts_query = self._escape_fts(query)

        if all_chunks:
            rows = self.conn.execute(
                """SELECT d.doc_id, d.filepath, d.filename, d.content,
                          d.line_offset, d.file_size, d.modified_at,
                          d.ingested_at, d.file_hash,
                          bm25(documents_fts, 1.0, 1.0) as rank
                   FROM documents_fts f
                   JOIN documents d ON d.id = f.rowid
                   WHERE documents_fts MATCH ?
                   ORDER BY rank""",
                (fts_query,),
            ).fetchall()

            results = []
            for row in rows:
                raw_rank = row[9] if row[9] is not None else 0.0
                score = min(1.0, max(0.0, abs(raw_rank) / (1.0 + abs(raw_rank))))
                results.append({
                    "doc_id": row[0],
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
        fetch_limit = max(limit * 100, 1000)
        rows = self.conn.execute(
            """SELECT d.doc_id, d.filepath, d.filename, d.content,
                      d.line_offset, d.file_size, d.modified_at,
                      d.ingested_at, d.file_hash,
                      bm25(documents_fts, 1.0, 1.0) as rank
               FROM documents_fts f
               JOIN documents d ON d.id = f.rowid
               WHERE documents_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (fts_query, fetch_limit),
        ).fetchall()

        best_per_file: dict[str, dict] = {}
        for row in rows:
            raw_rank = row[9] if row[9] is not None else 0.0
            score = min(1.0, max(0.0, abs(raw_rank) / (1.0 + abs(raw_rank))))
            filepath = row[1]
            if filepath not in best_per_file or score > best_per_file[filepath]["score"]:
                best_per_file[filepath] = {
                    "doc_id": row[0],
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
        return results

    def delete(self, doc_id: str):
        """Delete a document by doc_id string.

        Args:
            doc_id: Document doc_id to delete (e.g. 'abc123_0000').
        """
        self.conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        self.conn.commit()

    def delete_by_file_hash(self, file_hash: str):
        """Delete all chunks belonging to a file.

        Args:
            file_hash: SHA-256 hash of the file to delete all chunks for.
        """
        self.conn.execute("DELETE FROM documents WHERE file_hash = ?", (file_hash,))
        self.conn.commit()

    def delete_all(self):
        """Clear all documents from the database."""
        self.conn.execute("DELETE FROM documents")
        self.conn.commit()

    def stats(self) -> dict:
        """Return collection statistics.

        Returns:
            Dictionary with points_count, backend, unique_files,
            total_content_size_bytes, unique_extensions, last_ingested_at.
        """
        count = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        # Group by file_hash first so a file's size/extension/timestamp is
        # only counted once, not once per chunk.
        row = self.conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(file_size), 0), MAX(ingested_at)
            FROM (
                SELECT file_hash, MAX(file_size) AS file_size, MAX(ingested_at) AS ingested_at
                FROM documents
                GROUP BY file_hash
            )
            """
        ).fetchone()
        unique_files, total_size, last_ingested_at = row

        ext_rows = self.conn.execute("SELECT DISTINCT filepath FROM documents").fetchall()
        extensions = {Path(r[0]).suffix.lower() for r in ext_rows if Path(r[0]).suffix}

        return {
            "points_count": count,
            "backend": "sqlite-fts5",
            "unique_files": unique_files,
            "total_content_size_bytes": total_size,
            "unique_extensions": len(extensions),
            "last_ingested_at": last_ingested_at,
        }

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

        FTS5 has strict syntax rules:
        - `-` is AND NOT (triggers column lookup when followed by digits)
        - `+` is require operator
        - `@` `/` `.` `$` `%` `!` `#` `<` `>` cause syntax errors
        - `*` is prefix operator (can't use inside quotes)
        - `:` is column filter

        Strategy: replace dangerous chars with spaces so "ISBN-9780000000014"
        becomes "isbn 9780000000014" and matches both tokens.

        Args:
            query: Raw search query.

        Returns:
            FTS5-compatible query string.
        """
        # Replace FTS5-dangerous characters with spaces
        # - → AND NOT (column lookup with digits)
        # + → require operator
        # @ / → syntax errors
        # * → prefix operator (we add our own)
        # " ( ) → grouping/phrase
        # \ → escape char (causes "syntax error near \")
        _FTS_DANGEROUS = re.compile(r'[-+@"()/\\*$.%&!#<>=]')
        terms = []
        for t in query.split():
            t = t.strip()
            if not t:
                continue
            # Replace dangerous chars with space, collapse multiple spaces
            t = _FTS_DANGEROUS.sub(' ', t).strip()
            t = re.sub(r'\s+', ' ', t)
            if t:
                terms.append(t.lower())
        if not terms:
            return '""'
        # Each term gets prefix matching, joined by implicit AND
        return " ".join(f"{t}*" for t in terms)