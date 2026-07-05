from pathlib import Path

import pytest

from archivist.ingestion.extractors import (
    SUPPORTED_EXTENSIONS,
    normalize_text,
    sha256_file,
    should_chunk,
    iter_files,
    chunk_pdf_by_page,
    chunk_docx_by_section,
)


def test_normalize_text_lowercases_and_collapses_whitespace():
    assert normalize_text("Hello   WORLD\n\n\tfoo") == "hello world foo"


def test_sha256_file_deterministic(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    assert sha256_file(f) == sha256_file(f)


def test_iter_files_single(tmp_path: Path):
    (tmp_path / "doc.txt").write_text("hi")
    files = list(iter_files(tmp_path / "doc.txt", recursive=False))
    assert len(files) == 1


def test_iter_files_dir(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.docx").write_bytes(b"PK\x03\x04")
    files = sorted(iter_files(tmp_path))
    assert len(files) == 3
    assert files[0].name == "a.txt"
    assert files[1].name == "b.pdf"
    assert files[2].name == "c.docx"


def test_iter_files_ignores_other_extensions(tmp_path: Path):
    (tmp_path / "image.jpg").write_bytes(b"\xff\xd8")
    (tmp_path / "doc.txt").write_text("hi")
    files = list(iter_files(tmp_path))
    assert all(f.suffix == ".txt" for f in files)


def test_should_chunk_small_file(tmp_path: Path):
    (tmp_path / "small.txt").write_text("small content")
    assert not should_chunk(tmp_path / "small.txt", "small content")
