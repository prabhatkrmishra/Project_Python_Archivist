"""Archive extraction utilities for zip, rar, and 7z files."""

from __future__ import annotations

import logging
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

# Supported archive extensions
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}

# Max files to extract from a single archive
_MAX_ARCHIVE_FILES = 5000


class ArchiveError(Exception):
    """Raised when archive extraction fails."""
    pass


def _is_safe_path(dest: Path, member_name: str) -> bool:
    """Check for zip-slip path traversal attacks.

    Args:
        dest: Destination directory.
        member_name: Archive member filename.

    Returns:
        True if the resolved path is within dest.
    """
    try:
        resolved = (dest / member_name).resolve()
        return str(resolved).startswith(str(dest.resolve()))
    except (ValueError, OSError):
        return False


def _extract_zip(data: bytes, dest: Path) -> list[Path]:
    """Extract a ZIP archive from bytes.

    Args:
        data: Raw ZIP file bytes.
        dest: Destination directory.

    Returns:
        List of extracted file paths.

    Raises:
        ArchiveError: If extraction fails.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"Invalid ZIP file: {e}") from e

    extracted = []
    members = zf.namelist()

    if len(members) > _MAX_ARCHIVE_FILES:
        raise ArchiveError(
            f"Archive contains {len(members)} files, max is {_MAX_ARCHIVE_FILES}"
        )

    for member in members:
        if member.endswith("/"):
            continue
        if not _is_safe_path(dest, member):
            raise ArchiveError(f"Path traversal detected in archive: {member}")

        zf.extract(member, dest)
        extracted.append(dest / member)

    zf.close()
    return extracted


def _extract_7z(data: bytes, dest: Path) -> list[Path]:
    """Extract a 7z archive from bytes.

    Args:
        data: Raw 7z file bytes.
        dest: Destination directory.

    Returns:
        List of extracted file paths.

    Raises:
        ArchiveError: If extraction fails.
    """
    try:
        import py7zr
    except ImportError:
        raise ArchiveError("7z support requires py7zr: pip install py7zr")

    # Write to temp file since py7zr needs a file path
    tmp = dest / "__tmp__.7z"
    try:
        tmp.write_bytes(data)
        archive = py7zr.SevenZipFile(str(tmp), mode="r")
        members = archive.getnames()

        if len(members) > _MAX_ARCHIVE_FILES:
            raise ArchiveError(
                f"Archive contains {len(members)} files, max is {_MAX_ARCHIVE_FILES}"
            )

        for member in members:
            if not _is_safe_path(dest, member):
                raise ArchiveError(f"Path traversal detected in archive: {member}")

        archive.extractall(path=str(dest))
        archive.close()

        extracted = []
        for member in members:
            p = dest / member
            if p.is_file():
                extracted.append(p)
        return extracted

    finally:
        tmp.unlink(missing_ok=True)


def _extract_rar(data: bytes, dest: Path) -> list[Path]:
    """Extract a RAR archive from bytes.

    Args:
        data: Raw RAR file bytes.
        dest: Destination directory.

    Returns:
        List of extracted file paths.

    Raises:
        ArchiveError: If extraction fails or unrar is not available.
    """
    try:
        import rarfile
    except ImportError:
        raise ArchiveError("RAR support requires rarfile: pip install rarfile")

    # Check if unrar binary is available
    try:
        unrar_tool = rarfile.UNRAR_TOOL
        if not unrar_tool:
            raise ArchiveError("unrar binary not found")
    except Exception:
        raise ArchiveError(
            "RAR support requires unrar binary. "
            "Install from http://www.rarlab.com/rar_add.htm or skip RAR files."
        )

    # Write to temp file since rarfile needs a file path
    tmp = dest / "__tmp__.rar"
    try:
        tmp.write_bytes(data)
        rf = rarfile.RarFile(str(tmp))

        members = [m for m in rf.namelist() if not m.endswith("/")]

        if len(members) > _MAX_ARCHIVE_FILES:
            raise ArchiveError(
                f"Archive contains {len(members)} files, max is {_MAX_ARCHIVE_FILES}"
            )

        for member in members:
            if not _is_safe_path(dest, member):
                raise ArchiveError(f"Path traversal detected in archive: {member}")

        rf.extractall(dest)
        rf.close()

        extracted = []
        for member in members:
            p = dest / member
            if p.is_file():
                extracted.append(p)
        return extracted

    except ArchiveError:
        raise
    except Exception as e:
        raise ArchiveError(f"RAR extraction failed: {e}") from e
    finally:
        tmp.unlink(missing_ok=True)


def extract_archive(file_bytes: bytes, filename: str, dest: Path | None = None) -> list[Path]:
    """Detect archive format and extract to a directory.

    Args:
        file_bytes: Raw file bytes.
        filename: Original filename (used to detect format).
        dest: Destination directory. If None, creates a temp directory.

    Returns:
        Tuple of (extracted_files, dest_dir).
        Caller is responsible for cleanup of dest_dir.

    Raises:
        ArchiveError: If format is unsupported or extraction fails.
    """
    suffix = Path(filename).suffix.lower()

    if suffix not in ARCHIVE_EXTENSIONS:
        raise ArchiveError(
            f"Unsupported archive format: {suffix}. "
            f"Supported: {', '.join(sorted(ARCHIVE_EXTENSIONS))}"
        )

    if dest is None:
        dest = Path(tempfile.mkdtemp(prefix="archivist_archive_"))
    dest.mkdir(parents=True, exist_ok=True)

    if suffix == ".zip":
        return _extract_zip(file_bytes, dest)
    elif suffix == ".7z":
        return _extract_7z(file_bytes, dest)
    elif suffix == ".rar":
        return _extract_rar(file_bytes, dest)
    else:
        raise ArchiveError(f"Unsupported archive format: {suffix}")


def is_archive(filename: str) -> bool:
    """Check if a filename is a supported archive format.

    Args:
        filename: Filename to check.

    Returns:
        True if the file is a supported archive.
    """
    return Path(filename).suffix.lower() in ARCHIVE_EXTENSIONS
