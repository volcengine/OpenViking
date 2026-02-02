# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Filesystem operation tests"""

import pytest

from openviking import AsyncOpenViking


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
        """Test simple mode listing"""
        client, uri = client_with_resource
        parent_uri = "/".join(uri.split("/")[:-1]) + "/"

        entries = await client.ls(parent_uri, simple=True)

        assert isinstance(entries, list)
        assert all(isinstance(e, str) for e in entries)

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
