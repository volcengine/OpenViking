# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Search tests"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.message import TextPart
from openviking.sync_client import SyncOpenViking
from openviking_cli.client.sync_http import SyncHTTPClient


class TestFind:
    """Test find quick search"""

    async def test_find(self, client_with_resource_sync):
        """Test basic search"""
        client, uri = client_with_resource_sync

        result = await client.find(query="sample document")

        assert hasattr(result, "resources")
        assert hasattr(result, "memories")
        assert hasattr(result, "skills")
        assert hasattr(result, "total")

        """Test limiting result count"""
        result = await client.find(query="test", limit=5)

        assert len(result.resources) <= 5

        """Test search with target URI"""
        result = await client.find(query="sample", target_uri=uri)

        assert hasattr(result, "resources")

        """Test score threshold filtering"""
        result = await client.find(query="sample document", score_threshold=0.1)

        # Verify all results have score >= threshold
        for res in result.resources:
            assert res.score >= 0.1

        """Test no matching results"""
        result = await client.find(query="completely_random_nonexistent_query_xyz123")

        assert result.total >= 0


class TestSearch:
    """Test search complex search"""

    async def test_search(self, client_with_resource_sync):
        """Test basic complex search"""
        client, uri = client_with_resource_sync

        result = await client.search(query="sample document")

        assert hasattr(result, "resources")

        """Test search with session context"""
        session = client.session()
        # Add some messages to establish context
        session.add_message("user", [TextPart("I need help with testing")])

        result = await client.search(query="testing help", session=session)

        assert hasattr(result, "resources")

        """Test limiting result count"""
        result = await client.search(query="sample", limit=3)

        assert len(result.resources) <= 3

        """Test complex search with target URI"""
        parent_uri = "/".join(uri.split("/")[:-1]) + "/"

        result = await client.search(query="sample", target_uri=parent_uri)

        assert hasattr(result, "resources")


class TestSyncWrappers:
    def test_sync_http_client_find_forwards_keyword_args(self):
        mock_find = AsyncMock(return_value="ok")
        client = object.__new__(SyncHTTPClient)
        client._async_client = SimpleNamespace(find=mock_find)

        result = client.find(
            query="sample",
            target_uri="viking://resources/demo",
            limit=5,
            node_limit=3,
            score_threshold=0.25,
            filter={"kind": "resource"},
            tags=["machine-learning"],
            telemetry=True,
        )

        assert result == "ok"
        mock_find.assert_awaited_once_with(
            query="sample",
            target_uri="viking://resources/demo",
            limit=5,
            node_limit=3,
            score_threshold=0.25,
            filter={"kind": "resource"},
            tags=["machine-learning"],
            telemetry=True,
        )

    def test_sync_openviking_search_and_find_forward_keyword_args(self):
        mock_search = AsyncMock(return_value="search-ok")
        mock_find = AsyncMock(return_value="find-ok")
        client = object.__new__(SyncOpenViking)
        client._async_client = SimpleNamespace(search=mock_search, find=mock_find)

        search_result = client.search(
            query="sample",
            target_uri="viking://resources/demo",
            session="session-object",
            session_id="sess-1",
            limit=4,
            score_threshold=0.2,
            filter={"kind": "resource"},
            tags=["feature-store"],
            telemetry=True,
        )
        find_result = client.find(
            query="sample",
            target_uri="viking://resources/demo",
            limit=2,
            score_threshold=0.1,
            filter={"kind": "resource"},
            tags=["feature-store"],
            telemetry=True,
        )

        assert search_result == "search-ok"
        assert find_result == "find-ok"
        mock_search.assert_awaited_once_with(
            query="sample",
            target_uri="viking://resources/demo",
            session="session-object",
            session_id="sess-1",
            limit=4,
            score_threshold=0.2,
            filter={"kind": "resource"},
            tags=["feature-store"],
            telemetry=True,
        )
        mock_find.assert_awaited_once_with(
            query="sample",
            target_uri="viking://resources/demo",
            limit=2,
            score_threshold=0.1,
            filter={"kind": "resource"},
            tags=["feature-store"],
            telemetry=True,
        )
