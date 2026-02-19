# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for HTTP mode.

The server is automatically started via the ``server_url`` session fixture
defined in ``conftest.py``.
"""

import pytest
import pytest_asyncio

from openviking_cli.client.http import AsyncHTTPClient
from openviking_cli.exceptions import NotFoundError


class TestHTTPClientIntegration:
    """Integration tests for AsyncHTTPClient."""

    @pytest_asyncio.fixture
    async def client(self, server_url):
        """Create and initialize AsyncHTTPClient."""
        client = AsyncHTTPClient(url=server_url)
        await client.initialize()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_health(self, client):
        """Test health check."""
        result = await client.health()
        assert result is True

    @pytest.mark.asyncio
    async def test_ls_root(self, client):
        """Test ls on root."""
        result = await client.ls("viking://")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_find(self, client):
        """Test find operation."""
        result = await client.find(query="test", limit=5)
        assert result is not None
        assert hasattr(result, "resources")
        assert hasattr(result, "total")

    @pytest.mark.asyncio
    async def test_search(self, client):
        """Test search operation."""
        result = await client.search(query="test", limit=5)
        assert result is not None

    @pytest.mark.asyncio
    async def test_stat_not_found(self, client):
        """Test stat on non-existent path raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await client.stat("viking://nonexistent/path")

    @pytest.mark.asyncio
    async def test_tree(self, client):
        """Test tree operation."""
        result = await client.tree("viking://")
        assert result is not None

    @pytest.mark.asyncio
    async def test_observer_vikingdb(self, client):
        """Test observer vikingdb status."""
        result = await client._get_vikingdb_status()
        assert result is not None
        assert "is_healthy" in result

    @pytest.mark.asyncio
    async def test_observer_queue(self, client):
        """Test observer queue status."""
        result = await client._get_queue_status()
        assert result is not None


class TestSessionIntegration:
    """Integration tests for Session operations."""

    @pytest_asyncio.fixture
    async def client(self, server_url):
        """Create and initialize AsyncHTTPClient."""
        client = AsyncHTTPClient(url=server_url)
        await client.initialize()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_session_lifecycle(self, client):
        """Test session create, add message, and delete."""
        # Create session
        result = await client.create_session()
        assert "session_id" in result
        session_id = result["session_id"]

        # Add message
        msg_result = await client.add_message(
            session_id=session_id,
            role="user",
            content="Hello, this is a test message",
        )
        assert msg_result is not None

        # Get session
        session_data = await client.get_session(session_id)
        assert session_data is not None

        # Delete session
        await client.delete_session(session_id)

    @pytest.mark.asyncio
    async def test_list_sessions(self, client):
        """Test list sessions."""
        result = await client.list_sessions()
        assert isinstance(result, list)


class TestAsyncHTTPClientIntegration:
    """Integration tests for AsyncHTTPClient as a standalone client."""

    @pytest_asyncio.fixture
    async def client(self, server_url):
        """Create AsyncHTTPClient."""
        client = AsyncHTTPClient(url=server_url)
        await client.initialize()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_find_via_client(self, client):
        """Test find via AsyncHTTPClient."""
        result = await client.find(query="test", limit=5)
        assert result is not None

    @pytest.mark.asyncio
    async def test_ls_via_client(self, client):
        """Test ls via AsyncHTTPClient."""
        result = await client.ls("viking://")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_observer_access(self, client):
        """Test observer access."""
        observer = client.observer
        assert observer is not None

    @pytest.mark.asyncio
    async def test_session_via_client(self, client):
        """Test session creation via AsyncHTTPClient."""
        session = client.session()
        assert session is not None
        assert session._client is not None
