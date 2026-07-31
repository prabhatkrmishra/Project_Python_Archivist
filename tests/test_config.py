"""Tests for application configuration (Settings)."""

from __future__ import annotations

from pathlib import Path

import pytest

from archivist.config import Settings


class TestSettingsDefaults:
    def test_default_data_dir(self):
        assert Settings().data_dir == Path.home() / ".local" / "share" / "archivist"

    def test_default_api_settings(self, monkeypatch: pytest.MonkeyPatch):
        # Clear env vars so a local .env or exported var can't leak in.
        for name in ("ARCHIVIST_API_KEY", "ARCHIVIST_CORS_ORIGINS",
                     "ARCHIVIST_LOG_LEVEL", "ARCHIVIST_API_HOST",
                     "ARCHIVIST_API_PORT"):
            monkeypatch.delenv(name, raising=False)
        s = Settings()
        assert s.api_host == "0.0.0.0"
        assert s.api_port == 8000
        assert s.api_key is None
        assert s.cors_origins == "*"
        assert s.log_level == "INFO"

    def test_tracker_db_path(self, tmp_path: Path):
        s = Settings(data_dir=tmp_path)
        assert s.tracker_db == tmp_path / "ingested_files.db"

    def test_sqlite_db_path(self, tmp_path: Path):
        s = Settings(data_dir=tmp_path)
        assert s.sqlite_db == tmp_path / "archivist.db"

    def test_ensure_dirs_creates_nested_data_dir(self, tmp_path: Path):
        target = tmp_path / "nested" / "archivist"
        assert not target.exists()
        Settings(data_dir=target).ensure_dirs()
        assert target.is_dir()


class TestSettingsEnvOverride:
    def test_env_override_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARCHIVIST_API_KEY", "env-secret")
        assert Settings().api_key == "env-secret"

    def test_env_override_data_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("ARCHIVIST_DATA_DIR", str(tmp_path))
        assert Settings().data_dir == tmp_path

    def test_env_override_cors_origins(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(
            "ARCHIVIST_CORS_ORIGINS",
            "http://localhost:5173,http://localhost:3000",
        )
        assert Settings().cors_origins == "http://localhost:5173,http://localhost:3000"

    def test_env_override_int(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARCHIVIST_API_PORT", "9999")
        assert Settings().api_port == 9999

    def test_unknown_env_var_is_ignored(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARCHIVIST_DEFINITELY_NOT_A_SETTING", "x")
        Settings()  # must not raise
