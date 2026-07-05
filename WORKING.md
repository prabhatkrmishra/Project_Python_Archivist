# WORKING.md — Detailed Function Flow

This document traces exactly which functions get invoked when each CLI command is run.

---

## `archivist use [backend]`

```
Typer CLI entry point
├── cli.py:app()
│   └── cli.py:use()                       # @app.command()
│       ├── cli.py:_load_backend()          # Read ~/.config/archivist/backend
│       │   └── Path.home() / ".config" / "archivist" / "backend"
│       │       ├── [if exists] → read_text().strip()
│       │       └── [else] → "sqlite"       # Default fallback
│       │
│       ├── [if no argument]
│       │   ├── Rich Table display          # Show backends + current status
│       │   └── Print "Use: archivist use <sqlite|qdrant>"
│       │
│       ├── [if argument provided]
│       │   ├── Validate backend ∈ {"sqlite", "qdrant"}
│       │   │   └── [invalid] → console.print(error) → raise typer.Exit(1)
│       │   ├── [if same as current] → "Already using '{backend}'."
│       │   └── cli.py:_save_backend(backend)
│       │       └── Path.home() / ".config" / "archivist" / "backend"
│       │           └── write_text(backend)
│       │
│       └── console.print("Backend changed: {old} -> {new}")
```

---

## `archivist ingest <path>`

```
Typer CLI entry point
├── cli.py:app()                          # Typer app dispatch
│   └── cli.py:ingest()                   # @app.command()
│       ├── config.py:get_settings()      # Load Settings from env/.env
│       ├── Path(path).resolve()          # Resolve absolute path
│       ├── ingestion/tracker.py:Tracker(settings.tracker_db)
│       │   ├── sqlite3.connect()         # Open SQLite tracker DB
│       │   └── CREATE TABLE files...     # Create tracker table if not exists
│       ├── Rich Panel display            # Show "Ingesting: ..." header
│       │
│       │ # FILE DISCOVERY PHASE
│       ├── ingestion/extractors.py:iter_files(root, recursive)
│       │   ├── root.is_file()            # Single file? yield directly
│       │   └── root.glob("**/*")         # Directory? glob for supported extensions
│       │       └── yield Path if .suffix in SUPPORTED_EXTENSIONS
│       │
│       │ # SKIP CHECK PHASE
│       ├── ingestion/tracker.py:Tracker.is_indexed(f)
│       │   ├── ingestion/extractors.py:sha256_file(path)
│       │   │   └── hashlib.sha256()      # Compute SHA-256 of file contents
│       │   └── SELECT 1 FROM files WHERE file_hash = ?
│       │
│       │ # INGESTION PHASE (per file)
│       ├── [if backend == "sqlite"]
│       │   └── cli.py:_ingest_sqlite(filepath, tracker)
│       │       ├── ingestion/tracker.py:Tracker.is_indexed(filepath)  # Double-check
│       │       ├── ingestion/extractors.py:extract_text(filepath)
│       │       │   ├── [if .pdf] extract_pdf(path)  → pypdf.PdfReader → page.extract_text()
│       │       │   ├── [if .docx] extract_docx(path) → docx.Document → para.text
│       │       │   └── [else] extract_txt(path)      # All text/code files
│       │       ├── ingestion/extractors.py:normalize_for_display(raw)
│       │       │   ├── re.sub( control chars → " " )
│       │       │   ├── re.sub( [^\S\n]+ → " " )    # Collapse spaces, keep newlines
│       │       │   └── re.sub( \n{3,} → "\n\n" )    # Max 2 consecutive newlines
│       │       ├── search/sqlite_search.py:SQLiteSearch(settings.sqlite_db)
│       │       │   └── sqlite3.connect() + CREATE TABLE + CREATE VIRTUAL TABLE FTS5
│       │       ├── sqlite_search.py:SQLiteSearch.upsert(payload)
│       │       │   ├── INSERT OR REPLACE INTO documents (id, filepath, content, ...)
│       │       │   └── INSERT OR REPLACE INTO documents_fts(id, filepath, content)
│       │       ├── sqlite_search.py:SQLiteSearch.close()
│       │       └── ingestion/tracker.py:Tracker.record(filepath, file_hash)
│       │           └── INSERT OR REPLACE INTO files (file_hash, filepath, qdrant_point_id, ...)
│       │
│       │   [else if backend == "qdrant"]
│       │   └── ingestion/pipeline.py:ingest_file(filepath, tracker, use_bm25, chunk)
│       │       ├── ingestion/tracker.py:Tracker.is_indexed(path)
│       │       ├── ingestion/extractors.py:extract_text(path)
│       │       ├── ingestion/extractors.py:normalize_text(raw)       # Lowercase + collapse
│       │       ├── ingestion/extractors.py:normalize_for_display(raw) # Keep case + newlines
│       │       ├── ingestion/extractors.py:should_chunk(path, text)  # >10MB or >100 pages?
│       │       ├── ingestion/extractors.py:chunk_text(path, display_text)
│       │       │   ├── chunk_pdf_by_page(path)    # Split PDF by pages
│       │       │   └── chunk_docx_by_section(path) # Split DOCX by headings
│       │       ├── search/qdrant_client.py:ensure_collection(client, collection)
│       │       │   ├── client.get_collections()
│       │       │   ├── client.create_collection() + sparse_vectors_config
│       │       │   └── client.create_payload_index() × 3 (file_hash, filepath, content)
│       │       ├── vectorizer/hashing_tfidf.py:vectorize(chunk_text, use_bm25)
│       │       │   ├── [if use_bm25] bm25_vectorize(text) → models.Document
│       │       │   └── [else] hashing_vectorize([text])
│       │       │       ├── HashingVectorizer(n_features=2^20)  # Cached at module level
│       │       │       └── csr_to_qdrant_sparse(matrix[0])     # scipy CSR → SparseVector
│       │       ├── search/qdrant_client.py:build_point(vec, payload)
│       │       │   └── models.PointStruct(id=uuid, vector={"text": vec}, payload=...)
│       │       ├── search/qdrant_client.py:upsert_points(client, collection, points)
│       │       │   └── client.upsert(collection, points, wait=True)
│       │       └── ingestion/tracker.py:Tracker.record(path, point_id)
│       │
│       │ # SUMMARY
│       ├── Rich print: "Ingestion complete: X files -> Y vectors in Zs"
│       └── tracker.close()
```

---

## `archivist search "<query>"`

```
Typer CLI entry point
├── cli.py:app()
│   └── cli.py:search()                   # @app.command()
│       ├── [if backend == "sqlite"]
│       │   ├── search/sqlite_search.py:SQLiteSearch(settings.sqlite_db)
│       │   │   └── sqlite3.connect()
│       │   ├── sqlite_search.py:SQLiteSearch.search(query, limit)
│       │   │   ├── _escape_fts(query)    # Escape FTS5 special chars, build OR query
│       │   │   └── SELECT d.*, bm25(rank)
│       │   │       FROM documents_fts f
│       │   │       JOIN documents d ON d.id = f.id
│       │   │       WHERE documents_fts MATCH ?
│       │   │       ORDER BY rank LIMIT ?
│       │   └── sqlite_search.py:SQLiteSearch.close()
│       │
│       │   [else if backend == "qdrant"]
│       │   ├── qdrant_client.py:QdrantClient(url, api_key)
│       │   ├── vectorizer/hashing_tfidf.py:vectorize(query, use_bm25)
│       │   ├── search/qdrant_client.py:search(client, collection, query_vector, limit)
│       │   │   └── client.query_points(collection, query, using="text", limit, ef=100)
│       │   └── QdrantClient.close()
│       │
│       │ # DISPLAY PHASE
│       ├── [if no results] → "No results found." → Exit
│       │
│       └── for each hit:
│           ├── utils/text.py:extract_snippet(content, query, context_lines=3)
│           │   ├── re.compile(terms, IGNORECASE)   # Build regex from query
│           │   ├── pattern.search(content)          # Find first match
│           │   ├── Split content by newlines → lines
│           │   ├── Find match_line_idx
│           │   ├── Build context: start = match - 3, end = match + 4
│           │   └── Format: "► L42: matched line" + "  L41: context" etc.
│           │
│           ├── [if --json] → typer.echo(JSON)
│           └── [else] → Rich console.print with color formatting
```

---

## `archivist status`

```
Typer CLI entry point
├── cli.py:app()
│   └── cli.py:status()                   # @app.command()
│       ├── [if backend == "sqlite"]
│       │   ├── search/sqlite_search.py:SQLiteSearch(settings.sqlite_db)
│       │   ├── sqlite_search.py:SQLiteSearch.stats()
│       │   │   └── SELECT COUNT(*) FROM documents → {"points_count": N, "backend": "sqlite-fts5"}
│       │   └── SQLiteSearch.close()
│       │
│       │   [else if backend == "qdrant"]
│       │   ├── qdrant_client.py:QdrantClient(url, api_key)
│       │   ├── search/qdrant_client.py:get_stats(client, collection)
│       │   │   └── client.get_collection(collection) → {"points_count", "status", "vectors_count"}
│       │   └── QdrantClient.close()
│       │
│       ├── ingestion/tracker.py:Tracker(settings.tracker_db)
│       ├── tracker.py:Tracker.stats()
│       │   └── SELECT COUNT(*) FROM files → {"indexed_files": N}
│       ├── tracker.close()
│       └── console.print("Index status:", stats, tracker_files)
```

---

## `archivist delete <doc_id>`

```
Typer CLI entry point
├── cli.py:app()
│   └── cli.py:delete()                   # @app.command()
│       ├── cli.py:_get_backend(backend)
│       │   ├── [sqlite] → SQLiteSearch with delete, close methods
│       │   └── [qdrant] → QdrantClient with delete_points, close methods
│       │
│       ├── b["delete"](doc_id)
│       │   ├── [sqlite] sqlite_search.py:SQLiteSearch.delete(doc_id)
│       │   │   ├── DELETE FROM documents_fts WHERE id = ?
│       │   │   └── DELETE FROM documents WHERE id = ?
│       │   └── [qdrant] qdrant_client.py:delete_points(client, collection, [doc_id])
│       │       └── client.delete(collection, PointIdsList(points=[doc_id]))
│       │
│       └── b["close"]()
│           ├── [sqlite] sqlite_search.py:SQLiteSearch.close() → conn.close()
│           └── [qdrant] QdrantClient.close()
```

---

## `archivist clear --confirm`

```
Typer CLI entry point
├── cli.py:app()
│   └── cli.py:clear()                    # @app.command()
│       ├── [if not confirm] → raise typer.Abort()
│       │
│       ├── cli.py:_get_backend(backend)
│       │
│       ├── b["clear"]()
│       │   ├── [sqlite] sqlite_search.py:SQLiteSearch.delete_all()
│       │   │   ├── DELETE FROM documents_fts
│       │   │   └── DELETE FROM documents
│       │   └── [qdrant] QdrantClient.delete_collection(collection)
│       │
│       ├── b["close"]()
│       │
│       ├── os.path.exists(settings.tracker_db)
│       │   └── os.remove(settings.tracker_db)  # Delete SQLite tracker file
│       │
│       └── console.print("Database and tracker cleared.")
```

---

## `archivist reindex --confirm`

```
Typer CLI entry point
├── cli.py:app()
│   └── cli.py:reindex()                  # @app.command()
│       ├── [if not confirm] → raise typer.Abort()
│       └── console.print("Reindex not yet implemented...")
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `cli.py` | Typer CLI entry point, command routing, backend resolution |
| `config.py` | Pydantic settings (env vars, defaults) |
| `ingestion/extractors.py` | Text/code/PDF/DOCX extraction, normalization, chunking |
| `ingestion/tracker.py` | SQLite SHA256 hash tracker for idempotency |
| `ingestion/pipeline.py` | Qdrant ingestion orchestration |
| `search/sqlite_search.py` | SQLite FTS5 backend (default) |
| `search/qdrant_client.py` | Qdrant vector backend (optional) |
| `vectorizer/hashing_tfidf.py` | HashingVectorizer + BM25 vectorization |
| `utils/text.py` | Line-numbered snippet extraction |
| `api/routes.py` | FastAPI REST endpoints |
| `main.py` | FastAPI application factory |
| `~/.config/archivist/backend` | Persisted backend choice (sqlite or qdrant) |

---

## Data Flow Diagram

```
File → extract_text() → normalize_for_display() → SQLiteSearch.upsert()
                                                        ↓
                                              documents table (metadata)
                                              documents_fts table (FTS5 index)
                                                        ↓
                                              tracker.record(file_hash)
                                                        ↓
                                              files table (SHA256 hash)

Query → SQLiteSearch.search(query) → bm25 ranking → extract_snippet() → Display
```
