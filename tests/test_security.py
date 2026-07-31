"""Tests for app middleware: request ids, CORS, and logging setup."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from archivist.main import _cors_origins, configure_logging, create_app


class TestCorsOriginsParsing:
    def test_wildcard(self):
        assert _cors_origins("*") == ["*"]

    def test_single_origin(self):
        assert _cors_origins("http://localhost:5173") == ["http://localhost:5173"]

    def test_comma_separated_trims_whitespace(self):
        assert _cors_origins("http://a.com, http://b.com") == [
            "http://a.com",
            "http://b.com",
        ]

    def test_empty_string(self):
        assert _cors_origins("") == []

    def test_only_separators(self):
        assert _cors_origins(" , ") == []


class TestRequestIdMiddleware:
    def test_response_includes_request_id(self):
        with TestClient(create_app()) as client:
            r = client.get("/health")
            assert "x-request-id" in r.headers
            assert len(r.headers["x-request-id"]) == 12

    def test_incoming_request_id_is_echoed(self):
        with TestClient(create_app()) as client:
            r = client.get("/health", headers={"X-Request-ID": "correlation-123"})
            assert r.headers["x-request-id"] == "correlation-123"

    def test_request_ids_differ_across_requests(self):
        with TestClient(create_app()) as client:
            first = client.get("/health").headers["x-request-id"]
            second = client.get("/health").headers["x-request-id"]
            assert first != second


class TestCorsMiddleware:
    def test_allowed_origin_gets_cors_headers(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARCHIVIST_CORS_ORIGINS", "http://localhost:5173")
        with TestClient(create_app()) as client:
            r = client.get("/health", headers={"Origin": "http://localhost:5173"})
            assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_disallowed_origin_gets_no_cors_headers(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARCHIVIST_CORS_ORIGINS", "http://localhost:5173")
        with TestClient(create_app()) as client:
            r = client.get("/health", headers={"Origin": "http://evil.example"})
            assert "access-control-allow-origin" not in r.headers

    def test_wildcard_default_allows_any_origin(self):
        with TestClient(create_app()) as client:
            r = client.get("/health", headers={"Origin": "http://anything.example"})
            assert r.headers.get("access-control-allow-origin") == "*"


class TestConfigureLogging:
    def test_valid_level(self):
        configure_logging("DEBUG")  # must not raise

    def test_invalid_level_falls_back(self):
        configure_logging("NOT_A_LEVEL")  # must not raise
