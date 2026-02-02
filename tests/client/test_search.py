# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Search tests"""

from openviking.message import TextPart


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
