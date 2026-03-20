# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the stats API router."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openviking.server.routers.stats import router


@pytest.fixture
def mock_service():
    """Create a mock OpenVikingService."""
    service = MagicMock()
    service.vikingdb_manager = AsyncMock()
    service.vikingdb_manager.query = AsyncMock(return_value=[])

    # Mock session access
    mock_session = MagicMock()
    mock_session.load = AsyncMock()
    mock_stats = MagicMock()
    mock_stats.total_turns = 5
    mock_stats.memories_extracted = 3
    mock_stats.contexts_used = 2
    mock_stats.skills_used = 1
    mock_session.stats = mock_stats
    service.sessions.session.return_value = mock_session

    return service


@pytest.fixture
def mock_ctx():
    """Create a mock request context."""
    return MagicMock()


@pytest.fixture
def client(mock_service, mock_ctx):
    """Create a test client with mocked dependencies."""
    app = FastAPI()
    app.include_router(router)

    with (
        patch("openviking.server.routers.stats.get_service", return_value=mock_service),
        patch("openviking.server.routers.stats.get_request_context", return_value=mock_ctx),
    ):
        yield TestClient(app)


class TestGetMemoryStats:
    def test_empty_store(self, client):
        """Empty store returns zero counts."""
        response = client.get("/api/v1/stats/memories")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["result"]["total_memories"] == 0

    def test_invalid_category(self, client):
        """Unknown category returns an error."""
        response = client.get("/api/v1/stats/memories?category=bogus")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "INVALID_ARGUMENT" in data["error"]["code"]

    def test_valid_category_filter(self, client):
        """Valid category returns filtered stats."""
        response = client.get("/api/v1/stats/memories?category=cases")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "cases" in data["result"]["by_category"]

    def test_response_shape(self, client):
        """Response includes all expected top-level keys."""
        response = client.get("/api/v1/stats/memories")
        data = response.json()["result"]
        assert "total_memories" in data
        assert "by_category" in data
        assert "hotness_distribution" in data
        assert "staleness" in data
        assert "total_vectors" not in data


class TestGetSessionStats:
    def test_session_stats(self, client):
        """Session stats endpoint returns session data."""
        response = client.get("/api/v1/stats/sessions/test-session-123")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        result = data["result"]
        assert result["session_id"] == "test-session-123"
        assert result["total_turns"] == 5
        assert result["memories_extracted"] == 3

    def test_session_not_found(self, client, mock_service):
        """Missing session returns NOT_FOUND error."""
        mock_service.sessions.session.side_effect = KeyError("nonexistent")
        response = client.get("/api/v1/stats/sessions/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["error"]["code"] == "NOT_FOUND"

    def test_session_internal_error(self, client, mock_service):
        """Unexpected exception returns INTERNAL_ERROR, not NOT_FOUND."""
        mock_service.sessions.session.side_effect = RuntimeError("db timeout")
        response = client.get("/api/v1/stats/sessions/some-session")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["error"]["code"] == "INTERNAL_ERROR"
