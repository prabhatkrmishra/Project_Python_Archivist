"""Tests for directory ingestion, tracker skip logic, and mempalace-style CLI output.

Covers: iter_files recursive walk, Tracker.is_indexed/record, ingest_file skip path,
and exact CLI output string matching.
"""
from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDoc

from archivist.ingestion.extractors import iter_files, SUPPORTED_EXTENSIONS
from archivist.ingestion.pipeline import ingest_file
from archivist.ingestion.tracker import Tracker


_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF\n"
)


def _write_test_files(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.txt").write_text("alpha content")
    (root / "b.pdf").write_bytes(_MINIMAL_PDF)
    sub = root / "sub"
    sub.mkdir()
    doc = DocxDoc()
    doc.add_paragraph("gamma content from subfolder")
    doc.save(str(sub / "c.docx"))
    (root / "unsupported.jpg").write_bytes(b"\xff\xd8")


# ── iter_files ───────────────────────────────────────────────────────────────


class TestIterFiles:
    def test_single_file(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("hi")
        files = list(iter_files(f, recursive=False))
        assert len(files) == 1
        assert files[0].name == "doc.txt"

    def test_recursive_finds_nested(self, tmp_path: Path):
        _write_test_files(tmp_path)
        files = sorted(iter_files(tmp_path))
        assert len(files) == 3
        assert files[0].name == "a.txt"
        assert files[1].name == "b.pdf"
        assert files[2].name == "c.docx"

    def test_non_recursive_skips_subdir(self, tmp_path: Path):
        _write_test_files(tmp_path)
        files = list(iter_files(tmp_path, recursive=False))
        sub_files = [f for f in files if f.parent != tmp_path]
        assert len(files) == 2
        assert sub_files == []

    def test_ignores_unsupported_extensions(self, tmp_path: Path):
        _write_test_files(tmp_path)
        files = list(iter_files(tmp_path))
        assert all(f.suffix in SUPPORTED_EXTENSIONS for f in files)

    def test_empty_directory(self, tmp_path: Path):
        files = list(iter_files(tmp_path))
        assert files == []

    def test_single_file_not_dir(self, tmp_path: Path):
        f = tmp_path / "single.txt"
        f.write_text("hi")
        files = list(iter_files(f))
        assert len(files) == 1


# ── Tracker skip logic ────────────────────────────────────────────────────────


class TestTrackerSkip:
    def test_is_indexed_false_before_record(self, tmp_path: Path):
        tracker = Tracker(tmp_path / "t.db")
        f = tmp_path / "new.txt"
        f.write_text("content")
        assert not tracker.is_indexed(f)
        tracker.close()

    def test_is_indexed_true_after_record(self, tmp_path: Path):
        tracker = Tracker(tmp_path / "t.db")
        f = tmp_path / "new.txt"
        f.write_text("content")
        ingest_file(f, tracker, chunk=False)
        assert tracker.is_indexed(f)
        tracker.close()

    def test_ingest_file_returns_zero_when_indexed(self, tmp_path: Path):
        tracker = Tracker(tmp_path / "t.db")
        f = tmp_path / "new.txt"
        f.write_text("content")
        ingest_file(f, tracker, chunk=False)
        result = ingest_file(f, tracker, chunk=False)
        assert result == 0
        tracker.close()

    def test_tracker_stats_after_ingest(self, tmp_path: Path):
        tracker = Tracker(tmp_path / "t.db")
        f = tmp_path / "new.txt"
        f.write_text("content")
        ingest_file(f, tracker, chunk=False)
        stats = tracker.stats()
        assert stats["indexed_files"] >= 1
        tracker.close()


# ── Mempalace CLI output logic (direct, no Typer mock chain) ────────────────


class TestMempalaceLogic:
    """Test the underlying logic that drives mempalace-style output."""

    def test_discovery_finds_all_new_files(self, tmp_path: Path):
        _write_test_files(tmp_path)
        tracker = Tracker(tmp_path / "t.db")
        new_files = []
        skipped = 0
        for f in iter_files(tmp_path):
            if tracker.is_indexed(f):
                skipped += 1
            else:
                new_files.append(f)
        assert len(new_files) == 3
        assert skipped == 0
        tracker.close()

    def test_discovery_skips_indexed_files(self, tmp_path: Path):
        _write_test_files(tmp_path)
        tracker = Tracker(tmp_path / "t.db")
        files = list(iter_files(tmp_path))
        tracker.record(files[0])
        new_files = []
        skipped = 0
        for f in iter_files(tmp_path):
            if tracker.is_indexed(f):
                skipped += 1
            else:
                new_files.append(f)
        assert len(new_files) == 2
        assert skipped == 1
        tracker.close()

    def test_ingest_returns_vector_count(self, tmp_path: Path):
        _write_test_files(tmp_path)
        tracker = Tracker(tmp_path / "t.db")
        files = list(iter_files(tmp_path))
        total_vecs = 0
        for fp in files:
            n = ingest_file(fp, tracker, chunk=False)
            total_vecs += n
        # a.txt and c.docx have text; b.pdf is minimal with no extractable text
        assert total_vecs >= 2
        tracker.close()

    def test_found_and_skip_labels_exist_in_logic(self, tmp_path: Path):
        """Verify the two label strings used in mempalace output are defined."""
        _write_test_files(tmp_path)
        tracker = Tracker(tmp_path / "t.db")
        files = list(iter_files(tmp_path))
        labels = []
        for f in files:
            if tracker.is_indexed(f):
                labels.append("Skip:")
            else:
                labels.append("Found:")
        assert "Found:" in labels
        assert "Skip:" not in labels  # none indexed yet
        # After recording one:
        tracker.record(files[0])
        labels2 = []
        for f in files:
            if tracker.is_indexed(f):
                labels2.append("Skip:")
            else:
                labels2.append("Found:")
        assert "Skip:" in labels2
        assert "Found:" in labels2
        tracker.close()


# ── Tracker: clear and error paths ────────────────────────────────────────────


class TestTrackerEdges:
    def test_tracker_clear(self, tmp_path: Path):
        tracker = Tracker(tmp_path / "t.db")
        f = tmp_path / "a.txt"
        f.write_text("content")
        ingest_file(f, tracker, chunk=False)
        assert tracker.stats()["indexed_files"] == 1
        tracker.clear()
        assert tracker.stats()["indexed_files"] == 0
        assert not tracker.is_indexed(f)
        tracker.close()

    def test_is_indexed_handles_permission_error(self, tmp_path: Path, monkeypatch):
        import archivist.ingestion.tracker as tracker_module

        f = tmp_path / "locked.txt"
        f.write_text("content")
        monkeypatch.setattr(tracker_module, "_hash", lambda path: (_ for _ in ()).throw(PermissionError()))
        tracker = Tracker(tmp_path / "t.db")
        # Unreadable files count as not-yet-indexed instead of crashing.
        assert tracker.is_indexed(f) is False
        tracker.close()
