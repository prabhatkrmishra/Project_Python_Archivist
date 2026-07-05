# Archivist

Fully offline, CLI-first document search tool. Ingests 30+ file types — code, docs, config, markdown — indexes them with SQLite FTS5 (default) or Qdrant vector search, and lets you search with line-numbered context — all without any external API calls at runtime.

## Features

- **Zero external services** — SQLite FTS5 backend runs entirely in-process
- **Two backends** — SQLite FTS5 (default, fast) or Qdrant (vector search, optional)
- **Persistent config** — `archivist use <backend>` saves your choice, no flags needed
- **30+ file types** — code (.py, .js, .ts, .java, .c, .cpp, .go, .rs, etc.), docs (.pdf, .docx, .md, .txt), config (.json, .yaml, .toml, .xml)
- **Incremental ingestion** — SHA256 hash tracker skips already-indexed files
- **Line-numbered output** — search results show `> L42: matching line` with context
- **Large-file chunking** — PDFs split by page, DOCX by section, when >10MB / >100 pages
- **API key auth** — `X-API-Key` header for deployed endpoints
- **CLI + REST API** — use from terminal or integrate via HTTP

## Quick Start

### Prerequisites

- Python 3.13+

### Install

```bash
cd archivist
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
pip install -e .
```

### Select backend (once)

```bash
archivist use sqlite    # Fast, zero external services (default)
archivist use qdrant    # Vector search, requires Qdrant server
```

### Build distributable package

```bash
pip install build
python -m build
# Creates dist/archivist-0.1.0-py3-none-any.whl
```

### Install globally from wheel

```bash
pip install dist/archivist-0.1.0-py3-none-any.whl
archivist --help
```

### Ingest a directory

```bash
archivist ingest ./my_documents
```

### Search

```bash
archivist search "quarterly budget"
archivist search "project alpha" --json   # machine-readable
```

### Status

```bash
archivist status
```

## Usage

### CLI Commands

| Command | Description |
|---------|-------------|
| `archivist use [backend]` | Select and persist default backend |
| `archivist ingest <path>` | Ingest a file or directory |
| `archivist search "<query>"` | Search ingested documents |
| `archivist status` | Show index stats |
| `archivist delete <doc_id>` | Delete a document by ID |
| `archivist clear --confirm` | Delete all indexed data |
| `archivist reindex --confirm` | Rebuild all vectors |

### Backend Selection

```bash
# Show current backend and options
archivist use

# Set persistent backend (saved to ~/.config/archivist/backend)
archivist use sqlite    # FTS5 keyword search, zero external services
archivist use qdrant    # Vector search, requires Qdrant server

# Override for a single command (temporary)
archivist ingest ./docs -b qdrant
archivist search "query" -b sqlite
```

### Ingest Options

```bash
archivist ingest /path/to/docs \
  --recursive        # walk subdirectories (default: true) \
  --no-recursive     # only top-level files \
  --workers 8        # parallel workers (default: CPU count) \
  --chunk            # chunk large files (default: true) \
  --no-chunk         # index whole file as single vector \
  --bm25             # use Qdrant BM25 instead of HashingVectorizer
```

### Search Options

```bash
archivist search "machine learning" \
  --top 10           # number of results (default: 10) \
  --json             # JSON output for scripting
```

### Example Output

```
[1] H:\projects\my_docs\report.txt
Source: report.txt  |  Match: score=0.8234
  ...
  L41: revenue growth exceeded expectations
  L42: quarterly budget analysis shows profit margin
► L43: the quarterly report indicates strong performance
  L44: financial projections for next quarter
  L45: budget allocation for Q3
  ...
```

## API Usage

All endpoints are under `/api/v1`. Set `ARCHIVIST_API_KEY` env var to enable `X-API-Key` auth.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/search?q=...` | Search documents |
| `GET` | `/api/v1/status` | Index stats |
| `POST` | `/api/v1/ingest/file` | Upload single file (multipart) |
| `DELETE` | `/api/v1/documents/{id}` | Delete by ID |

### Examples

```bash
# Search
curl "http://localhost:8000/api/v1/search?q=quarterly+budget&size=5"

# Status
curl http://localhost:8000/api/v1/status

# Upload a file
curl -X POST -F "file=@report.pdf" http://localhost:8000/api/v1/ingest/file

# Delete
curl -X DELETE http://localhost:8000/api/v1/documents/abc-123-def

# Health check
curl http://localhost:8000/health
```

## How It Works

1. **Extraction** — Files are read based on type:
   - **Text/code files** (.py, .js, .ts, .java, .c, .go, .rs, .md, .json, .yaml, etc.) — read as plain text
   - **PDFs** — text extracted page-by-page via pypdf
   - **DOCX** — text extracted paragraph-by-paragraph via python-docx
   - Text is normalized (lowercased for vectorization, original case preserved for display).

2. **Indexing** — SQLite FTS5 (default) creates an inverted index for fast keyword search. Alternatively, Qdrant stores sparse TF-IDF vectors with HNSW indexing.

3. **Search** — Your query is matched against the index. Results are ranked by relevance (BM25 for SQLite, cosine similarity for Qdrant) and displayed with line-numbered context.

4. **Idempotency** — A SQLite tracker stores SHA256 hashes of ingested files. Re-running `archivist ingest` skips already-indexed files.

## Configuration

### Backend Config

Saved to `~/.config/archivist/backend`:

```bash
archivist use sqlite    # Creates/updates config file
archivist use           # Shows current + options
```

### Environment Variables

Override with env vars or `.env` file:

```bash
# Storage
ARCHIVIST_DATA_DIR=~/.local/share/archivist

# Qdrant backend (when using qdrant)
ARCHIVIST_QDRANT_URL=http://localhost:6333
ARCHIVIST_QDRANT_API_KEY=your-key

# Vectorizer
ARCHIVIST_VECTORIZER_N_FEATURES=1048576   # 2^20 dimensions
ARCHIVIST_VECTORIZER_USE_BM25=false

# API
ARCHIVIST_API_HOST=0.0.0.0
ARCHIVIST_API_PORT=8000
ARCHIVIST_API_KEY=your-api-key
```

## Architecture

```
archivist/
├── src/archivist/
│   ├── __init__.py          # Package metadata
│   ├── cli.py               # Typer CLI (use, ingest, search, status, delete, clear)
│   ├── config.py            # Pydantic settings with env override
│   ├── main.py              # FastAPI application factory
│   ├── api/
│   │   ├── routes.py        # REST API endpoints
│   │   └── schemas.py       # API schemas (placeholder)
│   ├── ingestion/
│   │   ├── extractors.py    # PDF/DOCX/TXT extraction + normalization
│   │   ├── pipeline.py      # Ingestion orchestration
│   │   └── tracker.py       # SQLite idempotency tracker
│   ├── search/
│   │   ├── sqlite_search.py # SQLite FTS5 backend (default)
│   │   └── qdrant_client.py # Qdrant vector backend (optional)
│   ├── utils/
│   │   └── text.py          # Snippet extraction with line numbers
│   └── vectorizer/
│       └── hashing_tfidf.py # HashingVectorizer + BM25 vectorization
├── tests/                   # 62 tests (all backends)
├── docs/
│   └── bare-metal.md        # Production deployment guide
├── pyproject.toml           # Dependencies and build config
├── requirements.txt         # Runtime dependencies
├── requirements-dev.txt     # Dev/test dependencies
├── WORKING.md               # Detailed function flow documentation
└── README.md                # This file
```

## Backend Comparison

| Feature | SQLite FTS5 (default) | Qdrant |
|---------|----------------------|--------|
| External services | None | Qdrant server required |
| Search speed | ~1ms | ~20ms (cold), ~5ms (warm) |
| Ranking | BM25 | Cosine similarity |
| Setup complexity | Zero | Docker/WSL2 needed |
| Scale limit | ~100K files | ~1M+ files |
| Vector search | No | Yes |

## Tests

```bash
pip install -r requirements-dev.txt
pip install -e .
pytest tests/ -v
```

**62 tests** covering:
- Text extraction (TXT, PDF, DOCX)
- Normalization and chunking
- Vectorization (HashingVectorizer + BM25)
- SQLite FTS5 search, delete, stats
- Qdrant search, delete, stats
- SQLite tracker idempotency
- Directory walk + mempalace output logic
- End-to-end ingest → search → verify

## FAQ

**Why SQLite FTS5 as default?**
Zero external services. No Docker, no Qdrant server, no JVM. Just Python's built-in SQLite. Fast enough for most use cases (sub-millisecond search on 100K documents).

**When should I use Qdrant?**
For 1M+ files, vector similarity search, or when you need semantic search beyond exact keywords.

**Can I switch backends?**
Yes. Use `archivist use <backend>` to change permanently, or `-b <backend>` for a single command. Data is stored separately per backend.

**Why not neural embeddings?**
The "no AI models" constraint. HashingVectorizer gives real vector search quality without any pretrained model, ONNX runtime, or GPU.

**Is the API key required?**
No. If `ARCHIVIST_API_KEY` is not set, the API endpoints are open.

## License

MIT
