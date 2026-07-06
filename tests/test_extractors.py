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
    extract_csv,
    extract_excel,
    extract_jsonl,
    chunk_csv_by_rows,
    chunk_jsonl_by_lines,
    chunk_code_by_lines,
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


# --- CSV Extraction Tests ---


def test_extract_csv_basic(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.write_text("name,age,city\nAlice,30,New York\nBob,25,San Francisco\n")
    result = extract_csv(f)
    assert "name | age | city" in result
    assert "name: Alice | age: 30 | city: New York" in result
    assert "name: Bob | age: 25 | city: San Francisco" in result


def test_extract_csv_tab_delimited(tmp_path: Path):
    f = tmp_path / "data.tsv"
    f.write_text("name\tage\tcity\nAlice\t30\tNew York\nBob\t25\tSan Francisco\n")
    result = extract_csv(f)
    assert "name | age | city" in result
    assert "name: Alice | age: 30 | city: New York" in result


def test_extract_csv_semicolon(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.write_text("name;age;city\nAlice;30;New York\n")
    result = extract_csv(f)
    assert "name | age | city" in result
    assert "name: Alice | age: 30 | city: New York" in result


def test_extract_csv_unicode(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.write_text("name,city\nAlice,café\nBob,résumé\n", encoding="utf-8")
    result = extract_csv(f)
    assert "café" in result
    assert "résumé" in result


def test_extract_csv_empty_rows(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.write_text("name,age\nAlice,30\n\n\nBob,25\n")
    result = extract_csv(f)
    assert "name: Alice | age: 30" in result
    assert "name: Bob | age: 25" in result


def test_extract_csv_single_column(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.write_text("fruit\napple\nbanana\ncherry\n")
    result = extract_csv(f)
    assert "fruit" in result
    assert "apple" in result
    assert "banana" in result


def test_extract_csv_latin1_encoding(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.write_bytes("name,city\nAlice,Ñoño\n".encode("latin-1"))
    result = extract_csv(f)
    assert "Ñoño" in result


# --- Excel Extraction Tests ---


def test_extract_excel_xlsx(tmp_path: Path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["name", "age", "city"])
    ws.append(["Alice", 30, "New York"])
    ws.append(["Bob", 25, "San Francisco"])
    f = tmp_path / "data.xlsx"
    wb.save(f)
    wb.close()

    result = extract_excel(f)
    assert "[Sheet: Sheet1]" in result
    assert "name | age | city" in result
    assert "name: Alice | age: 30 | city: New York" in result
    assert "name: Bob | age: 25 | city: San Francisco" in result


def test_extract_excel_multiple_sheets(tmp_path: Path):
    from openpyxl import Workbook

    wb = Workbook()
    ws1 = wb.active
    ws1.title = "People"
    ws1.append(["name", "age"])
    ws1.append(["Alice", 30])
    ws2 = wb.create_sheet("Cities")
    ws2.append(["city", "population"])
    ws2.append(["NYC", 8000000])
    f = tmp_path / "multi.xlsx"
    wb.save(f)
    wb.close()

    result = extract_excel(f)
    assert "[Sheet: People]" in result
    assert "[Sheet: Cities]" in result
    assert "name: Alice | age: 30" in result
    assert "city: NYC | population: 8000000" in result


def test_extract_excel_xls(tmp_path: Path):
    import xlwt

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Data")
    ws.write(0, 0, "name")
    ws.write(0, 1, "score")
    ws.write(1, 0, "Alice")
    ws.write(1, 1, 95)
    f = tmp_path / "data.xls"
    wb.save(f)

    result = extract_excel(f)
    assert "[Sheet: Data]" in result
    assert "name | score" in result
    assert "name: Alice | score: 95.0" in result


def test_extract_excel_empty_sheet(tmp_path: Path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Empty"
    f = tmp_path / "empty.xlsx"
    wb.save(f)
    wb.close()

    result = extract_excel(f)
    assert result == ""


# --- JSONL Extraction Tests ---


def test_extract_jsonl_basic(tmp_path: Path):
    f = tmp_path / "data.jsonl"
    f.write_text('{"name": "Alice", "age": 30}\n{"name": "Bob", "age": 25}\n')
    result = extract_jsonl(f)
    assert "L1:" in result
    assert "name: Alice" in result
    assert "age: 30" in result
    assert "L2:" in result
    assert "name: Bob" in result


def test_extract_jsonl_nested(tmp_path: Path):
    f = tmp_path / "data.jsonl"
    f.write_text('{"name": "Alice", "address": {"city": "NYC", "zip": "10001"}}\n')
    result = extract_jsonl(f)
    assert "address.city: NYC" in result
    assert "address.zip: 10001" in result


def test_extract_jsonl_arrays(tmp_path: Path):
    f = tmp_path / "data.jsonl"
    f.write_text('{"name": "Alice", "tags": ["admin", "user"]}\n')
    result = extract_jsonl(f)
    assert "tags:" in result
    assert "admin" in result


def test_extract_jsonl_empty_lines(tmp_path: Path):
    f = tmp_path / "data.jsonl"
    f.write_text('{"name": "Alice"}\n\n\n{"name": "Bob"}\n')
    result = extract_jsonl(f)
    assert "L1:" in result
    assert "L4:" in result


def test_extract_jsonl_invalid_json(tmp_path: Path):
    f = tmp_path / "data.jsonl"
    f.write_text('{"name": "Alice"}\nnot valid json\n{"name": "Bob"}\n')
    result = extract_jsonl(f)
    assert "L1:" in result
    assert "L2: not valid json" in result
    assert "L3:" in result


def test_extract_jsonl_mixed_types(tmp_path: Path):
    f = tmp_path / "data.jsonl"
    f.write_text('"just a string"\n42\n{"key": "value"}\n')
    result = extract_jsonl(f)
    assert "L1: just a string" in result
    assert "L2: 42" in result
    assert "L3: key: value" in result


# --- Chunking Tests ---


def test_chunk_csv_by_rows():
    lines = ["name,age,city"] + [f"person{i},{20+i},city{i}" for i in range(100)]
    text = "\n".join(lines)
    chunks = chunk_csv_by_rows(text, rows_per_chunk=30)
    assert len(chunks) == 4  # 30+30+30+10
    assert chunks[0].startswith("name,age,city\n")
    assert "person0" in chunks[0]
    assert "person29" in chunks[0]
    assert "person30" in chunks[1]


def test_chunk_jsonl_by_lines():
    lines = [f'{{"id": {i}, "val": "x"}}' for i in range(1200)]
    text = "\n".join(lines)
    chunks = chunk_jsonl_by_lines(text, lines_per_chunk=500)
    assert len(chunks) == 3  # 500+500+200


def test_chunk_csv_single_chunk():
    text = "name,age\nAlice,30\nBob,25\n"
    chunks = chunk_csv_by_rows(text, rows_per_chunk=100)
    assert len(chunks) == 1
    assert chunks[0] == text


# --- File Discovery Tests ---


def test_iter_files_includes_xlsx_jsonl(tmp_path: Path):
    (tmp_path / "a.xlsx").write_bytes(b"")
    (tmp_path / "b.jsonl").write_text("")
    (tmp_path / "c.txt").write_text("")
    files = sorted(iter_files(tmp_path))
    names = [f.name for f in files]
    assert "a.xlsx" in names
    assert "b.jsonl" in names
    assert "c.txt" in names


def test_supported_extensions_includes_tabular():
    assert ".csv" in SUPPORTED_EXTENSIONS
    assert ".tsv" in SUPPORTED_EXTENSIONS
    assert ".xls" in SUPPORTED_EXTENSIONS
    assert ".xlsx" in SUPPORTED_EXTENSIONS
    assert ".jsonl" in SUPPORTED_EXTENSIONS


# --- Code Chunking Tests ---


def test_chunk_code_by_lines_small_file(tmp_path: Path):
    """Small files stay as single chunk."""
    text = "def hello():\n    return 'world'\n"
    chunks = chunk_code_by_lines(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_code_by_lines_exact_boundary(tmp_path: Path):
    """File at exactly 1500 lines stays as single chunk."""
    lines = [f"L{i:04d}" for i in range(1500)]
    text = "\n".join(lines)
    chunks = chunk_code_by_lines(text)
    assert len(chunks) == 1


def test_chunk_code_by_lines_splits_large_file(tmp_path: Path):
    """Large files are split into chunks of 1500 lines."""
    lines = [f"L{i:04d}" for i in range(4200)]
    text = "\n".join(lines)
    chunks = chunk_code_by_lines(text)
    assert len(chunks) == 3  # 1500+1500+1200
    for c in chunks:
        assert c.strip()  # no empty chunks


def test_chunk_code_by_lines_preserves_content(tmp_path: Path):
    """Chunk content matches original lines."""
    lines = [f"line{i}" for i in range(3200)]
    text = "\n".join(lines)
    chunks = chunk_code_by_lines(text)
    assert len(chunks) == 3  # 1500+1500+200
    # First chunk has first 1500 lines
    chunk1_lines = chunks[0].split("\n")
    assert len(chunk1_lines) == 1500
    assert chunk1_lines[0] == "line0"
    assert chunk1_lines[1499] == "line1499"
    # Second chunk has next 1500 lines
    chunk2_lines = chunks[1].split("\n")
    assert len(chunk2_lines) == 1500
    assert chunk2_lines[0] == "line1500"
