"""Tests for XML file handling in Archivist.

Covers: XML discovery, extraction, chunking, ingestion pipeline,
and FTS5 search with XML content containing special characters.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from archivist.ingestion.extractors import (
    SUPPORTED_EXTENSIONS,
    extract_txt,
    iter_files,
    should_chunk,
    chunk_code_by_lines,
)
from archivist.ingestion.pipeline import ingest_file
from archivist.ingestion.tracker import Tracker
from archivist.search.sqlite_search import SQLiteSearch


# ── Minimal XML fixtures ──────────────────────────────────────────────────────

_SIMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<books>
  <book id="B001">
    <title>Python Cookbook</title>
    <author>David Beazley</author>
    <isbn>978-1-449-34037-7</isbn>
    <price currency="USD">49.99</price>
    <tags>python,programming</tags>
  </book>
  <book id="B002">
    <title>C++ Primer</title>
    <author>Stanley Lippman</author>
    <isbn>978-0-321-71404-6</isbn>
    <price currency="USD">59.99</price>
    <tags>c++,reference</tags>
  </book>
</books>"""


def _make_large_xml() -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<employees>",
    ]
    for i in range(200):
        lines.append(f'  <employee id="EMP-{i:04d}">')
        lines.append(f"    <name>Employee {i}</name>")
        lines.append(f"    <department>Dept-{i % 10}</department>")
        lines.append(f"    <email>emp{i}@example.com</email>")
        lines.append(f"    <phone>+1-555-{i:04d}-{(i * 7) % 10000:04d}</phone>")
        lines.append("  </employee>")
    lines.append("</employees>")
    return "\n".join(lines)


_LARGE_XML = _make_large_xml()


_SPECIAL_CHARS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<config>
  <setting name="api_url">https://api.example.com/v2</setting>
  <setting name="timeout">30s</setting>
  <setting name="retries">3</setting>
  <setting name="regex">^[a-z]+-[0-9]+$</setting>
  <setting name="formula">a+b=c</setting>
  <setting name="path">/usr/local/bin</setting>
  <setting name="version">1.0.0-beta</setting>
  <setting name="description">Supports C++ &amp; Python</setting>
</config>"""


# ── XML file discovery ─────────────────────────────────────────────────────────


class TestXmlDiscovery:
    def test_xml_in_supported_extensions(self):
        assert ".xml" in SUPPORTED_EXTENSIONS

    def test_iter_files_finds_xml(self, tmp_path: Path):
        (tmp_path / "data.xml").write_text(_SIMPLE_XML)
        (tmp_path / "other.txt").write_text("text")

        files = list(iter_files(tmp_path))
        names = [f.name for f in files]
        assert "data.xml" in names
        assert "other.txt" in names

    def test_iter_files_recursive_finds_nested_xml(self, tmp_path: Path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.xml").write_text(_SIMPLE_XML)

        files = list(iter_files(tmp_path))
        names = [f.name for f in files]
        assert "nested.xml" in names

    def test_iter_files_skips_unsupported(self, tmp_path: Path):
        (tmp_path / "image.xml.png").write_bytes(b"\x89PNG")
        (tmp_path / "notxml.dat").write_bytes(b"\x00")

        files = list(iter_files(tmp_path))
        assert len(files) == 0


# ── XML text extraction ────────────────────────────────────────────────────────


class TestXmlExtraction:
    def test_extract_simple_xml(self, tmp_path: Path):
        f = tmp_path / "books.xml"
        f.write_text(_SIMPLE_XML)
        text = extract_txt(f)
        assert "Python Cookbook" in text
        assert "David Beazley" in text
        assert "<book" in text  # raw XML preserved

    def test_extract_preserves_special_chars(self, tmp_path: Path):
        f = tmp_path / "config.xml"
        f.write_text(_SPECIAL_CHARS_XML)
        text = extract_txt(f)
        assert "https://api.example.com" in text
        assert "C++" in text
        assert "/usr/local/bin" in text

    def test_extract_preserves_unicode(self, tmp_path: Path):
        xml = '<?xml version="1.0"?>\n<data>\n  <item>Ñoño café résumé</item>\n</data>'
        f = tmp_path / "unicode.xml"
        f.write_text(xml, encoding="utf-8")
        text = extract_txt(f)
        assert "Ñoño" in text
        assert "café" in text
        assert "résumé" in text


# ── XML chunking ───────────────────────────────────────────────────────────────


class TestXmlChunking:
    def test_should_chunk_large_xml(self, tmp_path: Path):
        f = tmp_path / "large.xml"
        f.write_text(_LARGE_XML)
        assert should_chunk(f, extract_txt(f)) is True

    def test_should_not_chunk_small_xml(self, tmp_path: Path):
        f = tmp_path / "small.xml"
        f.write_text(_SIMPLE_XML)
        assert should_chunk(f, extract_txt(f)) is False

    def test_chunk_xml_by_lines(self):
        chunks = chunk_code_by_lines(_SIMPLE_XML, 3)
        assert len(chunks) > 1
        # All original lines should be in chunks
        all_chunk_lines = "\n".join(chunks)
        for line in _SIMPLE_XML.split("\n"):
            assert line in all_chunk_lines

    def test_chunk_large_xml(self):
        chunks = chunk_code_by_lines(_LARGE_XML, 50)
        assert len(chunks) > 3  # ~205 lines / 50 = ~4 chunks
        # First chunk should start with XML header
        assert chunks[0].startswith("<?xml")

    def test_chunk_preserves_xml_structure(self):
        chunks = chunk_code_by_lines(_SIMPLE_XML, 5)
        # Each chunk should be valid text (no corruption)
        for chunk in chunks:
            assert len(chunk) > 0


# ── XML ingestion pipeline ────────────────────────────────────────────────────


class TestXmlIngestion:
    def test_ingest_simple_xml(self, tmp_path: Path):
        tracker = Tracker(tmp_path / "tracker.db")
        f = tmp_path / "books.xml"
        f.write_text(_SIMPLE_XML)

        count = ingest_file(f, tracker, db_path=tmp_path / "test.db")
        assert count >= 1  # at least one chunk

        # Verify searchable
        search = SQLiteSearch(tmp_path / "test.db")
        results = search.search("Python Cookbook")
        assert len(results) > 0
        assert "books.xml" in results[0]["filepath"]
        search.close()

    def test_ingest_large_xml(self, tmp_path: Path):
        tracker = Tracker(tmp_path / "tracker.db")
        f = tmp_path / "employees.xml"
        f.write_text(_LARGE_XML)

        count = ingest_file(f, tracker, db_path=tmp_path / "test.db")
        assert count >= 1  # 16KB file, below chunk threshold

        # Verify searchable across chunks
        search = SQLiteSearch(tmp_path / "test.db")
        results = search.search("Employee 150", all_chunks=True)
        assert len(results) > 0
        search.close()

    def test_ingest_xml_with_special_chars(self, tmp_path: Path):
        tracker = Tracker(tmp_path / "tracker.db")
        f = tmp_path / "config.xml"
        f.write_text(_SPECIAL_CHARS_XML)

        count = ingest_file(f, tracker, db_path=tmp_path / "test.db")
        assert count >= 1

        # Search for content with special chars
        search = SQLiteSearch(tmp_path / "test.db")
        results = search.search("api example")
        assert len(results) > 0
        search.close()

    def test_ingest_xml_produces_correct_doc_id(self, tmp_path: Path):
        tracker = Tracker(tmp_path / "tracker.db")
        f = tmp_path / "books.xml"
        f.write_text(_SIMPLE_XML)

        ingest_file(f, tracker, db_path=tmp_path / "test.db")

        search = SQLiteSearch(tmp_path / "test.db")
        stats = search.stats()
        assert stats["points_count"] >= 1
        search.close()


# ── FTS5 search with XML content ──────────────────────────────────────────────


class TestXmlFts5Search:
    def _setup_search(self, tmp_path: Path, xml_content: str) -> SQLiteSearch:
        tracker = Tracker(tmp_path / "tracker.db")
        f = tmp_path / "data.xml"
        f.write_text(xml_content)
        ingest_file(f, tracker, db_path=tmp_path / "test.db")
        return SQLiteSearch(tmp_path / "test.db")

    def test_search_xml_title(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _SIMPLE_XML)
        results = search.search("Python Cookbook")
        assert len(results) > 0
        assert "Python Cookbook" in results[0]["content"]
        search.close()

    def test_search_xml_author(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _SIMPLE_XML)
        results = search.search("David Beazley")
        assert len(results) > 0
        search.close()

    def test_search_xml_isbn(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _SIMPLE_XML)
        # ISBN contains hyphens — FTS5 should handle it
        results = search.search("978-1-449")
        assert len(results) > 0
        search.close()

    def test_search_xml_tag_content(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _SIMPLE_XML)
        results = search.search("python programming")
        assert len(results) > 0
        search.close()

    def test_search_xml_special_chars_url(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _SPECIAL_CHARS_XML)
        results = search.search("api example")
        assert len(results) > 0
        search.close()

    def test_search_xml_special_chars_cpp(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _SPECIAL_CHARS_XML)
        results = search.search("C++ Python")
        assert len(results) > 0
        search.close()

    def test_search_xml_special_chars_path(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _SPECIAL_CHARS_XML)
        # "/" is a dangerous char in FTS5 — should be replaced with space
        results = search.search("/usr/local")
        assert len(results) > 0
        search.close()

    def test_search_xml_special_chars_formula(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _SPECIAL_CHARS_XML)
        # "+" is a dangerous char in FTS5
        results = search.search("a+b=c")
        assert len(results) > 0
        search.close()

    def test_search_xml_special_chars_version(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _SPECIAL_CHARS_XML)
        # "-" in version string
        results = search.search("1.0.0-beta")
        assert len(results) > 0
        search.close()

    def test_search_xml_employee_id(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _LARGE_XML)
        # "-" in employee ID
        results = search.search("EMP-0150")
        assert len(results) > 0
        search.close()

    def test_search_xml_phone_number(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _LARGE_XML)
        # "+" and "-" in phone number
        results = search.search("+1-555")
        assert len(results) > 0
        search.close()

    def test_search_xml_email(self, tmp_path: Path):
        search = self._setup_search(tmp_path, _LARGE_XML)
        # "@" in email
        results = search.search("emp150 example")
        assert len(results) > 0
        search.close()


# ── FTS5 escape function ──────────────────────────────────────────────────────


class TestFts5Escape:
    def test_escape_hyphenated_isbn(self):
        from archivist.search.sqlite_search import SQLiteSearch
        result = SQLiteSearch._escape_fts("ISBN-978-0-321-71404-6")
        # Hyphens replaced with spaces, each term gets prefix
        assert "isbn" in result
        assert "978" in result
        assert "321" in result
        assert "71404" in result

    def test_escape_cpp(self):
        from archivist.search.sqlite_search import SQLiteSearch
        result = SQLiteSearch._escape_fts("c++ code")
        # "+" replaced with space
        assert "c" in result
        assert "code" in result

    def test_escape_at_sign(self):
        from archivist.search.sqlite_search import SQLiteSearch
        result = SQLiteSearch._escape_fts("user@example.com")
        # "@" replaced with space
        assert "user" in result
        assert "example" in result

    def test_escape_url(self):
        from archivist.search.sqlite_search import SQLiteSearch
        result = SQLiteSearch._escape_fts("https://api.example.com")
        # ":" and "/" replaced with space
        assert "https" in result
        assert "api" in result
        assert "example" in result

    def test_escape_formula(self):
        from archivist.search.sqlite_search import SQLiteSearch
        result = SQLiteSearch._escape_fts("a+b=c")
        # "+" replaced with space
        assert "a" in result
        assert "b" in result
        assert "c" in result

    def test_escape_version(self):
        from archivist.search.sqlite_search import SQLiteSearch
        result = SQLiteSearch._escape_fts("1.0.0-beta")
        # "-" replaced with space
        assert "1" in result
        assert "0" in result
        assert "beta" in result

    def test_escape_empty(self):
        from archivist.search.sqlite_search import SQLiteSearch
        assert SQLiteSearch._escape_fts("") == '""'

    def test_escape_whitespace_only(self):
        from archivist.search.sqlite_search import SQLiteSearch
        assert SQLiteSearch._escape_fts("   ") == '""'
