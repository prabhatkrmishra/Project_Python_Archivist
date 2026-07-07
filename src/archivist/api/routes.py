"""API route handlers for Archivist.

FastAPI endpoints for document ingestion, search, and management.
Supports single file, multi-file, archive (zip/rar/7z), and directory ingestion.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from archivist.config import get_settings
from archivist.ingestion.extractors import iter_files, normalize_for_display
from archivist.ingestion.tracker import Tracker
from archivist.search.sqlite_search import SQLiteSearch
from archivist.utils.text import extract_snippet

from .archives import ArchiveError, analyze_archive, extract_archive, is_archive
from .schemas import (
    DocumentInfo,
    DocumentsResponse,
    ErrorResponse,
    IngestedFile,
    IngestResponse,
    JobStatus,
    SearchResult,
    SearchResponse,
    StatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["archivist"])
settings = get_settings()


def _get_max_bytes() -> int:
    """Return max upload size in bytes."""
    return settings.api_max_upload_mb * 1024 * 1024


# ── Async job store ─────────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _update_job(job_id: str, **kwargs) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)


def _run_ingest_job(
    job_id: str,
    filepaths: list[Path],
    root_dir: Path,
) -> None:
    """Background worker that ingests files and updates progress."""
    tracker = Tracker(settings.tracker_db)
    start = time.time()
    total = len(filepaths)
    results: list[IngestedFile] = []
    tmp_dir = _jobs[job_id].get("_tmp_dir")

    _update_job(job_id, status="running", total_files=total, processed_files=0)

    for i, filepath in enumerate(filepaths):
        rel = str(filepath.relative_to(root_dir)) if filepath.is_relative_to(root_dir) else filepath.name
        _update_job(
            job_id, 
            processed_files=i, 
            current_file=rel, 
            elapsed_seconds=round(time.time() - start, 3)
        )

        try:
            result = _ingest_single_file(filepath, tracker, root_dir=root_dir)
        except Exception as exc:
            result = IngestedFile(filename=filepath.name, status="error", vectors=0, error=str(exc))
        results.append(result)

    elapsed = time.time() - start
    tracker.close()

    total_vectors = sum(r.vectors for r in results)
    response = IngestResponse(
        status="ok",
        total_files=len(results),
        total_vectors=total_vectors,
        elapsed_seconds=round(elapsed, 3),
        files=results,
    )

    _update_job(
        job_id,
        status="done",
        processed_files=total,
        current_file="",
        elapsed_seconds=elapsed,
        result=response,
        _finished_at=time.time(),
    )


def _ingest_single_file(
    filepath: Path, tracker: Tracker, root_dir: Path | None = None
) -> IngestedFile:
    """Ingest a single file through the proper pipeline.

    Args:
        filepath: Path to file to ingest.
        tracker: Tracker instance for idempotency.
        root_dir: If given, the stored filepath is relative to this
            directory instead of the absolute on-disk path. Used to strip
            throwaway temp-upload/extraction directories (e.g.
            `archivist_archive_xxxxx`) from what gets shown/stored, so
            only the meaningful path inside the upload/archive remains.

    Returns:
        IngestedFile with status details.
    """
    if root_dir is not None and filepath.is_relative_to(root_dir):
        display_filepath = str(filepath.relative_to(root_dir))
    else:
        display_filepath = str(filepath)

    try:
        if tracker.is_indexed(filepath):
            return IngestedFile(
                filename=filepath.name, status="skipped", vectors=0
            )

        raw = filepath.read_bytes()
        file_hash = hashlib.sha256(raw).hexdigest()

        from archivist.ingestion.extractors import (
            chunk_text,
            extract_text,
            should_chunk,
        )

        text = extract_text(filepath)
        display_text = normalize_for_display(text)

        if not display_text.strip():
            tracker.record(filepath, file_hash)
            return IngestedFile(
                filename=filepath.name, status="skipped", vectors=0
            )

        sq = SQLiteSearch(settings.sqlite_db)
        sq.delete_by_file_hash(file_hash)

        if should_chunk(filepath, display_text):
            chunks = chunk_text(filepath, display_text)
        else:
            chunks = [display_text]

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stat = filepath.stat()

        for i, chunk_content in enumerate(chunks):
            doc_id = f"{file_hash}_{i:04d}"
            line_offset = i * 1500
            sq.upsert({
                "doc_id": doc_id,
                "filepath": display_filepath,
                "filename": filepath.name,
                "content": chunk_content,
                "line_offset": line_offset,
                "file_size": stat.st_size,
                "modified_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)
                ),
                "ingested_at": timestamp,
                "file_hash": file_hash,
            })

        sq.close()
        tracker.record(filepath, file_hash)
        return IngestedFile(
            filename=filepath.name, status="ok", vectors=len(chunks)
        )

    except Exception as e:
        logger.error(f"Ingest failed for {filepath}: {e}")
        return IngestedFile(
            filename=filepath.name, status="error", vectors=0, error=str(e)
        )


# ── Search ────────────────────────────────────────────────────────────────────


@router.get(
    "/search",
    response_model=SearchResponse,
    responses={400: {"model": ErrorResponse}},
)
async def search(
    q: str = Query(..., description="Search query"),
    size: int = Query(10, ge=1, le=100, description="Number of results"),
    offset: int = Query(0, ge=0, description="Result offset for pagination"),
    all_chunks: bool = Query(False, description="Return all matching chunks"),
    file_ext: str | None = Query(None, description="Filter by file extension (e.g., .py)"),
    min_score: float = Query(0.0, ge=0.0, le=1.0, description="Minimum relevance score"),
    content_preview: bool = Query(False, description="Include first 500 chars of raw content"),
):
    """Search ingested documents with pagination and filters."""
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        results = sq.search(q, limit=max(offset + size, 500), all_chunks=all_chunks)
    finally:
        sq.close()

    # Apply filters
    filtered = []
    for r in results:
        if file_ext:
            ext = Path(r.get("filepath", "")).suffix.lower()
            if ext != file_ext.lower():
                continue
        score = r.get("score", 0.0)
        if score < min_score:
            continue
        filtered.append(r)

    total = len(filtered)
    page = filtered[offset : offset + size]

    search_results = []
    for i, r in enumerate(page, offset + 1):
        content = r.get("content", "")
        line_offset = r.get("line_offset", 0)
        snippet = extract_snippet(content, q, line_offset=line_offset, plain=True)
        filepath = r.get("filepath", "unknown")
        source = Path(filepath).name if filepath != "unknown" else "unknown"

        search_results.append(SearchResult(
            rank=i,
            score=r.get("score", 0.0),
            filepath=filepath,
            source=source,
            filename=source,
            line_offset=line_offset,
            snippet=snippet,
            content_preview=content[:500] if content_preview else "",
            doc_id=r.get("doc_id", ""),
            file_hash=r.get("file_hash", ""),
            file_size=r.get("file_size", 0),
            modified_at=r.get("modified_at", ""),
            ingested_at=r.get("ingested_at", ""),
        ))

    return SearchResponse(
        query=q,
        total=total,
        offset=offset,
        limit=size,
        all_chunks=all_chunks,
        results=search_results,
    )


# ── Status ────────────────────────────────────────────────────────────────────


@router.get("/status", response_model=StatusResponse)
async def status():
    """Get index statistics."""
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        stats = sq.stats()
    finally:
        sq.close()

    tracker = Tracker(settings.tracker_db)
    tracker_stats = tracker.stats()
    tracker.close()

    db_size = 0
    if settings.sqlite_db.exists():
        db_size = settings.sqlite_db.stat().st_size

    return StatusResponse(
        points_count=stats.get("points_count", 0),
        backend=stats.get("backend", "unknown"),
        tracker_files=tracker_stats.get("indexed_files", 0),
        db_size_bytes=db_size,
    )


# ── Ingest: Single File ──────────────────────────────────────────────────────


@router.post(
    "/ingest/file",
    response_model=IngestResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def ingest_file(file: UploadFile = File(...)):
    """Ingest a single file through the extraction pipeline."""
    content = await file.read()
    if len(content) > _get_max_bytes():
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size: {settings.api_max_upload_mb}MB",
        )

    # Save to temp file so we can use the proper pipeline
    tmp_dir = Path(tempfile.mkdtemp(prefix="archivist_upload_"))
    try:
        filename = file.filename or "unknown.txt"
        filepath = tmp_dir / filename
        filepath.write_bytes(content)

        tracker = Tracker(settings.tracker_db)
        start = time.time()
        result = _ingest_single_file(filepath, tracker, root_dir=tmp_dir)
        elapsed = time.time() - start
        tracker.close()

        return IngestResponse(
            status="ok",
            total_files=1,
            total_vectors=result.vectors,
            elapsed_seconds=round(elapsed, 3),
            files=[result],
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Ingest: Multi-File ───────────────────────────────────────────────────────


@router.post("/ingest/files")
async def ingest_files(files: list[UploadFile] = File(...)):
    """Start async ingestion of multiple files. Returns a job_id to poll for progress."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    tmp_dir = Path(tempfile.mkdtemp(prefix="archivist_upload_"))
    filepaths: list[Path] = []

    for f in files:
        content = await f.read()
        filename = f.filename or "unknown.txt"
        filepath = tmp_dir / filename
        filepath.write_bytes(content)
        filepaths.append(filepath)

    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "total_files": len(filepaths),
        "processed_files": 0,
        "current_file": "",
        "elapsed_seconds": 0.0,
        "error": None,
        "result": None,
        "_tmp_dir": tmp_dir,
    }

    thread = threading.Thread(
        target=_run_ingest_job, args=(job_id, filepaths, tmp_dir), daemon=True
    )
    thread.start()

    return {"job_id": job_id, "total_files": len(filepaths)}


# ── Ingest: Archive ──────────────────────────────────────────────────────────


@router.post("/archive/analyze")
async def analyze_archive_upload(file: UploadFile = File(...)):
    """Validate an archive and summarize its contents without extracting it.

    Used by the "Analyzing archive..." step in the UI right after a file is
    selected, so the user gets real, immediate feedback (valid/invalid,
    file count, size) before committing to the full extraction + ingest.
    """
    content = await file.read()
    if len(content) > _get_max_bytes():
        return {
            "valid": False,
            "format": Path(file.filename or "").suffix.lower(),
            "file_count": 0,
            "total_size": 0,
            "error": f"Archive too large. Max size: {settings.api_max_upload_mb}MB",
        }

    filename = file.filename or "archive.zip"
    return analyze_archive(content, filename)


@router.post("/ingest/archive")
async def ingest_archive(file: UploadFile = File(...)):
    """Start async archive ingestion. Returns a job_id to poll for progress."""
    content = await file.read()
    if len(content) > _get_max_bytes():
        raise HTTPException(
            status_code=413,
            detail=f"Archive too large. Max size: {settings.api_max_upload_mb}MB",
        )

    filename = file.filename or "archive.zip"
    tmp_dir = Path(tempfile.mkdtemp(prefix="archivist_archive_"))

    try:
        try:
            extracted_files = extract_archive(content, filename, dest=tmp_dir)
        except ArchiveError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if not extracted_files:
            return {"job_id": None, "total_files": 0}

        job_id = uuid.uuid4().hex
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "total_files": len(extracted_files),
            "processed_files": 0,
            "current_file": "",
            "elapsed_seconds": 0.0,
            "error": None,
            "result": None,
            "_tmp_dir": tmp_dir,
        }

        thread = threading.Thread(
            target=_run_ingest_job,
            args=(job_id, extracted_files, tmp_dir),
            daemon=True,
        )
        thread.start()

        return {"job_id": job_id, "total_files": len(extracted_files)}
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


# ── Ingest: Directory ────────────────────────────────────────────────────────


@router.post(
    "/ingest/directory",
    response_model=IngestResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def ingest_directory(body: dict):
    """Ingest all files from a local directory path."""
    path_str = body.get("path", "")
    recursive = body.get("recursive", True)

    if not path_str:
        raise HTTPException(status_code=400, detail="path is required")

    root = Path(path_str).resolve()
    if not root.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {root}")
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {root}")

    tracker = Tracker(settings.tracker_db)
    start = time.time()
    results: list[IngestedFile] = []

    for filepath in iter_files(root, recursive=recursive):
        result = _ingest_single_file(filepath, tracker)
        results.append(result)

    elapsed = time.time() - start
    tracker.close()

    total_vectors = sum(r.vectors for r in results)
    return IngestResponse(
        status="ok",
        total_files=len(results),
        total_vectors=total_vectors,
        elapsed_seconds=round(elapsed, 3),
        files=results,
    )


@router.post("/ingest/directory/upload")
async def ingest_directory_upload(
    files: list[UploadFile] = File(...),
    paths: str = Form(...),
):
    """Start async folder ingestion. Returns a job_id to poll for progress."""
    relative_paths = paths.split("\n")

    if len(files) != len(relative_paths):
        raise HTTPException(
            status_code=400,
            detail=f"Mismatch: {len(files)} files but {len(relative_paths)} paths",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="archivist_dirupload_"))
    filepaths: list[Path] = []

    # Save files preserving directory structure
    for f, rel_path in zip(files, relative_paths):
        rel_path = rel_path.strip()
        if not rel_path:
            continue
        dest = tmp_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = await f.read()
        dest.write_bytes(content)
        filepaths.append(dest)

    # Use tmp_dir as the root so the selected folder's own name (the first
    # path segment of each relative path, e.g. "zygisk") is preserved in
    # the stored/displayed filepath instead of being stripped along with
    # the throwaway temp dir prefix.
    root_dir = tmp_dir

    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "total_files": len(filepaths),
        "processed_files": 0,
        "current_file": "",
        "elapsed_seconds": 0.0,
        "error": None,
        "result": None,
        "_tmp_dir": tmp_dir,
    }

    thread = threading.Thread(
        target=_run_ingest_job, args=(job_id, filepaths, root_dir), daemon=True
    )
    thread.start()

    return {"job_id": job_id, "total_files": len(filepaths)}


# ── Documents ─────────────────────────────────────────────────────────────────


@router.get(
    "/documents",
    response_model=DocumentsResponse,
    responses={400: {"model": ErrorResponse}},
)
async def list_documents(
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    file_hash: str | None = Query(None, description="Filter by file hash"),
    file_ext: str | None = Query(None, description="Filter by file extension"),
):
    """List all ingested documents with pagination."""
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        rows = sq.conn.execute(
            """SELECT doc_id, filepath, filename, line_offset,
                      file_size, modified_at, ingested_at, file_hash
               FROM documents
               ORDER BY ingested_at DESC"""
        ).fetchall()
    finally:
        sq.close()

    # Apply filters
    filtered = []
    for row in rows:
        if file_hash and row[7] != file_hash:
            continue
        if file_ext:
            ext = Path(row[1]).suffix.lower()
            if ext != file_ext.lower():
                continue
        filtered.append(row)

    total = len(filtered)
    page = filtered[offset : offset + limit]

    documents = [
        DocumentInfo(
            doc_id=r[0],
            filepath=r[1],
            filename=r[2],
            line_offset=r[3],
            file_size=r[4],
            modified_at=r[5],
            ingested_at=r[6],
            file_hash=r[7],
        )
    for r in page
    ]

    return DocumentsResponse(
        total=total, offset=offset, limit=limit, documents=documents
    )


@router.get("/documents/extensions", response_model=list[str])
async def list_extensions():
    """Return distinct file extensions from the documents index."""
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        rows = sq.conn.execute(
            "SELECT DISTINCT filepath FROM documents ORDER BY filepath"
        ).fetchall()
    finally:
        sq.close()
    exts = set()
    for r in rows:
        ext = Path(r[0]).suffix.lower()
        if ext:
            exts.add(ext)
    return sorted(exts)


@router.get(
    "/documents/{doc_id}",
    response_model=DocumentInfo,
    responses={404: {"model": ErrorResponse}},
)
async def get_document(doc_id: str):
    """Get a single document by ID."""
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        row = sq.conn.execute(
            """SELECT doc_id, filepath, filename, line_offset,
                      file_size, modified_at, ingested_at, file_hash
               FROM documents WHERE doc_id = ?""",
            (doc_id,),
        ).fetchone()
    finally:
        sq.close()

    if not row:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

    return DocumentInfo(
        doc_id=row[0],
        filepath=row[1],
        filename=row[2],
        line_offset=row[3],
        file_size=row[4],
        modified_at=row[5],
        ingested_at=row[6],
        file_hash=row[7],
    )


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete(
    "/documents/all",
    responses={200: {"description": "All documents deleted"}},
)
async def delete_all_documents():
    """Delete all documents and clear the index completely.

    Exactly mirrors `archivist clear --confirm`: closes both database
    connections, then removes the search db and tracker db files from
    disk entirely (plus their -wal/-shm sidecar files, since both run in
    WAL mode), rather than deleting rows in place. Both files are
    recreated fresh, empty, the next time anything connects to them.

    NOTE: this route must stay registered before /documents/{doc_id}
    below - FastAPI matches routes in registration order, and the
    parameterized route would otherwise swallow "all" as a doc_id.
    """
    sq = SQLiteSearch(settings.sqlite_db)
    sq.delete_all()
    sq.close()

    tracker = Tracker(settings.tracker_db)
    tracker.close()

    for db_path in (settings.sqlite_db, settings.tracker_db):
        for suffix in ("", "-wal", "-shm"):
            path = db_path.with_name(db_path.name + suffix)
            if path.exists():
                os.remove(path)

    return {"status": "deleted", "message": "All documents cleared"}


@router.delete(
    "/documents",
    responses={200: {"description": "Deleted by file hash"}},
)
async def delete_documents_by_hash(
    file_hash: str = Query(..., description="File hash to delete all chunks for"),
):
    """Delete all document chunks for a given file hash."""
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        sq.delete_by_file_hash(file_hash)
    finally:
        sq.close()
    return {"status": "deleted", "file_hash": file_hash}


@router.delete(
    "/documents/{doc_id}",
    responses={200: {"description": "Deleted"}},
)
async def delete_document(doc_id: str):
    """Delete a document by its ID."""
    sq = SQLiteSearch(settings.sqlite_db)
    try:
        sq.delete(doc_id)
    finally:
        sq.close()
    return {"status": "deleted", "id": doc_id}


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    """Poll for async job progress."""
    with _jobs_lock:
        job = _jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Build response (exclude internal keys)
    response = JobStatus(
        job_id=job["job_id"],
        status=job["status"],
        total_files=job["total_files"],
        processed_files=job["processed_files"],
        current_file=job["current_file"],
        elapsed_seconds=job["elapsed_seconds"],
        error=job["error"],
        result=job["result"],
    )

    # Clean up done jobs after 30 seconds
    if job["status"] in ("done", "error"):
        elapsed = time.time() - job.get("_finished_at", time.time())
        if elapsed > 30:
            with _jobs_lock:
                _jobs.pop(job_id, None)
            tmp = job.get("_tmp_dir")
            if tmp and tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)

    return response