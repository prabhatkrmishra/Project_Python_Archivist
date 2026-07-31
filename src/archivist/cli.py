"""Command-line interface for Archivist.

Provides CLI commands for document ingestion, search, and management.
Uses SQLite FTS5 as the default and only search backend.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from archivist.config import get_settings
from archivist.ingestion.extractors import iter_files
from archivist.ingestion.tracker import Tracker
from archivist.utils.text import extract_snippet

app = typer.Typer(help="Archivist -- offline document search")
console = Console()
settings = get_settings()


def _get_backend():
    """Return the SQLite search backend.

    Returns:
        Dictionary with search, delete, stats, clear, close methods.
    """
    from archivist.search.sqlite_search import SQLiteSearch
    sq = SQLiteSearch(settings.sqlite_db)
    return {
        "client": sq,
        "search": lambda q, limit=10, all_chunks=False: sq.search(q, limit=limit, all_chunks=all_chunks),
        "delete": lambda doc_id: sq.delete(doc_id),
        "stats": lambda: sq.stats(),
        "clear": lambda: sq.delete_all(),
        "close": sq.close,
    }


@app.command()
def ingest(
    path: str,
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    workers: int = typer.Option(settings.ingest_workers, "--workers", "-w"),
    chunk: bool = typer.Option(True, "--chunk/--no-chunk"),
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Ingest a file or directory into the vector store."""
    root = Path(path).resolve()
    if not root.exists():
        if json_output:
            typer.echo(json.dumps({"error": f"Path not found: {root}"}, indent=2))
        else:
            console.print(f"[red]Path not found: {root}[/red]")
        raise typer.Exit(1)

    tracker = Tracker(settings.tracker_db)
    if not json_output:
        console.print(Panel(f"Ingesting: {root}", title="Archivist"))

    new_files: list[Path] = []
    skipped = 0

    for f in iter_files(root, recursive=recursive):
        try:
            if tracker.is_indexed(f):
                skipped += 1
                continue
        except PermissionError:
            skipped += 1
            continue
        new_files.append(f)

    total_new = len(new_files)
    if not json_output:
        console.print(f"Ingesting {total_new} files ({skipped} already indexed)...")

    ingested = 0
    total_vectors = 0
    errors = 0
    files_detail = []
    start = time.time()

    for i, filepath in enumerate(new_files, 1):
        try:
            n = _ingest_sqlite(filepath, tracker)
            total_vectors += n
            ingested += 1
            files_detail.append({
                "file": str(filepath),
                "name": filepath.name,
                "vectors": n,
                "status": "ok",
            })
            if not json_output:
                console.print(f" [{i}/{total_new}] {filepath.name} -> {n} vectors")
        except Exception as e:
            errors += 1
            files_detail.append({
                "file": str(filepath),
                "name": filepath.name,
                "vectors": 0,
                "status": "error",
                "error": str(e),
            })
            if not json_output:
                console.print(f" [{i}/{total_new}] {filepath.name} ERR {e}")

    elapsed = time.time() - start

    if json_output:
        output = {
            "path": str(root),
            "total_new": total_new,
            "skipped": skipped,
            "ingested": ingested,
            "total_vectors": total_vectors,
            "errors": errors,
            "elapsed_seconds": round(elapsed, 2),
            "files": files_detail,
        }
        typer.echo(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        elapsed_fmt = _fmt_duration(elapsed)
        console.print(
            f"\n[green]Ingestion complete: {ingested} files -> {total_vectors} vectors in {elapsed_fmt}[/green]\n"
            f"  ({skipped} already indexed, {errors} errors)"
        )

    tracker.close()


def _ingest_sqlite(filepath: Path, tracker: Tracker) -> int:
    """Ingest a file into SQLite FTS5 backend.

    Large files are split into multiple chunks; each chunk records the real
    starting line number so search snippets point at the right location.

    Args:
        filepath: Path to file to ingest.
        tracker: Tracker instance for idempotency.

    Returns:
        Number of chunks created (1 if content, 0 if empty/skipped).
    """
    from archivist.ingestion.extractors import (
        extract_text,
        normalize_for_display,
        should_chunk,
        chunk_text,
        cumulative_line_offsets,
    )

    if tracker.is_indexed(filepath):
        return 0

    raw = extract_text(filepath)
    display_text = normalize_for_display(raw)

    if not display_text.strip():
        file_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()
        tracker.record(filepath, file_hash)
        return 0

    from archivist.search.sqlite_search import SQLiteSearch
    sq = SQLiteSearch(settings.sqlite_db)

    file_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()

    # Delete old chunks if re-ingesting
    sq.delete_by_file_hash(file_hash)

    # Chunk the content
    if should_chunk(filepath, display_text):
        chunks = chunk_text(filepath, display_text)
    else:
        chunks = [display_text]

    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stat = filepath.stat()

    offsets = cumulative_line_offsets(chunks)
    for i, chunk_content in enumerate(chunks):
        doc_id = f"{file_hash}_{i:04d}"
        line_offset = offsets[i]
        sq.upsert({
            "doc_id": doc_id,
            "filepath": str(filepath),
            "filename": filepath.name,
            "content": chunk_content,
            "line_offset": line_offset,
            "file_size": stat.st_size,
            "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
            "ingested_at": timestamp,
            "file_hash": file_hash,
        })

    sq.close()
    tracker.record(filepath, file_hash)
    return len(chunks)


@app.command()
def search(
    query: str,
    top: int = typer.Option(10, "--top", "-n"),
    json_output: bool = typer.Option(False, "--json", "-j"),
    all_chunks: bool = typer.Option(False, "--all", "-a"),
):
    """Search ingested documents."""
    from archivist.search.sqlite_search import SQLiteSearch
    sq = SQLiteSearch(settings.sqlite_db)
    results = sq.search(query, limit=top, all_chunks=all_chunks)
    sq.close()

    if json_output:
        output = {
            "query": query,
            "total": len(results),
            "all_chunks": all_chunks,
            "results": [],
        }
        for i, hit in enumerate(results, 1):
            content = hit.get("content", "")
            line_offset = hit.get("line_offset", 0)
            snippet = extract_snippet(content, query, line_offset=line_offset, plain=True)
            filepath = hit.get("filepath", "unknown")
            source = Path(filepath).name if filepath != "unknown" else "unknown"
            score = hit.get("score", 0.0)
            output["results"].append({
                "rank": i,
                "score": score,
                "filepath": filepath,
                "source": source,
                "line_offset": line_offset,
                "snippet": snippet,
                "doc_id": hit.get("doc_id", ""),
                "file_hash": hit.get("file_hash", ""),
            })
        typer.echo(json.dumps(output, indent=2, ensure_ascii=False))
        raise typer.Exit(0)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        raise typer.Exit(0)

    for i, hit in enumerate(results, 1):
        content = hit.get("content", "")
        line_offset = hit.get("line_offset", 0)
        snippet = extract_snippet(content, query, line_offset=line_offset)
        filepath = hit.get("filepath", "unknown")
        source = Path(filepath).name if filepath != "unknown" else "unknown"
        score = hit.get("score", 0.0)

        console.print(f"\n[bold cyan][{i}][/bold cyan] [blue]{filepath}[/blue]")
        console.print(f"[dim]Source: {source}  |  Match: score={score}[/dim]")
        console.print(snippet)


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", "-j"),
):
    """Show index statistics."""
    from archivist.search.sqlite_search import SQLiteSearch
    sq = SQLiteSearch(settings.sqlite_db)
    stats = sq.stats()
    sq.close()

    tracker = Tracker(settings.tracker_db)
    tracker_stats = tracker.stats()
    tracker.close()

    if json_output:
        output = {**stats, "tracker_files": tracker_stats["indexed_files"]}
        typer.echo(json.dumps(output, indent=2, ensure_ascii=False))
        raise typer.Exit(0)

    console.print("[bold]Index status:[/bold]")
    for k, v in stats.items():
        console.print(f"  {k}: {v}")
    console.print(f"  tracker_files: {tracker_stats['indexed_files']}")


@app.command()
def delete(
    doc_id: str,
):
    """Delete a document by ID."""
    b = _get_backend()
    b["delete"](doc_id)
    b["close"]()
    console.print(f"[green]Deleted: {doc_id}[/green]")


@app.command()
def clear(
    confirm: bool = typer.Option(False, "--confirm", prompt="This will delete ALL indexed data. Continue?"),
):
    """Delete all vectors and reset the tracker. Use with caution."""
    if not confirm:
        raise typer.Abort()
    b = _get_backend()
    b["clear"]()
    b["close"]()
    import os
    # Remove the search database
    if os.path.exists(settings.sqlite_db):
        os.remove(settings.sqlite_db)
    # Remove the tracker database
    if os.path.exists(settings.tracker_db):
        os.remove(settings.tracker_db)
    console.print("[green]Database and tracker cleared.[/green]")


def _fmt_duration(seconds: float) -> str:
    """Format duration in human-readable format.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted string like '1h 23m 45s' or '2m 30s' or '45s'.
    """
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


if __name__ == "__main__":
    app()
