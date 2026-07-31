"""Tests for Archivist API endpoints.

Covers: search, status, ingest (file/files/archive/directory),
documents (list/get/delete), archive extraction (zip/7z).
"""
from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from archivist.api.archives import ArchiveError, _is_safe_path, extract_archive, is_archive
from archivist.main import create_app
from archivist.config import Settings


@pytest.fixture(autouse=True)
def _test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point DB paths to temp dir for all tests."""
    test_settings = Settings(
        data_dir=tmp_path,
    )

    monkeypatch.setattr("archivist.api.routes.settings", test_settings)
    monkeypatch.setattr("archivist.ingestion.pipeline.settings", test_settings)
    yield


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _make_text_file(name: str, content: str) -> bytes:
    """Create an in-memory text file."""
    return content.encode("utf-8")


def _make_zip(files: dict[str, str]) -> bytes:
    """Create an in-memory ZIP archive.

    Args:
        files: Dict of {filename: content}.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_7z(files: dict[str, str]) -> bytes:
    """Create an in-memory 7z archive."""
    import py7zr
    import tempfile
    import os

    tmp = tempfile.mktemp(suffix=".7z")
    try:
        with py7zr.SevenZipFile(tmp, "w") as archive:
            for name, content in files.items():
                archive.writef(io.BytesIO(content.encode("utf-8")), name)
        data = Path(tmp).read_bytes()
    finally:
        os.unlink(tmp)
    return data


async def _poll_job(client: AsyncClient, job_id: str, max_attempts: int = 100) -> dict:
    """Poll job status until done or max attempts reached."""
    for _ in range(max_attempts):
        await asyncio.sleep(0.1)
        r = await client.get(f"/api/v1/jobs/{job_id}")
        assert r.status_code == 200
        data = r.json()
        if data["status"] == "done":
            return data
        assert data["status"] in ("pending", "running")
    raise TimeoutError(f"Job {job_id} did not complete in time")


# ── Health ────────────────────────────────────────────────────────────────────


class TestHealth:
    async def test_health(self, client: AsyncClient):
        r = await client.get("/api/v1/status")
        assert r.status_code == 200
        assert "backend" in r.json()


# ── API key auth ──────────────────────────────────────────────────────────────


class TestApiKeyAuth:
    async def test_api_key_required_when_configured(
        self, app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        secured = Settings(data_dir=tmp_path, api_key="secret-key")
        import archivist.api.security as security

        # Depends() captures a direct reference to the function, so module
        # monkeypatching won't work - dependency_overrides is the supported way.
        app.dependency_overrides[security.get_settings] = lambda: secured

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            # No key -> rejected
            r = await c.get("/api/v1/status")
            assert r.status_code == 401
            # Wrong key -> rejected
            r = await c.get("/api/v1/status", headers={"X-API-Key": "nope"})
            assert r.status_code == 401
            # Correct key -> allowed
            r = await c.get("/api/v1/status", headers={"X-API-Key": "secret-key"})
            assert r.status_code == 200
            # Health endpoint stays open (not part of the router)
            r = await c.get("/health")
            assert r.status_code == 200


# ── Status ────────────────────────────────────────────────────────────────────


class TestStatus:
    async def test_status_empty(self, client: AsyncClient):
        r = await client.get("/api/v1/status")
        assert r.status_code == 200
        data = r.json()
        assert data["points_count"] == 0
        assert data["backend"] == "sqlite-fts5"
        assert data["tracker_files"] == 0
        assert "db_size_bytes" in data

    async def test_status_json_schema(self, client: AsyncClient):
        r = await client.get("/api/v1/status")
        data = r.json()
        for key in ["points_count", "backend", "tracker_files", "db_size_bytes"]:
            assert key in data


# ── Search ────────────────────────────────────────────────────────────────────


class TestSearch:
    async def test_search_empty_index(self, client: AsyncClient):
        r = await client.get("/api/v1/search", params={"q": "hello"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["results"] == []
        assert data["query"] == "hello"

    async def test_search_response_schema(self, client: AsyncClient):
        r = await client.get("/api/v1/search", params={"q": "test"})
        data = r.json()
        for key in ["query", "total", "offset", "limit", "all_chunks", "results"]:
            assert key in data

    async def test_search_pagination_params(self, client: AsyncClient):
        r = await client.get(
            "/api/v1/search",
            params={"q": "test", "offset": 5, "size": 3},
        )
        data = r.json()
        assert data["offset"] == 5
        assert data["limit"] == 3

    async def test_search_with_content_preview(self, client: AsyncClient):
        r = await client.get(
            "/api/v1/search", params={"q": "test", "content_preview": "true"}
        )
        assert r.status_code == 200


# ── Ingest: Single File (synchronous) ─────────────────────────────────────────


class TestIngestFile:
    async def test_ingest_single_file(self, client: AsyncClient, tmp_path: Path):
        content = _make_text_file("test.txt", "Hello world, this is a test file.")
        r = await client.post(
            "/api/v1/ingest/file",
            files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["total_files"] == 1
        assert data["files"][0]["status"] == "ok"
        assert data["files"][0]["vectors"] >= 1

    async def test_ingest_file_response_schema(self, client: AsyncClient):
        content = _make_text_file("doc.py", "def hello():\n    pass\n")
        r = await client.post(
            "/api/v1/ingest/file",
            files={"file": ("doc.py", io.BytesIO(content), "text/plain")},
        )
        data = r.json()
        for key in ["status", "total_files", "total_vectors", "elapsed_seconds", "files"]:
            assert key in data

    async def test_ingest_empty_file(self, client: AsyncClient):
        r = await client.post(
            "/api/v1/ingest/file",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["files"][0]["status"] == "skipped"

    async def test_ingest_duplicate_file(self, client: AsyncClient, tmp_path: Path):
        content = _make_text_file("dup.txt", "duplicate content")
        r1 = await client.post(
            "/api/v1/ingest/file",
            files={"file": ("dup.txt", io.BytesIO(content), "text/plain")},
        )
        assert r1.status_code == 200
        assert r1.json()["files"][0]["status"] == "ok"

        r2 = await client.post(
            "/api/v1/ingest/file",
            files={"file": ("dup.txt", io.BytesIO(content), "text/plain")},
        )
        assert r2.status_code == 200
        assert r2.json()["files"][0]["status"] == "skipped"


# ── Ingest: Multi-File (async) ────────────────────────────────────────────────


class TestIngestFiles:
    async def test_ingest_multiple_files(self, client: AsyncClient, tmp_path: Path):
        files = [
            ("files", ("a.txt", io.BytesIO(b"file a content"), "text/plain")),
            ("files", ("b.txt", io.BytesIO(b"file b content"), "text/plain")),
        ]
        r = await client.post("/api/v1/ingest/files", files=files)
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        assert data["total_files"] == 2

        job_id = data["job_id"]
        result = await _poll_job(client, job_id)
        assert result["result"]["total_vectors"] >= 2

    async def test_ingest_no_files(self, client: AsyncClient):
        r = await client.post("/api/v1/ingest/files")
        assert r.status_code in (400, 422)


# ── Ingest: Archive (async) ───────────────────────────────────────────────────


class TestIngestArchive:
    async def test_ingest_zip_archive(self, client: AsyncClient, tmp_path: Path):
        zip_data = _make_zip({
            "src/main.py": "def main():\n    print('hello')\n",
            "src/utils.py": "def helper():\n    return 42\n",
        })
        r = await client.post(
            "/api/v1/ingest/archive",
            files={"file": ("code.zip", io.BytesIO(zip_data), "application/zip")},
        )
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        assert data["total_files"] == 2

        job_id = data["job_id"]
        result = await _poll_job(client, job_id)
        assert result["result"]["total_vectors"] >= 2

    async def test_ingest_7z_archive(self, client: AsyncClient, tmp_path: Path):
        zip_data = _make_7z({
            "docs/readme.md": "# Hello\nThis is a test.",
            "docs/guide.md": "# Guide\nStep 1, Step 2.",
        })
        r = await client.post(
            "/api/v1/ingest/archive",
            files={"file": ("docs.7z", io.BytesIO(zip_data), "application/x-7z-compressed")},
        )
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        assert data["total_files"] == 2

        job_id = data["job_id"]
        result = await _poll_job(client, job_id)
        assert result["result"]["total_files"] == 2

    async def test_ingest_unsupported_archive(self, client: AsyncClient, tmp_path: Path):
        r = await client.post(
            "/api/v1/ingest/archive",
            files={"file": ("file.txt", io.BytesIO(b"not an archive"), "text/plain")},
        )
        assert r.status_code == 400
        assert "Unsupported" in r.json()["detail"]


# ── Ingest: Directory (synchronous) ───────────────────────────────────────────


class TestIngestDirectory:
    async def test_ingest_directory(self, client: AsyncClient, tmp_path: Path):
        d = tmp_path / "mycode"
        d.mkdir()
        (d / "a.py").write_text("def alpha(): pass\n")
        (d / "b.py").write_text("def beta(): pass\n")

        r = await client.post(
            "/api/v1/ingest/directory",
            json={"path": str(d), "recursive": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["total_files"] == 2
        assert data["total_vectors"] >= 2

    async def test_ingest_directory_not_found(self, client: AsyncClient, tmp_path: Path):
        r = await client.post(
            "/api/v1/ingest/directory",
            json={"path": str(tmp_path / "nonexistent")},
        )
        assert r.status_code == 404

    async def test_ingest_directory_missing_path(self, client: AsyncClient, tmp_path: Path):
        r = await client.post("/api/v1/ingest/directory", json={})
        assert r.status_code == 400


# ── Documents ─────────────────────────────────────────────────────────────────


class TestDocuments:
    async def test_list_documents_empty(self, client: AsyncClient):
        r = await client.get("/api/v1/documents")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["documents"] == []

    async def test_list_documents_after_ingest(self, client: AsyncClient, tmp_path: Path):
        content = _make_text_file("doc.txt", "test content for listing")
        await client.post(
            "/api/v1/ingest/file",
            files={"file": ("doc.txt", io.BytesIO(content), "text/plain")},
        )

        r = await client.get("/api/v1/documents")
        data = r.json()
        assert data["total"] >= 1
        doc = data["documents"][0]
        assert "doc_id" in doc
        assert "filepath" in doc
        assert "file_hash" in doc

    async def test_list_documents_pagination(self, client: AsyncClient, tmp_path: Path):
        for i in range(5):
            content = _make_text_file(f"f{i}.txt", f"content {i}")
            await client.post(
                "/api/v1/ingest/file",
                files={"file": (f"f{i}.txt", io.BytesIO(content), "text/plain")},
            )

        r = await client.get("/api/v1/documents", params={"limit": 2, "offset": 0})
        data = r.json()
        assert len(data["documents"]) == 2
        assert data["total"] >= 5

    async def test_list_documents_filter_ext(self, client: AsyncClient, tmp_path: Path):
        for name, content in [("a.py", "py code"), ("b.txt", "text content")]:
            c = _make_text_file(name, content)
            await client.post(
                "/api/v1/ingest/file",
                files={"file": (name, io.BytesIO(c), "text/plain")},
            )

        r = await client.get("/api/v1/documents", params={"file_ext": ".py"})
        data = r.json()
        assert all(".py" in d["filepath"] for d in data["documents"])

    async def test_get_document_not_found(self, client: AsyncClient):
        r = await client.get("/api/v1/documents/nonexistent_id")
        assert r.status_code == 404

    async def test_delete_document(self, client: AsyncClient, tmp_path: Path):
        content = _make_text_file("del.txt", "delete me")
        ingest_r = await client.post(
            "/api/v1/ingest/file",
            files={"file": ("del.txt", io.BytesIO(content), "text/plain")},
        )
        doc_id = ingest_r.json()["files"][0].get("doc_id", "")
        if not doc_id:
            list_r = await client.get("/api/v1/documents")
            docs = list_r.json()["documents"]
            doc_id = docs[0]["doc_id"] if docs else None

        if doc_id:
            r = await client.delete(f"/api/v1/documents/{doc_id}")
            assert r.status_code == 200
            assert r.json()["status"] == "deleted"


# ── Archive Utilities ─────────────────────────────────────────────────────────


class TestArchiveUtilities:
    def test_is_archive(self):
        assert is_archive("file.zip") is True
        assert is_archive("file.rar") is True
        assert is_archive("file.7z") is True
        assert is_archive("file.txt") is False
        assert is_archive("file.py") is False

    def test_extract_zip(self, tmp_path: Path):
        zip_data = _make_zip({"a.txt": "hello", "b.txt": "world"})
        files = extract_archive(zip_data, "test.zip", dest=tmp_path)
        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"a.txt", "b.txt"}

    def test_extract_zip_nested_dirs(self, tmp_path: Path):
        zip_data = _make_zip({
            "src/main.py": "code",
            "tests/test_main.py": "test code",
        })
        files = extract_archive(zip_data, "project.zip", dest=tmp_path)
        assert len(files) == 2

    def test_extract_zip_empty(self, tmp_path: Path):
        zip_data = _make_zip({})
        files = extract_archive(zip_data, "empty.zip", dest=tmp_path)
        assert len(files) == 0

    def test_extract_zip_corrupt(self, tmp_path: Path):
        # Magic-byte validation runs before extraction and rejects the fake
        # bytes with its own message rather than the zipfile parser error.
        with pytest.raises(ArchiveError, match="contents don't match"):
            extract_archive(b"not a zip", "bad.zip", dest=tmp_path)

    def test_extract_unsupported_format(self, tmp_path: Path):
        with pytest.raises(ArchiveError, match="Unsupported"):
            extract_archive(b"data", "file.txt", dest=tmp_path)

    def test_is_safe_path(self, tmp_path: Path):
        assert _is_safe_path(tmp_path, "file.txt") is True
        assert _is_safe_path(tmp_path, "subdir/file.txt") is True
        assert _is_safe_path(tmp_path, "../etc/passwd") is False
        assert _is_safe_path(tmp_path, "../../etc/passwd") is False

    def test_extract_7z(self, tmp_path: Path):
        zip_data = _make_7z({"doc.md": "# Hello"})
        files = extract_archive(zip_data, "test.7z", dest=tmp_path)
        assert len(files) == 1
        assert files[0].name == "doc.md"