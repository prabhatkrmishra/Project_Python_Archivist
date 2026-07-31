"""Tests for the Typer CLI commands (ingest, search, status, delete, clear)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from archivist.cli import app
from archivist.config import Settings


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _test_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the CLI's module-level settings at a temp data dir."""
    test_settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr("archivist.cli.settings", test_settings)
    return test_settings


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    """A directory with a few ingestable files, separate from the data dir."""
    src = tmp_path / "docs"
    src.mkdir()
    (src / "a.txt").write_text("quarterly budget report 2024")
    (src / "b.txt").write_text("project alpha milestones and timeline")
    (src / "notes.md").write_text("# ShadowTracker\ndebugging notes for the tracker")
    return src


def _invoke_json(runner: CliRunner, args: list[str]) -> dict:
    """Run a CLI command expecting JSON on stdout and return the parsed dict."""
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# ── ingest ─────────────────────────────────────────────────────────────────────


class TestIngestCommand:
    def test_ingest_directory(self, runner: CliRunner, docs_dir: Path):
        data = _invoke_json(runner, ["ingest", str(docs_dir), "--json"])
        assert data["path"] == str(docs_dir.resolve())
        assert data["total_new"] == 3
        assert data["ingested"] == 3
        assert data["errors"] == 0
        assert data["total_chunks"] >= 3
        assert all(f["status"] == "ok" for f in data["files"])

    def test_ingest_is_idempotent(self, runner: CliRunner, docs_dir: Path):
        first = _invoke_json(runner, ["ingest", str(docs_dir), "--json"])
        assert first["ingested"] == 3

        second = _invoke_json(runner, ["ingest", str(docs_dir), "--json"])
        assert second["ingested"] == 0
        assert second["skipped"] == 3

    def test_ingest_missing_path(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(app, ["ingest", str(tmp_path / "nope"), "--json"])
        assert result.exit_code == 1
        assert json.loads(result.output)["error"]

    def test_ingest_corrupt_file_reports_error(self, runner: CliRunner, tmp_path: Path):
        src = tmp_path / "bad"
        src.mkdir()
        # .pdf is a supported extension, but garbage bytes fail extraction.
        (src / "broken.pdf").write_bytes(b"%PDF-1.4 garbage that is not a real pdf")
        data = _invoke_json(runner, ["ingest", str(src), "--json"])
        assert data["ingested"] == 0
        assert data["errors"] == 1
        assert data["files"][0]["status"] == "error"

    # ── non-JSON output ────────────────────────────────────────────────────────

    def test_ingest_non_json_progress(self, runner: CliRunner, docs_dir: Path):
        result = runner.invoke(app, ["ingest", str(docs_dir)])
        assert result.exit_code == 0
        assert "Ingesting:" in result.output
        assert "Ingesting 3 files (0 already indexed)" in result.output
        for name in ("a.txt", "b.txt", "notes.md"):
            assert f"{name} ->" in result.output
        assert "Ingestion complete: 3 files" in result.output

    def test_ingest_non_json_already_indexed(self, runner: CliRunner, docs_dir: Path):
        _invoke_json(runner, ["ingest", str(docs_dir), "--json"])
        result = runner.invoke(app, ["ingest", str(docs_dir)])
        assert result.exit_code == 0
        assert "Ingesting 0 files (3 already indexed)" in result.output

    def test_ingest_missing_path_non_json(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(app, ["ingest", str(tmp_path / "nope")])
        assert result.exit_code == 1
        assert "Path not found" in result.output

    def test_ingest_permission_error_skips_file(
        self, runner: CliRunner, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import archivist.cli as cli

        class LockedTracker:
            def __init__(self, *args, **kwargs):
                pass

            def is_indexed(self, path):
                raise PermissionError("denied")

            def record(self, *args, **kwargs):
                pass

            def close(self):
                pass

        monkeypatch.setattr(cli, "Tracker", LockedTracker)
        result = runner.invoke(app, ["ingest", str(docs_dir)])
        assert result.exit_code == 0
        assert "3 already indexed" in result.output

    def test_ingest_non_json_reports_errors(
        self, runner: CliRunner, docs_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import archivist.cli as cli

        def boom(filepath, tracker):
            raise RuntimeError("boom")

        monkeypatch.setattr(cli, "_ingest_sqlite", boom)
        result = runner.invoke(app, ["ingest", str(docs_dir)])
        assert result.exit_code == 0
        assert "ERR boom" in result.output
        assert "3 errors" in result.output

    def test_ingest_missing_path_json_error(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(app, ["ingest", str(tmp_path / "nope"), "--json"])
        assert result.exit_code == 1
        assert json.loads(result.output)["error"]


# ── _ingest_sqlite internals ──────────────────────────────────────────────────


class TestIngestSqlite:
    def test_already_indexed_returns_zero(self, runner: CliRunner, docs_dir: Path):
        import archivist.cli as cli
        from archivist.ingestion.tracker import Tracker

        _invoke_json(runner, ["ingest", str(docs_dir), "--json"])

        tracker = Tracker(cli.settings.tracker_db)
        try:
            assert cli._ingest_sqlite(docs_dir / "a.txt", tracker) == 0
        finally:
            tracker.close()

    def test_empty_text_records_and_returns_zero(
        self, runner: CliRunner, tmp_path: Path
    ):
        import archivist.cli as cli
        from archivist.ingestion.tracker import Tracker

        src = tmp_path / "blank"
        src.mkdir()
        (src / "blank.txt").write_text("   \n\t\n  ")

        tracker = Tracker(cli.settings.tracker_db)
        try:
            assert cli._ingest_sqlite(src / "blank.txt", tracker) == 0
            assert tracker.stats()["indexed_files"] == 1
        finally:
            tracker.close()

    def test_chunked_file_splits_into_multiple_chunks(
        self, runner: CliRunner, tmp_path: Path
    ):
        import archivist.cli as cli
        from archivist.ingestion.tracker import Tracker

        src = tmp_path / "bigcode"
        src.mkdir()
        # Code chunks split at 1500 lines, so 1600 lines -> 2 chunks.
        lines = "\n".join(f"def func_{i}(): pass" for i in range(1600))
        (src / "big.py").write_text(lines)

        tracker = Tracker(cli.settings.tracker_db)
        try:
            n = cli._ingest_sqlite(src / "big.py", tracker)
        finally:
            tracker.close()
        assert n == 2


# ── search ─────────────────────────────────────────────────────────────────────


class TestSearchCommand:
    @pytest.fixture(autouse=True)
    def _ingested(self, runner: CliRunner, docs_dir: Path):
        _invoke_json(runner, ["ingest", str(docs_dir), "--json"])

    def test_search_finds_match(self, runner: CliRunner):
        data = _invoke_json(runner, ["search", "quarterly", "--json"])
        assert data["total"] >= 1
        assert data["results"][0]["source"] == "a.txt"
        assert data["results"][0]["score"] > 0

    def test_search_no_match(self, runner: CliRunner):
        data = _invoke_json(runner, ["search", "zzz_no_match", "--json"])
        assert data["total"] == 0
        assert data["results"] == []

    def test_search_limit(self, runner: CliRunner):
        # Only notes.md matches "ShadowTracker", so --top 2 returns that one.
        data = _invoke_json(runner, ["search", "ShadowTracker", "--json", "--top", "2"])
        assert len(data["results"]) == 1
        assert data["results"][0]["source"] == "notes.md"

    def test_search_all_chunks(self, runner: CliRunner):
        data = _invoke_json(runner, ["search", "project", "--json", "--all"])
        assert data["all_chunks"] is True

    # ── non-JSON output ────────────────────────────────────────────────────────

    def test_search_non_json_no_results(self, runner: CliRunner):
        result = runner.invoke(app, ["search", "zzz_no_match"])
        assert result.exit_code == 0
        assert "No results found." in result.output

    def test_search_non_json_prints_hits(self, runner: CliRunner):
        result = runner.invoke(app, ["search", "quarterly"])
        assert result.exit_code == 0
        assert "a.txt" in result.output
        assert "score=" in result.output
        # The rich snippet for a matching doc is printed too.
        assert "quarterly" in result.output


# ── status ─────────────────────────────────────────────────────────────────────


class TestStatusCommand:
    def test_status_empty(self, runner: CliRunner):
        data = _invoke_json(runner, ["status", "--json"])
        assert data["points_count"] == 0
        assert data["backend"] == "sqlite-fts5"
        assert data["tracker_files"] == 0

    def test_status_after_ingest(self, runner: CliRunner, docs_dir: Path):
        _invoke_json(runner, ["ingest", str(docs_dir), "--json"])
        data = _invoke_json(runner, ["status", "--json"])
        assert data["points_count"] >= 3
        assert data["tracker_files"] == 3

    def test_status_non_json(self, runner: CliRunner, docs_dir: Path):
        _invoke_json(runner, ["ingest", str(docs_dir), "--json"])
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Index status:" in result.output
        assert "backend: sqlite-fts5" in result.output
        assert "tracker_files: 3" in result.output


# ── delete ─────────────────────────────────────────────────────────────────────


class TestDeleteCommand:
    def test_delete_removes_document(self, runner: CliRunner, docs_dir: Path):
        _invoke_json(runner, ["ingest", str(docs_dir), "--json"])
        found = _invoke_json(runner, ["search", "quarterly", "--json"])
        doc_id = found["results"][0]["doc_id"]
        assert doc_id

        result = runner.invoke(app, ["delete", doc_id])
        assert result.exit_code == 0

        after = _invoke_json(runner, ["search", "quarterly", "--json"])
        assert after["total"] == 0


# ── clear ─────────────────────────────────────────────────────────────────────


class TestClearCommand:
    def test_clear_aborts_without_confirm(self, runner: CliRunner):
        result = runner.invoke(app, ["clear"])
        assert result.exit_code != 0

    def test_clear_aborts_when_prompt_declined(self, runner: CliRunner):
        # Typer does not emit --no-confirm when a prompt is attached to the
        # option; declining happens by answering 'n' (or Ctrl+C) at the prompt.
        result = runner.invoke(app, ["clear"], input="n\n")
        assert result.exit_code != 0

    def test_clear_removes_databases(self, runner: CliRunner, docs_dir: Path):
        _invoke_json(runner, ["ingest", str(docs_dir), "--json"])

        result = runner.invoke(app, ["clear", "--confirm"])
        assert result.exit_code == 0

        import archivist.cli as cli
        assert not cli.settings.sqlite_db.exists()
        assert not cli.settings.tracker_db.exists()


# ── _fmt_duration ─────────────────────────────────────────────────────────────


class TestFmtDuration:
    def test_seconds_only(self):
        import archivist.cli as cli

        assert cli._fmt_duration(45) == "45s"
        assert cli._fmt_duration(0) == "0s"

    def test_minutes_and_seconds(self):
        import archivist.cli as cli

        assert cli._fmt_duration(150) == "2m 30s"

    def test_hours_minutes_seconds(self):
        import archivist.cli as cli

        assert cli._fmt_duration(3700) == "1h 1m 40s"
