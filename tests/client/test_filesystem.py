# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Filesystem operation tests"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking import AsyncOpenViking, OpenViking
from openviking.client import LocalClient
from openviking.server.identity import RequestContext, Role
from openviking.telemetry import get_current_telemetry
from openviking_cli.session.user_id import UserIdentifier


class TestLs:
    """Test ls operation"""

    async def test_ls_directory(self, client_with_resource):
        """Test listing directory contents"""
        client, uri = client_with_resource
        # Get parent directory
        parent_uri = "/".join(uri.split("/")[:-1]) + "/"

        entries = await client.ls(parent_uri)

        assert isinstance(entries, list)
        assert len(entries) > 0

    async def test_ls_simple_mode(self, client_with_resource):
        """Test simple mode listing returns non-empty URI strings (fixes #218)"""
        client, uri = client_with_resource
        parent_uri = "/".join(uri.split("/")[:-1]) + "/"

        entries = await client.ls(parent_uri, simple=True)

        assert isinstance(entries, list)
        assert all(isinstance(e, str) for e in entries)
        assert all(e.startswith("viking://") for e in entries)

    async def test_ls_recursive(self, client_with_resource):
        """Test recursive listing"""
        client, _ = client_with_resource

        entries = await client.ls("viking://", recursive=True)

        assert isinstance(entries, list)

    async def test_ls_root(self, client: AsyncOpenViking):
        """Test listing root directory"""
        entries = await client.ls("viking://")

        assert isinstance(entries, list)


class TestRead:
    """Test read operation"""

    async def test_read_file(self, client_with_resource):
        """Test reading file content"""
        client, uri = client_with_resource
        entries = await client.tree(uri)
        content = ""
        for e in entries:
            if not e["isDir"]:
                content = await client.read(e["uri"])
                assert isinstance(content, str)
                assert len(content) > 0
                assert "Sample Document" in content

    async def test_read_nonexistent_file(self, client: AsyncOpenViking):
        """Test reading nonexistent file"""
        with pytest.raises(Exception):  # noqa: B017
            await client.read("viking://nonexistent/file.txt")

    async def test_write_with_wait_returns_queue_status(self):
        """Test local SDK write(wait=True) preserves queue_status and binds telemetry."""
        queue_status = {
            "Semantic": {"processed": 1, "error_count": 0, "errors": []},
            "Embedding": {"processed": 0, "error_count": 0, "errors": []},
        }
        seen: dict[str, object] = {}

        async def _fake_write(**kwargs):
            telemetry = get_current_telemetry()
            seen["enabled"] = telemetry.enabled
            seen["telemetry_id"] = telemetry.telemetry_id
            seen["kwargs"] = kwargs
            return {"uri": kwargs["uri"], "queue_status": queue_status}

        client = LocalClient.__new__(LocalClient)
        client._ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
        client._service = SimpleNamespace(fs=SimpleNamespace(write=_fake_write))

        result = await LocalClient.write(
            client,
            uri="viking://resources/demo.md",
            content="Updated from client test",
            wait=True,
            telemetry=False,
        )

        assert result["uri"] == "viking://resources/demo.md"
        assert result["queue_status"] == queue_status
        assert seen["enabled"] is True
        assert str(seen["telemetry_id"]).startswith("tm_")
        assert seen["kwargs"]["wait"] is True


class TestAbstract:
    """Test abstract operation"""

    async def test_abstract_directory(self, client_with_resource):
        """Test reading directory abstract"""
        client, uri = client_with_resource
        # Get parent directory
        parent_uri = "/".join(uri.split("/")[:-1]) + "/"

        abstract = await client.abstract(parent_uri)

        assert isinstance(abstract, str)


class TestOverview:
    """Test overview operation"""

    async def test_overview_directory(self, client_with_resource):
        """Test reading directory overview"""
        client, uri = client_with_resource
        parent_uri = "/".join(uri.split("/")[:-1]) + "/"

        overview = await client.overview(parent_uri)

        assert isinstance(overview, str)


class TestTree:
    """Test tree operation"""

    async def test_tree_success(self, client_with_resource):
        """Test getting directory tree"""
        client, _ = client_with_resource

        tree = await client.tree("viking://")

        assert isinstance(tree, (list, dict))

    async def test_tree_specific_directory(self, client_with_resource):
        """Test getting tree of specific directory"""
        client, uri = client_with_resource
        parent_uri = "/".join(uri.split("/")[:-1]) + "/"

        tree = await client.tree(parent_uri)

        assert isinstance(tree, (list, dict))


async def test_sync_openviking_write_updates_existing_file(test_data_dir, sample_markdown_file):
    """Sync OpenViking exposes write() and delegates to the async client."""
    await AsyncOpenViking.reset()
    client = OpenViking(path=str(test_data_dir))

    try:
        client._async_client.write = AsyncMock(return_value={"uri": "viking://resources/demo.md"})

        write_result = client.write(
            "viking://resources/demo.md",
            "updated content",
            mode="append",
            wait=True,
            timeout=3.0,
            telemetry=False,
        )

        assert write_result == {"uri": "viking://resources/demo.md"}
        client._async_client.write.assert_awaited_once_with(
            uri="viking://resources/demo.md",
            content="updated content",
            mode="append",
            wait=True,
            timeout=3.0,
            telemetry=False,
        )
    finally:
        client.close()
        await AsyncOpenViking.reset()
