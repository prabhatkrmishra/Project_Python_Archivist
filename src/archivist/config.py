"""Configuration management for Archivist.

Uses pydantic-settings to load configuration from:
1. Environment variables (highest priority)
2. .env file (if present)
3. Default values (lowest priority)
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable override support.

    All settings can be overridden via environment variables with the prefix
    ARCHIVIST_ (e.g., ARCHIVIST_DATA_DIR).

    Attributes:
        vectorizer_n_features: HashingVectorizer feature dimensions (2^20).
        vectorizer_norm: Vector normalization type (l2 recommended).
        ingest_batch_size: Batch size for ingestion operations.
        ingest_workers: Number of parallel ingestion workers.
        ingest_recursive: Recursively scan directories.
        ingest_chunk_large: Chunk large files by page/section.
        ingest_chunk_threshold_bytes: Byte threshold for chunking (default 10MB).
        ingest_chunk_threshold_pages: Page threshold for PDF chunking (default 100).
        api_host: FastAPI server bind address.
        api_port: FastAPI server port.
        api_workers: Number of Uvicorn workers.
        api_key: API authentication key (None = no auth).
        data_dir: Storage directory for SQLite database and tracker.
        config_dir: Configuration directory.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Vectorizer Configuration
    vectorizer_n_features: int = 1_048_576  # 2^20 dimensions
    vectorizer_norm: str = "l2"

    # Ingestion Configuration
    ingest_batch_size: int = 100
    ingest_workers: int = 4
    ingest_recursive: bool = True
    ingest_chunk_large: bool = True
    ingest_chunk_threshold_bytes: int = 10 * 1024 * 1024  # 10 MB
    ingest_chunk_threshold_pages: int = 100

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 4
    api_key: str | None = None  # None = no authentication
    api_max_upload_mb: int = 50  # Max upload size in MB
    api_max_archive_files: int = 5000  # Max files per archive

    # Storage Paths
    data_dir: Path = Path.home() / ".local" / "share" / "archivist"
    config_dir: Path = Path.home() / ".config" / "archivist"

    @property
    def tracker_db(self) -> Path:
        """Path to the SQLite tracker database."""
        return self.data_dir / "ingested_files.db"

    @property
    def sqlite_db(self) -> Path:
        """Path to the SQLite FTS5 search database."""
        return self.data_dir / "archivist.db"

    def ensure_dirs(self) -> None:
        """Create required directories if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    """Create and return configured Settings instance.

    Returns:
        Settings: Configured settings with directories ensured.
    """
    s = Settings()
    s.ensure_dirs()
    return s
