"""Archive extraction utilities for zip, rar, and 7z files."""

from __future__ import annotations

import logging
import tempfile
import zipfile
import zlib
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

# Supported archive extensions
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}

# Magic-byte signatures for each format. Extensions are just filenames and
# can be renamed/spoofed trivially (e.g. a plain text file saved as
# "notes.zip"), so before we ever hand file bytes to zipfile/py7zr/rarfile
# we verify the actual content starts with a valid header for the format
# it claims to be. This is a pure-Python check - no extra dependency.
_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".zip": (
        b"PK\x03\x04",  # normal archive with at least one entry
        b"PK\x05\x06",  # empty archive (end-of-central-directory only)
        b"PK\x07\x08",  # spanned/multi-volume archive marker
    ),
    ".7z": (
        b"7z\xbc\xaf\x27\x1c",  # 6-byte 7z signature
    ),
    ".rar": (
        b"Rar!\x1a\x07\x00",  # RAR 1.5 - 4.x
        b"Rar!\x1a\x07\x01\x00",  # RAR 5.0+
    ),
}

# Max files to extract from a single archive
_MAX_ARCHIVE_FILES = 5000


class ArchiveError(Exception):
    """Raised when archive extraction fails."""
    pass


def _validate_signature(file_bytes: bytes, suffix: str) -> None:
    """Verify the file's magic bytes actually match its claimed format.

    A file extension is just a name and proves nothing about the content -
    this checks the real header bytes so a mislabeled or corrupted file is
    rejected up front, before any extraction library touches it.

    Args:
        file_bytes: Raw file bytes as uploaded.
        suffix: Lowercased file extension, e.g. ".zip".

    Raises:
        ArchiveError: If the content's signature doesn't match any valid
            header for the claimed format (or the file is empty/truncated).
    """
    signatures = _MAGIC_SIGNATURES.get(suffix)
    if not signatures:
        return

    if not file_bytes:
        raise ArchiveError(f"File is empty - not a valid {suffix} archive.")

    if not any(file_bytes.startswith(sig) for sig in signatures):
        raise ArchiveError(
            f"File has a {suffix} extension but its contents don't match a "
            f"valid {suffix} archive header. It may be corrupted, "
            f"truncated, or not actually a {suffix} file."
        )


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


def analyze_archive(file_bytes: bytes, filename: str) -> dict:
    """Validate an archive and summarize its contents without extracting to disk.

    This is the real safety check behind the "Analyzing archive..." step in
    the UI: it verifies the magic-byte signature, confirms the archive
    actually opens and its internal structure is intact (catching corrupt
    or truncated files that pass the signature check but are still broken),
    and reports how many files it contains - all before the user commits to
    a full extraction/ingest.

    Args:
        file_bytes: Raw file bytes as uploaded.
        filename: Original filename (used to detect format).

    Returns:
        Dict with:
            valid (bool): whether the archive is safe to ingest.
            format (str): detected extension, e.g. ".zip".
            file_count (int): number of files inside (0 if invalid).
            total_size (int): total uncompressed size in bytes (0 if invalid).
            error (str | None): human-readable reason if invalid.
    """
    suffix = Path(filename).suffix.lower()

    if suffix not in ARCHIVE_EXTENSIONS:
        return {
            "valid": False,
            "format": suffix,
            "file_count": 0,
            "total_size": 0,
            "error": (
                f"Unsupported archive format: {suffix}. "
                f"Supported: {', '.join(sorted(ARCHIVE_EXTENSIONS))}"
            ),
        }

    try:
        _validate_signature(file_bytes, suffix)
    except ArchiveError as e:
        return {
            "valid": False,
            "format": suffix,
            "file_count": 0,
            "total_size": 0,
            "error": str(e),
        }

    try:
        if suffix == ".zip":
            file_count, total_size = _analyze_zip(file_bytes)
        elif suffix == ".7z":
            file_count, total_size = _analyze_7z(file_bytes)
        else:  # ".rar"
            file_count, total_size = _analyze_rar(file_bytes)
    except ArchiveError as e:
        return {
            "valid": False,
            "format": suffix,
            "file_count": 0,
            "total_size": 0,
            "error": str(e),
        }

    if file_count > _MAX_ARCHIVE_FILES:
        return {
            "valid": False,
            "format": suffix,
            "file_count": file_count,
            "total_size": total_size,
            "error": f"Archive contains {file_count} files, max is {_MAX_ARCHIVE_FILES}",
        }

    if file_count == 0:
        return {
            "valid": False,
            "format": suffix,
            "file_count": 0,
            "total_size": 0,
            "error": "Archive is valid but contains no files.",
        }

    return {
        "valid": True,
        "format": suffix,
        "file_count": file_count,
        "total_size": total_size,
        "error": None,
    }


def _analyze_zip(file_bytes: bytes) -> tuple[int, int]:
    """Open a zip in-memory, verify integrity, and count/size its members."""
    try:
        zf = zipfile.ZipFile(BytesIO(file_bytes))
    except zipfile.BadZipFile as e:
        raise ArchiveError(f"Invalid ZIP file: {e}") from e

    bad_member = None
    try:
        bad_member = zf.testzip()
    except (zipfile.BadZipFile, zlib.error) as e:
        zf.close()
        raise ArchiveError(f"Invalid or corrupt ZIP file: {e}") from e
    if bad_member is not None:
        zf.close()
        raise ArchiveError(f"Corrupt file inside archive: {bad_member}")

    members = [m for m in zf.infolist() if not m.filename.endswith("/")]
    file_count = len(members)
    total_size = sum(m.file_size for m in members)
    zf.close()
    return file_count, total_size


def _analyze_7z(file_bytes: bytes) -> tuple[int, int]:
    """Open a 7z in a scratch temp file, verify integrity, count/size members."""
    try:
        import py7zr
    except ImportError:
        raise ArchiveError("7z support requires py7zr: pip install py7zr")

    with tempfile.NamedTemporaryFile(suffix=".7z", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        try:
            archive = py7zr.SevenZipFile(tmp_path, mode="r")
        except Exception as e:
            # py7zr raises Bad7zFile for well-formed-but-invalid headers, but
            # truncated garbage can surface as struct.error instead - both mean
            # "this is not a readable 7z archive" and both must map to a clean
            # ArchiveError rather than escaping as an internal traceback.
            raise ArchiveError(f"Invalid 7z file: {e}") from e

        if archive.testzip() is not None:
            archive.close()
            raise ArchiveError("Corrupt file inside archive")

        infos = [i for i in archive.list() if not i.is_directory]
        file_count = len(infos)
        total_size = sum(i.uncompressed for i in infos)
        archive.close()
        return file_count, total_size
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _analyze_rar(file_bytes: bytes) -> tuple[int, int]:
    """Open a rar in a scratch temp file, verify integrity, count/size members."""
    try:
        import rarfile
    except ImportError:
        raise ArchiveError("RAR support requires rarfile: pip install rarfile")

    try:
        unrar_tool = rarfile.UNRAR_TOOL
        if not unrar_tool:
            raise ArchiveError("unrar binary not found")
    except ArchiveError:
        raise
    except Exception:
        raise ArchiveError(
            "RAR support requires unrar binary. "
            "Install from http://www.rarlab.com/rar_add.htm or skip RAR files."
        )

    with tempfile.NamedTemporaryFile(suffix=".rar", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        try:
            rf = rarfile.RarFile(tmp_path)
        except rarfile.Error as e:
            raise ArchiveError(f"Invalid RAR file: {e}") from e

        try:
            bad_member = rf.testrar()
        except rarfile.Error as e:
            # rarfile 4.x raises BadRarFile for a corrupt member instead of
            # returning its name. Convert it so the API can report a clean
            # invalid result rather than an unhandled 500.
            rf.close()
            raise ArchiveError(f"Corrupt file inside archive: {e}") from e

        if bad_member is not None:
            rf.close()
            raise ArchiveError(f"Corrupt file inside archive: {bad_member}")

        infos = [i for i in rf.infolist() if not i.is_dir()]
        file_count = len(infos)
        total_size = sum(i.file_size for i in infos)
        rf.close()
        return file_count, total_size
    finally:
        Path(tmp_path).unlink(missing_ok=True)


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
            # Close before raising: on Windows the open file handle would
            # otherwise make the finally-block unlink fail with WinError 32.
            archive.close()
            raise ArchiveError(
                f"Archive contains {len(members)} files, max is {_MAX_ARCHIVE_FILES}"
            )

        for member in members:
            if not _is_safe_path(dest, member):
                archive.close()
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
            # Close before raising: an open rarfile handle would make the
            # finally-block temp unlink fail on Windows.
            rf.close()
            raise ArchiveError(
                f"Archive contains {len(members)} files, max is {_MAX_ARCHIVE_FILES}"
            )

        for member in members:
            if not _is_safe_path(dest, member):
                rf.close()
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

    _validate_signature(file_bytes, suffix)

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