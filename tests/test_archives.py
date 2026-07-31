"""Deep tests for archive analysis and extraction (api/archives.py).

Covers magic-byte validation, corrupt archives, limits, zip-slip protection,
and the 7z/rar code paths that the API-level tests don't reach.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from archivist.api.archives import (
    _is_safe_path,
    _MAX_ARCHIVE_FILES,
    analyze_archive,
    extract_archive,
)
from archivist.api.archives import ArchiveError


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_7z(files: dict[str, str]) -> bytes:
    import os
    import tempfile

    import py7zr

    tmp = tempfile.mktemp(suffix=".7z")
    try:
        with py7zr.SevenZipFile(tmp, "w") as archive:
            for name, content in files.items():
                archive.writef(io.BytesIO(content.encode("utf-8")), name)
        return Path(tmp).read_bytes()
    finally:
        os.unlink(tmp)


def _make_stored_zip(files: dict[str, str]) -> bytes:
    """Create an uncompressed zip so corrupted bytes reliably break CRC."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _corrupt(bytes_: bytes) -> bytes:
    """Flip a byte in the middle to break CRC/integrity checks."""
    mid = len(bytes_) // 2
    return bytes_[:mid] + bytes((bytes_[mid] ^ 0xFF,)) + bytes_[mid + 1 :]


# ── analyze_archive ───────────────────────────────────────────────────────────


class TestAnalyzeArchive:
    def test_zip_valid(self):
        result = analyze_archive(_make_zip({"a.txt": "hello"}), "x.zip")
        assert result["valid"] is True
        assert result["format"] == ".zip"
        assert result["file_count"] == 1
        assert result["total_size"] > 0
        assert result["error"] is None

    def test_7z_valid(self):
        result = analyze_archive(_make_7z({"a.md": "# Hi"}), "x.7z")
        assert result["valid"] is True
        assert result["file_count"] == 1

    def test_unsupported_format(self):
        result = analyze_archive(b"data", "notes.txt")
        assert result["valid"] is False
        assert "Unsupported" in result["error"]

    def test_bad_signature_rejected(self):
        result = analyze_archive(b"definitely not a zip", "fake.zip")
        assert result["valid"] is False
        assert "don't match" in result["error"]

    def test_empty_file_rejected(self):
        result = analyze_archive(b"", "empty.zip")
        assert result["valid"] is False

    def test_corrupt_zip_rejected(self):
        data = _corrupt(_make_stored_zip({"a.txt": "hello world " * 1000}))
        result = analyze_archive(data, "corrupt.zip")
        assert result["valid"] is False
        assert "Corrupt" in result["error"] or "Invalid" in result["error"]

    def test_empty_zip_rejected(self):
        result = analyze_archive(_make_zip({}), "empty.zip")
        assert result["valid"] is False
        assert "no files" in result["error"]

    def test_file_count_limit(self, monkeypatch: pytest.MonkeyPatch):
        import archivist.api.archives as archives

        monkeypatch.setattr(archives, "_MAX_ARCHIVE_FILES", 2)
        data = _make_zip({"a.txt": "1", "b.txt": "2", "c.txt": "3"})
        result = analyze_archive(data, "many.zip")
        assert result["valid"] is False
        assert "max is" in result["error"]

    def test_file_count_limit_constant(self):
        assert _MAX_ARCHIVE_FILES == 5000


# ── extract_archive ───────────────────────────────────────────────────────────


class TestExtractArchive:
    def test_extract_7z(self, tmp_path: Path):
        files = extract_archive(_make_7z({"docs/readme.md": "# Hi"}), "x.7z", dest=tmp_path)
        assert [f.name for f in files] == ["readme.md"]

    def test_extract_rar_without_unrar(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr("rarfile.UNRAR_TOOL", None)
        data = b"Rar!\x1a\x07\x00" + b"fake payload"
        with pytest.raises(ArchiveError, match="unrar"):
            extract_archive(data, "fake.rar", dest=tmp_path)

    def test_extract_rar_bad_signature(self, tmp_path: Path):
        with pytest.raises(ArchiveError, match="don't match"):
            extract_archive(b"plain text", "fake.rar", dest=tmp_path)

    def test_extract_creates_dest_dir(self, tmp_path: Path):
        dest = tmp_path / "nested" / "dest"
        files = extract_archive(_make_zip({"a.txt": "x"}), "x.zip", dest=dest)
        assert files[0].exists()

    def test_extract_default_temp_dir(self):
        import shutil
        import tempfile

        before = set(Path(tempfile.gettempdir()).glob("archivist_archive_*"))
        files = extract_archive(_make_zip({"a.txt": "x"}), "x.zip")
        assert len(files) == 1
        created = set(Path(tempfile.gettempdir()).glob("archivist_archive_*")) - before
        assert created  # a temp dir was auto-created
        for p in created:
            shutil.rmtree(p, ignore_errors=True)


# ── _is_safe_path ─────────────────────────────────────────────────────────────


class TestIsSafePath:
    def test_normal_file(self, tmp_path: Path):
        assert _is_safe_path(tmp_path, "file.txt") is True

    def test_nested_file(self, tmp_path: Path):
        assert _is_safe_path(tmp_path, "sub/dir/file.txt") is True

    def test_traversal_rejected(self, tmp_path: Path):
        assert _is_safe_path(tmp_path, "../evil.txt") is False

    def test_deep_traversal_rejected(self, tmp_path: Path):
        assert _is_safe_path(tmp_path, "../../../../etc/passwd") is False

    def test_normalized_traversal_inside(self, tmp_path: Path):
        # "sub/../file.txt" normalizes to a path still inside dest - allowed.
        assert _is_safe_path(tmp_path, "sub/../file.txt") is True


# ── Signature validation ──────────────────────────────────────────────────────


class TestValidateSignature:
    def test_unknown_suffix_is_noop(self):
        # Extensions outside the archive set have no magic-byte table, so the
        # check must pass through without rejecting anything.
        from archivist.api.archives import _validate_signature

        _validate_signature(b"anything at all", ".txt")  # must not raise


# ── analyze_archive: deep error paths ─────────────────────────────────────────


class TestAnalyzeArchiveErrors:
    def test_zip_with_valid_magic_but_garbage_body(self):
        # Signature check passes (PK\x03\x04) but the central directory is
        # garbage, so ZipFile itself must fail to open.
        data = b"PK\x03\x04" + b"this is not a real zip payload"
        result = analyze_archive(data, "broken.zip")
        assert result["valid"] is False
        assert "Invalid ZIP" in result["error"]

    def test_zip_whose_testzip_raises(self, monkeypatch: pytest.MonkeyPatch):
        # A corrupt DEFLATE stream can make testzip() raise zlib.error rather
        # than returning the offending member; that must map to invalid too.
        import zlib

        import zipfile as zf_mod

        def _boom(self):
            raise zlib.error("decompress data error")

        monkeypatch.setattr(zf_mod.ZipFile, "testzip", _boom)
        result = analyze_archive(_make_stored_zip({"a.txt": "x"}), "boom.zip")
        assert result["valid"] is False
        assert "Invalid or corrupt ZIP" in result["error"]

    def test_7z_import_missing(self, monkeypatch: pytest.MonkeyPatch):
        import sys

        monkeypatch.setitem(sys.modules, "py7zr", None)
        # Raw valid signature only - can't use _make_7z() because that helper
        # itself imports py7zr and would fail under the patch.
        result = analyze_archive(b"7z\xbc\xaf\x27\x1c\x00\x04", "x.7z")
        assert result["valid"] is False
        assert "py7zr" in result["error"]

    def test_7z_with_valid_magic_but_garbage_body(self):
        data = b"7z\xbc\xaf\x27\x1c" + b"garbage payload"
        result = analyze_archive(data, "broken.7z")
        assert result["valid"] is False
        assert result["error"]  # rejected before any extraction

    def test_7z_corrupt_member_reported(self, monkeypatch: pytest.MonkeyPatch):
        import py7zr

        monkeypatch.setattr(py7zr.SevenZipFile, "testzip", lambda self: ["a.md"])
        result = analyze_archive(_make_7z({"a.md": "# Hi"}), "corrupt.7z")
        assert result["valid"] is False
        assert "Corrupt" in result["error"]

    def test_rar_without_unrar_binary(self, monkeypatch: pytest.MonkeyPatch):
        import rarfile

        monkeypatch.setattr(rarfile, "UNRAR_TOOL", None)
        data = b"Rar!\x1a\x07\x00" + b"fake payload"
        result = analyze_archive(data, "x.rar")
        assert result["valid"] is False
        assert "unrar" in result["error"]


# ── extract_archive: deep error paths ─────────────────────────────────────────


class TestExtractArchiveErrors:
    def test_zip_with_valid_magic_but_garbage_body(self, tmp_path: Path):
        with pytest.raises(ArchiveError, match="Invalid ZIP"):
            extract_archive(
                b"PK\x03\x04" + b"not a real zip payload", "broken.zip", dest=tmp_path
            )

    def test_zip_over_file_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import archivist.api.archives as archives

        monkeypatch.setattr(archives, "_MAX_ARCHIVE_FILES", 1)
        data = _make_zip({"a.txt": "1", "b.txt": "2"})
        with pytest.raises(ArchiveError, match="max is"):
            extract_archive(data, "many.zip", dest=tmp_path)

    def test_zip_path_traversal_rejected(self, tmp_path: Path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.txt", "pwned")
        with pytest.raises(ArchiveError, match="Path traversal"):
            extract_archive(buf.getvalue(), "evil.zip", dest=tmp_path)

    def test_7z_import_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import sys

        monkeypatch.setitem(sys.modules, "py7zr", None)
        with pytest.raises(ArchiveError, match="py7zr"):
            extract_archive(b"7z\xbc\xaf\x27\x1c\x00\x04", "x.7z", dest=tmp_path)

    def test_7z_over_file_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import archivist.api.archives as archives

        monkeypatch.setattr(archives, "_MAX_ARCHIVE_FILES", 1)
        data = _make_7z({"a.md": "# A", "b.md": "# B"})
        with pytest.raises(ArchiveError, match="max is"):
            extract_archive(data, "many.7z", dest=tmp_path)

    def test_rar_without_unrar_binary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import rarfile

        monkeypatch.setattr(rarfile, "UNRAR_TOOL", None)
        data = b"Rar!\x1a\x07\x00" + b"fake payload"
        with pytest.raises(ArchiveError, match="unrar"):
            extract_archive(data, "x.rar", dest=tmp_path)

    def test_rar_import_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import sys

        monkeypatch.setitem(sys.modules, "rarfile", None)
        data = b"Rar!\x1a\x07\x00" + b"fake payload"
        with pytest.raises(ArchiveError, match="rarfile"):
            extract_archive(data, "x.rar", dest=tmp_path)


# ── _is_safe_path: exception handling ─────────────────────────────────────────


class TestIsSafePathErrors:
    def test_unresolvable_name_returns_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import pathlib

        # A name that cannot be resolved (e.g. an embedded null byte on some
        # platforms) must be treated as unsafe rather than crashing. Force the
        # resolution failure so the test is platform-independent.
        def _boom(self, strict=False):
            raise ValueError("embedded null byte")

        monkeypatch.setattr(pathlib.Path, "resolve", _boom)
        assert _is_safe_path(tmp_path, "file.txt") is False
