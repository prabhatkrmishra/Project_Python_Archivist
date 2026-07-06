# WORKING.md — Detailed Function Flow

This document traces exactly which functions get invoked when each CLI command is run.

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
│           │ # INGESTION PHASE (per file)
│       └── cli.py:_ingest_sqlite(filepath, tracker)
│           ├── ingestion/tracker.py:Tracker.is_indexed(filepath)  # Double-check
│           ├── ingestion/extractors.py:extract_text(filepath)
│           │   ├── [if .pdf] extract_pdf(path)  → pypdf.PdfReader → page.extract_text()
│           │   ├── [if .docx] extract_docx(path) → docx.Document → para.text
│           │   ├── [if .csv/.tsv] extract_csv(path) → csv.reader → flatten rows as "header: value | header: value"
│           │   ├── [if .xls/.xlsx] extract_excel(path) → openpyxl/xlrd → flatten rows per sheet
│           │   ├── [if .jsonl] extract_jsonl(path) → json.loads per line → flatten with dot notation
│           │   └── [else] extract_txt(path)      # All text/code files
│           ├── ingestion/extractors.py:normalize_for_display(raw)
│           │   ├── re.sub( control chars → " " )
│           │   ├── re.sub( [^\S\n]+ → " " )    # Collapse spaces, keep newlines
│           │   └── re.sub( \n{3,} → "\n\n" )    # Max 2 consecutive newlines
│           ├── search/sqlite_search.py:SQLiteSearch(settings.sqlite_db)
│           │   └── sqlite3.connect() + CREATE TABLE + CREATE VIRTUAL TABLE FTS5 (external-content)
│           │   └── _ensure_triggers() → CREATE TRIGGER documents_ai/ad/au (auto-sync FTS5)
│           ├── sqlite_search.py:SQLiteSearch.delete_by_file_hash(file_hash)
│           │   └── DELETE FROM documents WHERE file_hash = ? → triggers clean FTS5 via 'delete' command
│           ├── sqlite_search.py:SQLiteSearch.upsert(payload)
│           │   └── INSERT OR REPLACE INTO documents (doc_id, filepath, content, ...) → trigger auto-inserts to FTS5
│           ├── sqlite_search.py:SQLiteSearch.close()
│           └── ingestion/tracker.py:Tracker.record(filepath, file_hash)
│               └── INSERT OR REPLACE INTO files (file_hash, filepath, ...)
│
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
│       ├── search/sqlite_search.py:SQLiteSearch(settings.sqlite_db)
│       │   └── sqlite3.connect()
│       ├── sqlite_search.py:SQLiteSearch.search(query, limit, all_chunks)
│       │   ├── _escape_fts(query)    # Escape FTS5 special chars, prefix match, remove_diacritics
│       │   ├── [if all_chunks=False] SELECT ... JOIN documents d ON d.id = f.rowid ... → Best-per-file dedup
│       │   └── [if all_chunks=True]  SELECT ... JOIN documents d ON d.id = f.rowid ... → every chunk
│       └── sqlite_search.py:SQLiteSearch.close()
│
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
│       ├── search/sqlite_search.py:SQLiteSearch(settings.sqlite_db)
│       ├── sqlite_search.py:SQLiteSearch.stats()
│       │   └── SELECT COUNT(*) FROM documents → {"points_count": N, "backend": "sqlite-fts5"}
│       └── SQLiteSearch.close()
│
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
│       ├── cli.py:_get_backend()
│       │   └── SQLiteSearch with delete, close methods
│       │
│       ├── b["delete"](doc_id)
│       │   └── sqlite_search.py:SQLiteSearch.delete(doc_id)
│       │       └── DELETE FROM documents WHERE doc_id = ? → trigger auto-cleans FTS5
│       │
│       └── b["close"]()
│           └── sqlite_search.py:SQLiteSearch.close() → conn.close()
```

---

## `archivist clear --confirm`

```
Typer CLI entry point
├── cli.py:app()
│   └── cli.py:clear()                    # @app.command()
│       ├── [if not confirm] → raise typer.Abort()
│       │
│       ├── cli.py:_get_backend()
│       │
│       ├── b["clear"]()
│       │   └── sqlite_search.py:SQLiteSearch.delete_all()
│       │       └── DELETE FROM documents → triggers auto-clean FTS5
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
| `cli.py` | Typer CLI entry point, command routing |
| `config.py` | Pydantic settings (env vars, defaults) |
| `ingestion/extractors.py` | Text/code/PDF/DOCX/CSV/Excel/JSONL extraction, normalization, chunking |
| `ingestion/tracker.py` | SQLite SHA256 hash tracker for idempotency |
| `ingestion/pipeline.py` | Ingestion orchestration |
| `search/sqlite_search.py` | SQLite FTS5 backend (external-content with auto-sync triggers) |
| `vectorizer/hashing_tfidf.py` | HashingVectorizer vectorization |
| `utils/text.py` | Line-numbered snippet extraction |
| `api/routes.py` | FastAPI REST endpoints |
| `main.py` | FastAPI application factory |

---

## Data Flow Diagram

```
File → extract_text()
  ├── .pdf  → extract_pdf()        → pypdf page-by-page text
  ├── .docx → extract_docx()       → python-docx paragraph text
  ├── .csv/.tsv → extract_csv()    → auto-detect delimiter → flatten "header: value | header: value"
  ├── .xls/.xlsx → extract_excel() → openpyxl/xlrd → flatten rows per sheet
  ├── .jsonl → extract_jsonl()     → json.loads per line → flatten nested with dot notation
  └── .txt/.py/.js/... → extract_txt() → plain text read

→ normalize_for_display() → SQLiteSearch.upsert()
                                                        ↓
                                              documents table (INTEGER id, TEXT doc_id, content)
                                              trigger documents_ai → documents_fts (external-content, no content stored)
                                                        ↓
                                              tracker.record(file_hash)
                                                        ↓
                                              files table (SHA256 hash)

Query → SQLiteSearch.search(query) → bm25 ranking → extract_snippet() → Display
```
