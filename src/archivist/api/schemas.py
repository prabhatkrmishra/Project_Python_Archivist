"""Pydantic schemas for Archivist API requests and responses."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Search ────────────────────────────────────────────────────────────────────


class SearchResult(BaseModel):
    """Single search result with full metadata."""

    rank: int = Field(description="Position in result set (1-indexed)")
    score: float = Field(description="BM25 relevance score (0-1)")
    filepath: str = Field(description="Absolute path to source file")
    source: str = Field(description="Filename only")
    filename: str = Field(description="Filename only")
    line_offset: int = Field(description="Starting line number of this chunk")
    snippet: str = Field(description="Line-numbered context around match")
    content_preview: str = Field(default="", description="First 500 chars of raw content")
    doc_id: str = Field(description="Unique document chunk ID")
    file_hash: str = Field(description="SHA-256 hash of source file")
    file_size: int = Field(description="File size in bytes")
    modified_at: str = Field(description="File last modified timestamp")
    ingested_at: str = Field(description="Ingestion timestamp")


class SearchResponse(BaseModel):
    """Paginated search response."""

    query: str
    total: int
    offset: int
    limit: int
    all_chunks: bool
    results: list[SearchResult]


# ── Ingestion ─────────────────────────────────────────────────────────────────


class IngestedFile(BaseModel):
    """Status of a single ingested file."""

    filename: str
    status: str = Field(description="ok, skipped, or error")
    chunks: int = Field(description="Number of chunks created")
    error: str | None = Field(default=None, description="Error message if status=error")


class IngestResponse(BaseModel):
    """Response from ingestion endpoints."""

    status: str
    total_files: int
    total_chunks: int
    elapsed_seconds: float
    files: list[IngestedFile]


class IngestDirectoryRequest(BaseModel):
    """Request body for directory ingestion."""

    path: str = Field(description="Local directory path to ingest")
    recursive: bool = Field(default=True, description="Scan subdirectories")


# ── Documents ─────────────────────────────────────────────────────────────────


class DocumentInfo(BaseModel):
    """Document metadata without full content."""

    doc_id: str
    filepath: str
    filename: str
    line_offset: int
    file_size: int
    modified_at: str
    ingested_at: str
    file_hash: str


class DocumentsResponse(BaseModel):
    """Paginated document list response."""

    total: int
    offset: int
    limit: int
    documents: list[DocumentInfo]


# ── Status ────────────────────────────────────────────────────────────────────


class StatusResponse(BaseModel):
    """Index statistics."""

    points_count: int
    backend: str
    tracker_files: int
    db_size_bytes: int
    unique_files: int = 0
    total_content_size_bytes: int = 0
    unique_extensions: int = 0
    last_ingested_at: str | None = None


# ── Errors ────────────────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str


class JobStatus(BaseModel):
    """Progress of an async ingestion job."""

    job_id: str
    status: str = Field(description="pending, running, done, error")
    total_files: int = 0
    processed_files: int = 0
    current_file: str = ""
    elapsed_seconds: float = 0.0
    error: str | None = None
    result: IngestResponse | None = None