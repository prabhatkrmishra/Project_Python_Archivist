"""File extraction and text processing for Archivist.

Supports:
- Plain text and code files (.txt, .py, .js, .ts, .java, .c, .cpp, etc.)
- PDF documents (.pdf) via pypdf
- Word documents (.docx) via python-docx

All extracted text is normalized for consistent vectorization.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterator

import pypdf
from docx import Document


# Supported file extensions for ingestion
SUPPORTED_EXTENSIONS = {
    # Plain text / code
    ".txt", ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
    ".cs", ".rb", ".go", ".rs", ".php", ".html", ".htm", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".conf",
    ".md", ".rst", ".csv", ".tsv", ".sql", ".sh", ".bash", ".zsh", ".bat",
    ".ps1", ".dockerfile", ".makefile", ".gitignore", ".env", ".log",
    # Documents
    ".pdf", ".docx",
}

# Chunking thresholds
_CHUNK_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB
_CHUNK_THRESHOLD_PAGES = 100


class UnsupportedFileType(Exception):
    """Raised when attempting to extract text from an unsupported file type."""
    pass


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hash of file contents.

    Args:
        path: Path to file to hash.

    Returns:
        Hex digest string of the file's SHA-256 hash.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_text(text: str) -> str:
    """Normalize text for vectorization.

    Processes text by:
    1. Converting to lowercase
    2. Stripping control characters (except newlines)
    3. Collapsing all whitespace (including newlines) to single spaces

    Args:
        text: Raw text to normalize.

    Returns:
        Normalized text string.
    """
    text = text.lower()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_for_display(text: str) -> str:
    """Normalize text for display while preserving line structure and case.

    Strips control characters, collapses multiple spaces to one within lines,
    preserves newlines and leading indentation for line-numbered snippet display.

    Args:
        text: Raw text to normalize.

    Returns:
        Normalized text with preserved line breaks, indentation, and case.
    """
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = text.expandtabs(4)
    # Process each line: preserve leading spaces, collapse internal spaces
    lines = text.split("\n")
    result = []
    for line in lines:
        # Match leading whitespace and the rest
        m = re.match(r"^(\s*)(.*)", line)
        if m:
            leading = m.group(1)
            rest = re.sub(r" {2,}", " ", m.group(2))
            result.append(leading + rest)
    text = "\n".join(result)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_txt(path: Path) -> str:
    """Extract text from a plain text or code file.

    Args:
        path: Path to text file.

    Returns:
        File contents as string.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def extract_pdf(path: Path) -> str:
    """Extract text from a PDF document.

    Args:
        path: Path to .pdf file.

    Returns:
        Concatenated text from all pages.
    """
    reader = pypdf.PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def extract_docx(path: Path) -> str:
    """Extract text from a Word document.

    Args:
        path: Path to .docx file.

    Returns:
        Concatenated text from all paragraphs.
    """
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text)


def extract_text(path: Path) -> str:
    """Extract text from a file based on its extension.

    Args:
        path: Path to file.

    Returns:
        Extracted text content.

    Raises:
        UnsupportedFileType: If file extension is not supported.
    """
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_pdf(path)
    if ext == ".docx":
        return extract_docx(path)
    # All text and code files — read as plain text
    if ext in SUPPORTED_EXTENSIONS:
        return extract_txt(path)
    raise UnsupportedFileType(f"Unsupported file type: {ext}")


def should_chunk(path: Path, text: str) -> bool:
    """Determine if a document should be split into chunks.

    Args:
        path: Path to file.
        text: Extracted text content.

    Returns:
        True if document exceeds size or page thresholds.
    """
    size = path.stat().st_size
    if size > _CHUNK_THRESHOLD_BYTES:
        return True
    if path.suffix.lower() == ".pdf":
        try:
            page_count = len(pypdf.PdfReader(str(path)).pages)
            if page_count > _CHUNK_THRESHOLD_PAGES:
                return True
        except Exception:
            pass
    return False


def chunk_pdf_by_page(path: Path) -> list[str]:
    """Split PDF into per-page text chunks.

    Args:
        path: Path to PDF file.

    Returns:
        List of text strings, one per page.
    """
    reader = pypdf.PdfReader(str(path))
    return [p.extract_text() or "" for p in reader.pages]


def chunk_docx_by_section(path: Path) -> list[str]:
    """Split DOCX by heading-level paragraphs.

    Args:
        path: Path to DOCX file.

    Returns:
        List of text chunks split at heading boundaries.
    """
    doc = Document(str(path))
    chunks: list[str] = []
    current: list[str] = []
    for para in doc.paragraphs:
        if para.style.name.startswith("Heading") and current:
            chunks.append("\n".join(current))
            current = []
        current.append(para.text)
    if current:
        chunks.append("\n".join(current))
    return chunks if chunks else ["\n".join(p.text for p in doc.paragraphs if p.text)]


def chunk_text(path: Path, text: str) -> list[str]:
    """Chunk large documents by page (PDF) or section (DOCX).

    Args:
        path: Path to file.
        text: Extracted text content.

    Returns:
        List of text chunks.
    """
    ext = path.suffix.lower()
    if ext == ".pdf":
        return chunk_pdf_by_page(path)
    if ext == ".docx":
        return chunk_docx_by_section(path)
    return [text]


def iter_files(root: Path, recursive: bool = True) -> Iterator[Path]:
    """Yield supported files from a directory.

    Args:
        root: Root directory or file to scan.
        recursive: If True, recurse into subdirectories.

    Yields:
        Path objects for supported files.
    """
    if root.is_file():
        if _is_supported(root):
            yield root
        return
    pattern = "**/*" if recursive else "*"
    for p in root.glob(pattern):
        if p.is_file() and _is_supported(p):
            yield p


def _is_supported(path: Path) -> bool:
    """Check if a file is supported for ingestion.

    Matches by extension or by known extensionless filenames.

    Args:
        path: Path to check.

    Returns:
        True if file can be ingested.
    """
    ext = path.suffix.lower()
    if ext in SUPPORTED_EXTENSIONS:
        return True
    # Extensionless files like Makefile, Dockerfile, .gitignore
    if path.name.lower() in {"makefile", "dockerfile", "procfile"}:
        return True
    return False
