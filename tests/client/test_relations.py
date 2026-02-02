# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Relation tests"""


class TestLink:
    """Test link creating relations"""

    async def test_link_single_uri(self, client_with_resource):
        """Test creating single relation"""
        client, uri = client_with_resource
        target_uri = "viking://resources/target/"

        await client.link(from_uri=uri, uris=target_uri, reason="Test link")

        relations = await client.relations(uri)
        assert any(r.get("uri") == target_uri for r in relations)

    async def test_link_multiple_uris(self, client_with_resource):
        """Test creating multiple relations"""
        client, uri = client_with_resource
        target_uris = ["viking://resources/target1/", "viking://resources/target2/"]

        await client.link(from_uri=uri, uris=target_uris, reason="Test multiple links")

        relations = await client.relations(uri)
        for target in target_uris:
            assert any(r.get("uri") == target for r in relations)

    async def test_link_with_reason(self, client_with_resource):
        """Test creating relation with reason"""
        client, uri = client_with_resource
        target_uri = "viking://resources/reason_test/"
        reason = "This is a test reason for the link"

        await client.link(from_uri=uri, uris=target_uri, reason=reason)

        relations = await client.relations(uri)
        link = next((r for r in relations if r.get("uri") == target_uri), None)
        assert link is not None
        assert link.get("reason") == reason


class TestUnlink:
    """Test unlink deleting relations"""

    async def test_unlink_success(self, client_with_resource):
        """Test successful relation deletion"""
        client, uri = client_with_resource
        target_uri = "viking://resources/unlink_test/"

        # Create relation first
        await client.link(from_uri=uri, uris=target_uri, reason="Test")

        # Verify relation exists
        relations = await client.relations(uri)
        assert any(r.get("uri") == target_uri for r in relations)

        # Delete relation
        await client.unlink(from_uri=uri, uri=target_uri)

        # Verify relation deleted
        relations = await client.relations(uri)
        assert not any(r.get("uri") == target_uri for r in relations)

    async def test_unlink_nonexistent(self, client_with_resource):
        """Test deleting nonexistent relation"""
        client, uri = client_with_resource

        # Should not raise exception
        await client.unlink(from_uri=uri, uri="viking://nonexistent/")


class TestRelations:
    """Test relations getting relations"""

    async def test_relations_empty(self, client_with_resource):
        """Test getting empty relation list"""
        client, uri = client_with_resource

        relations = await client.relations(uri)

        assert isinstance(relations, list)

    async def test_relations_with_data(self, client_with_resource):
        """Test getting relation list with data"""
        client, uri = client_with_resource
        target_uri = "viking://resources/relations_test/"

        await client.link(from_uri=uri, uris=target_uri, reason="Test reason")

        relations = await client.relations(uri)

        assert len(relations) > 0
        link = next((r for r in relations if r.get("uri") == target_uri), None)
        assert link is not None
        assert link.get("reason") == "Test reason"
