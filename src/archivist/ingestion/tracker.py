"""File ingestion tracker using SQLite.

Tracks which files have been indexed by their SHA-256 hash to enable
idempotent re-runs (skip already-indexed files).
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import NamedTuple


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS files (
    file_hash       TEXT PRIMARY KEY,
    filepath        TEXT NOT NULL,
    file_size       INTEGER,
    modified        REAL,
    ingested        REAL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_filepath ON files(filepath);
"""


class FileRecord(NamedTuple):
    """Represents an indexed file record."""
    file_hash: str
    filepath: str
    file_size: int | None
    modified: float | None
    ingested: float | None


class Tracker:
    """SQLite-based tracker for indexed files.

    Uses SHA-256 file hashes as primary keys to enable idempotent
    ingestion (skipping already-indexed files).

    Args:
        db_path: Path to SQLite database file.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(CREATE_TABLE_SQL)
        self._conn.commit()

    def is_indexed(self, path: Path) -> bool:
        """Check if a file has already been indexed.

        Args:
            path: Path to file to check.

        Returns:
            True if file hash exists in tracker.
        """
        try:
            file_hash = _hash(path)
        except PermissionError:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM files WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return row is not None

    def record(self, path: Path, file_hash: str | None = None) -> None:
        """Record a successfully ingested file.

        Args:
            path: Path to ingested file.
            file_hash: Optional pre-computed file hash.
        """
        if file_hash is None:
            file_hash = _hash(path)
        stat = path.stat()
        self._conn.execute(
            """INSERT OR REPLACE INTO files
               (file_hash, filepath, file_size, modified, ingested)
               VALUES (?, ?, ?, ?, ?)""",
            (
                file_hash,
                str(path.resolve()),
                stat.st_size,
                stat.st_mtime,
                time.time(),
            ),
        )
        self._conn.commit()

    def stats(self) -> dict:
        """Get tracker statistics.

        Returns:
            Dictionary with indexed_files count.
        """
        total = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {"indexed_files": total}

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def _hash(path: Path) -> str:
    """Compute SHA-256 hash of a file.

    Args:
        path: Path to file.

    Returns:
        Hex digest string.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
