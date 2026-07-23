# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Relation tests"""

import pytest

from openviking import AsyncOpenViking
from openviking_cli.exceptions import InvalidArgumentError


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

    async def test_link_from_file_resource(self, client: AsyncOpenViking):
        """Test creating relation when the source is a file resource."""
        source_uri = "viking://resources/file-source.md"
        target_uri = "viking://resources/file-target.md"

        await client.write(source_uri, "source", mode="create")
        await client.write(target_uri, "target", mode="create")

        await client.link(from_uri=source_uri, uris=target_uri, reason="File source test")

        relations = await client.relations(source_uri)
        link = next((r for r in relations if r.get("uri") == target_uri), None)
        assert link is not None
        assert link.get("reason") == "File source test"


class TestFileRelationSidecars:
    """Regression coverage for file-source relation metadata."""

    async def test_file_relation_sidecar_is_hidden_and_not_directly_writable(
        self, client: AsyncOpenViking
    ):
        source_uri = "viking://resources/hidden-sidecar-source.md"
        target_uri = "viking://resources/hidden-sidecar-target.md"
        sidecar_uri = f"{source_uri}.relations.json"

        await client.write(source_uri, "source", mode="create")
        await client.write(target_uri, "target", mode="create")
        await client.link(from_uri=source_uri, uris=target_uri, reason="hidden sidecar")

        entries = await client.ls("viking://resources", show_all_hidden=True)
        assert sidecar_uri not in {entry["uri"] for entry in entries}

        tree = await client.tree("viking://resources", show_all_hidden=True)
        assert sidecar_uri not in {entry["uri"] for entry in tree}

        with pytest.raises(
            InvalidArgumentError, match="cannot write derived semantic file directly"
        ):
            await client.write(sidecar_uri, "collision", mode="create")

    async def test_file_relation_sidecar_follows_mv_and_rm(self, client: AsyncOpenViking):
        source_uri = "viking://resources/move-sidecar-source.md"
        target_uri = "viking://resources/move-sidecar-target.md"
        moved_uri = "viking://resources/move-sidecar-destination.md"
        source_sidecar_uri = f"{source_uri}.relations.json"
        moved_sidecar_uri = f"{moved_uri}.relations.json"
        viking_fs = client._service.viking_fs

        await client.write(source_uri, "source", mode="create")
        await client.write(target_uri, "target", mode="create")
        await client.link(from_uri=source_uri, uris=target_uri, reason="move sidecar")
        assert await viking_fs.exists(source_sidecar_uri, ctx=client._ctx)

        await client.mv(source_uri, moved_uri)

        relations = await client.relations(moved_uri)
        assert any(relation.get("uri") == target_uri for relation in relations)
        assert not await viking_fs.exists(source_sidecar_uri, ctx=client._ctx)
        assert await viking_fs.exists(moved_sidecar_uri, ctx=client._ctx)

        await client.rm(moved_uri)
        assert not await viking_fs.exists(moved_sidecar_uri, ctx=client._ctx)

        orphan_uri = "viking://resources/orphan-sidecar-source.md"
        orphan_sidecar_uri = f"{orphan_uri}.relations.json"
        await viking_fs.write_file(orphan_sidecar_uri, "[]", ctx=client._ctx)
        assert await viking_fs.exists(orphan_sidecar_uri, ctx=client._ctx)

        await client.rm(orphan_uri)
        assert not await viking_fs.exists(orphan_sidecar_uri, ctx=client._ctx)

    async def test_move_file_preserves_file_relation_sidecar(self, client: AsyncOpenViking):
        source_uri = "viking://resources/direct-move-sidecar-source.md"
        target_uri = "viking://resources/direct-move-sidecar-target.md"
        moved_uri = "viking://resources/direct-move-sidecar-destination.md"
        viking_fs = client._service.viking_fs

        await client.write(source_uri, "source", mode="create")
        await client.write(target_uri, "target", mode="create")
        await client.link(from_uri=source_uri, uris=target_uri, reason="direct move sidecar")

        await viking_fs.move_file(source_uri, moved_uri, ctx=client._ctx)

        relations = await client.relations(moved_uri)
        assert any(relation.get("uri") == target_uri for relation in relations)
        assert not await viking_fs.exists(f"{source_uri}.relations.json", ctx=client._ctx)
        assert await viking_fs.exists(f"{moved_uri}.relations.json", ctx=client._ctx)


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
