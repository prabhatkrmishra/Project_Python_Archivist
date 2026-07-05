"""Command-line interface for Archivist.

Provides CLI commands for document ingestion, search, and management.
Supports both SQLite FTS5 (default) and Qdrant backends via --backend flag
or persistent config via 'archivist use <backend>'.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from archivist.config import get_settings
from archivist.ingestion.extractors import iter_files, normalize_for_display
from archivist.ingestion.tracker import Tracker
from archivist.utils.text import extract_snippet

app = typer.Typer(help="Archivist -- offline document search")
console = Console()
settings = get_settings()

# Backend config file
_BACKEND_CONFIG = settings.config_dir / "backend"


def _load_backend() -> str:
    """Load saved backend from config file. Falls back to 'sqlite'."""
    try:
        if _BACKEND_CONFIG.exists():
            value = _BACKEND_CONFIG.read_text(encoding="utf-8").strip()
            if value in ("sqlite", "qdrant"):
                return value
    except Exception:
        pass
    return "sqlite"


def _save_backend(backend: str) -> None:
    """Persist backend choice to config file."""
    _BACKEND_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _BACKEND_CONFIG.write_text(backend, encoding="utf-8")


def _resolve_backend(override: str | None) -> str:
    """Resolve backend: explicit flag > saved config > default sqlite."""
    if override:
        if override not in ("sqlite", "qdrant"):
            console.print(f"[red]Invalid backend: {override}. Use 'sqlite' or 'qdrant'.[/red]")
            raise typer.Exit(1)
        return override
    return _load_backend()


BACKEND_OPT = typer.Option(None, "--backend", "-b", help="Override saved backend (sqlite or qdrant)")


def _get_backend(backend: str):
    """Return the appropriate search backend.

    Args:
        backend: Backend name ('sqlite' or 'qdrant').

    Returns:
        Dictionary with search, delete, stats, clear, close methods.
    """
    if backend == "qdrant":
        from qdrant_client import QdrantClient
        from archivist.search.qdrant_client import (
            search as qdrant_search,
            delete_points,
            get_stats,
        )
        client = QdrantClient(url=str(settings.qdrant_url), api_key=settings.qdrant_api_key)
        return {
            "client": client,
            "search": lambda q, limit=10: qdrant_search(
                client, settings.qdrant_collection,
                vectorize(q, use_bm25=settings.vectorizer_use_bm25),
                limit=limit,
            ),
            "delete": lambda doc_id: delete_points(client, settings.qdrant_collection, [doc_id]),
            "stats": lambda: get_stats(client, settings.qdrant_collection),
            "clear": lambda: client.delete_collection(settings.qdrant_collection),
            "close": client.close,
        }
    else:
        from archivist.search.sqlite_search import SQLiteSearch
        sq = SQLiteSearch(settings.sqlite_db)
        return {
            "client": sq,
            "search": lambda q, limit=10: sq.search(q, limit=limit),
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
    bm25: bool = typer.Option(False, "--bm25", help="Use Qdrant native BM25 instead of HashingVectorizer"),
    backend: str = BACKEND_OPT,
):
    """Ingest a file or directory into the vector store."""
    backend = _resolve_backend(backend)
    root = Path(path).resolve()
    if not root.exists():
        console.print(f"[red]Path not found: {root}[/red]")
        raise typer.Exit(1)

    tracker = Tracker(settings.tracker_db)
    console.print(Panel(f"Ingesting: {root}", title="Archivist"))

    new_files: list[Path] = []
    skipped = 0

    for f in iter_files(root, recursive=recursive):
        try:
            if tracker.is_indexed(f):
                console.print(f"Skip: {f.relative_to(root)}")
                skipped += 1
                continue
        except PermissionError:
            console.print(f"Skip (locked): {f.relative_to(root)}")
            skipped += 1
            continue
        new_files.append(f)
        console.print(f"Found: {f.relative_to(root)}")

    total_new = len(new_files)
    console.print(f"Ingesting {total_new} files ({skipped} already indexed)...")

    ingested = 0
    total_vectors = 0
    errors = 0
    start = time.time()

    for i, filepath in enumerate(new_files, 1):
        try:
            if backend == "sqlite":
                n = _ingest_sqlite(filepath, tracker)
            else:
                from archivist.ingestion.pipeline import ingest_file
                n = ingest_file(filepath, tracker, use_bm25=bm25, chunk=chunk)
            total_vectors += n
            console.print(f" [{i}/{total_new}] {filepath.name} -> {n} vectors")
            ingested += 1
        except Exception as e:
            errors += 1
            console.print(f" [{i}/{total_new}] {filepath.name} ERR {e}")

    elapsed = _fmt_duration(time.time() - start)
    console.print(
        f"\n[green]Ingestion complete: {ingested} files -> {total_vectors} vectors in {elapsed}[/green]\n"
        f"  ({skipped} already indexed, {errors} errors)"
    )
    tracker.close()


def _ingest_sqlite(filepath: Path, tracker: Tracker) -> int:
    """Ingest a file into SQLite FTS5 backend.

    Args:
        filepath: Path to file to ingest.
        tracker: Tracker instance for idempotency.

    Returns:
        Number of vectors created (1 if content, 0 if empty/skipped).
    """
    import hashlib
    import time
    from archivist.ingestion.extractors import extract_text

    if tracker.is_indexed(filepath):
        return 0

    raw = extract_text(filepath)
    display_text = normalize_for_display(raw)
    content = display_text[:50_000]  # FTS5 size limit

    if not content.strip():
        file_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()
        tracker.record(filepath, file_hash)
        return 0

    from archivist.search.sqlite_search import SQLiteSearch
    sq = SQLiteSearch(settings.sqlite_db)

    file_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()
    sq.upsert({
        "filepath": str(filepath),
        "filename": filepath.name,
        "content": content,
        "file_size": filepath.stat().st_size,
        "modified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(filepath.stat().st_mtime)),
        "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "file_hash": file_hash,
    })
    sq.close()
    tracker.record(filepath, file_hash)
    return 1


@app.command()
def search(
    query: str,
    top: int = typer.Option(10, "--top", "-n"),
    json_output: bool = typer.Option(False, "--json", "-j"),
    backend: str = BACKEND_OPT,
):
    """Search ingested documents."""
    backend = _resolve_backend(backend)
    from archivist.vectorizer.hashing_tfidf import vectorize

    if backend == "qdrant":
        from qdrant_client import QdrantClient
        from archivist.search.qdrant_client import search as qdrant_search
        client = QdrantClient(url=str(settings.qdrant_url), api_key=settings.qdrant_api_key)
        q_vec = vectorize(query, use_bm25=settings.vectorizer_use_bm25)
        hits = qdrant_search(client, settings.qdrant_collection, q_vec, limit=top)
        client.close()
        results = [{"id": h.id, "filepath": h.payload.get("filepath", "unknown"),
                     "filename": h.payload.get("filename", ""),
                     "content": h.payload.get("content", ""),
                     "score": round(h.score or 0.0, 4)} for h in hits]
    else:
        from archivist.search.sqlite_search import SQLiteSearch
        sq = SQLiteSearch(settings.sqlite_db)
        results = sq.search(query, limit=top)
        sq.close()

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        raise typer.Exit(0)

    for i, hit in enumerate(results, 1):
        content = hit.get("content", "")
        snippet = extract_snippet(content, query)
        filepath = hit.get("filepath", "unknown")
        source = Path(filepath).name if filepath != "unknown" else "unknown"
        score = hit.get("score", 0.0)

        if json_output:
            escaped = snippet.replace('"', '\\"').replace("\n", "\\n")
            typer.echo(
                f'{{"rank": {i}, "score": {score}, "filepath": "{filepath}", "source": "{source}", "snippet": "{escaped}"}}'
            )
        else:
            console.print(f"\n[bold cyan][{i}][/bold cyan] [blue]{filepath}[/blue]")
            console.print(f"[dim]Source: {source}  |  Match: score={score}[/dim]")
            console.print(snippet)


@app.command()
def status(
    backend: str = BACKEND_OPT,
):
    """Show index statistics."""
    backend = _resolve_backend(backend)
    if backend == "qdrant":
        from qdrant_client import QdrantClient
        from archivist.search.qdrant_client import get_stats
        client = QdrantClient(url=str(settings.qdrant_url), api_key=settings.qdrant_api_key)
        stats = get_stats(client, settings.qdrant_collection)
        client.close()
    else:
        from archivist.search.sqlite_search import SQLiteSearch
        sq = SQLiteSearch(settings.sqlite_db)
        stats = sq.stats()
        sq.close()

    tracker = Tracker(settings.tracker_db)
    tracker_stats = tracker.stats()
    tracker.close()

    console.print("[bold]Index status:[/bold]")
    for k, v in stats.items():
        console.print(f"  {k}: {v}")
    console.print(f"  tracker_files: {tracker_stats['indexed_files']}")


@app.command()
def delete(
    doc_id: str,
    backend: str = BACKEND_OPT,
):
    """Delete a document by ID."""
    backend = _resolve_backend(backend)
    b = _get_backend(backend)
    b["delete"](doc_id)
    b["close"]()
    console.print(f"[green]Deleted: {doc_id}[/green]")


@app.command()
def clear(
    confirm: bool = typer.Option(False, "--confirm", prompt="This will delete ALL indexed data. Continue?"),
    backend: str = BACKEND_OPT,
):
    """Delete all vectors and reset the tracker. Use with caution."""
    if not confirm:
        raise typer.Abort()
    backend = _resolve_backend(backend)
    b = _get_backend(backend)
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


@app.command()
def reindex(
    confirm: bool = typer.Option(False, "--confirm", prompt="This will rebuild all vectors. Continue?"),
):
    """Re-vectorize all documents (use after changing vectorizer settings).

    Args:
        confirm: Confirmation flag (required).
    """
    if not confirm:
        raise typer.Abort()
    console.print("[yellow]Reindex not yet implemented -- clear and re-ingest.[/yellow]")


@app.command()
def use(
    backend: str = typer.Argument(None, help="Backend to use: sqlite or qdrant"),
):
    """Select and persist the default search backend.

    Without arguments, shows current backend and interactive selector.
    With argument, sets the backend and saves to config.

    Examples:
        archivist use          # Show current + interactive picker
        archivist use sqlite   # Set SQLite FTS5 as default
        archivist use qdrant   # Set Qdrant as default
    """
    current = _load_backend()

    if backend is None:
        # Show current and let user pick
        table = Table(title="Search Backends")
        table.add_column("Backend", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Description")
        table.add_row("sqlite", "[bold]ACTIVE[/bold]" if current == "sqlite" else "",
                       "FTS5 keyword search, zero external services")
        table.add_row("qdrant", "[bold]ACTIVE[/bold]" if current == "qdrant" else "",
                       "Vector search, requires Qdrant server")
        console.print(table)
        console.print(f"\nCurrent: [bold]{current}[/bold]")
        console.print("Use: archivist use <sqlite|qdrant>")
        return

    backend = backend.lower().strip()
    if backend not in ("sqlite", "qdrant"):
        console.print(f"[red]Invalid backend: '{backend}'. Must be 'sqlite' or 'qdrant'.[/red]")
        raise typer.Exit(1)

    if backend == current:
        console.print(f"[yellow]Already using '{backend}'.[/yellow]")
        return

    _save_backend(backend)
    console.print(f"[green]Backend changed: {current} -> {backend}[/green]")
    console.print(f"Config saved to: {_BACKEND_CONFIG}")


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
