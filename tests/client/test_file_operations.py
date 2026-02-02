# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""File operation tests"""

from pathlib import Path

import pytest

from openviking import AsyncOpenViking


class TestRm:
    """Test rm delete operation"""

    async def test_rm_file(self, client: AsyncOpenViking, sample_markdown_file: Path):
        """Test deleting file"""
        # Add resource first
        print(f"Add resource: {sample_markdown_file}")
        result = await client.add_resource(
            path=str(sample_markdown_file),
            reason="Test rm",
        )

        uris = await client.tree(result["root_uri"])
        for data in uris:
            if not data["isDir"]:
                await client.rm(data["uri"])
                with pytest.raises(Exception):  # noqa: B017
                    await client.read(data["uri"])

    async def test_rm_directory_recursive(self, client: AsyncOpenViking, sample_directory: Path):
        """Test recursive directory deletion"""
        # Add files from directory first
        for f in sample_directory.glob("**/*.txt"):
            await client.add_resource(path=str(f), reason="Test rm dir")

        # Get resource directory
        entries = await client.ls("viking://resources/")
        for data in entries:
            if data["isDir"]:
                dir_uri = data["uri"]
                await client.rm(dir_uri, recursive=True)
                with pytest.raises(Exception):  # noqa: B017
                    await client.stat(dir_uri)


class TestMv:
    """Test mv move operation"""

    async def test_mv_file(self, client: AsyncOpenViking, sample_markdown_file: Path):
        """Test moving file"""
        # Add resource first
        result = await client.add_resource(
            path=str(sample_markdown_file),
            reason="Test mv",
        )
        uri = result["root_uri"]
        new_uri = "viking://resources/moved/"
        await client.mv(uri, new_uri)
        # Verify original location does not exist
        with pytest.raises(Exception):  # noqa: B017
            await client.stat(uri)

        await client.stat(new_uri)


class TestGrep:
    """Test grep content search"""

    async def test_grep_basic(self, client_with_resource):
        """Test basic content search"""
        client, uri = client_with_resource

        result = await client.grep(uri, pattern="Sample")

        assert isinstance(result, dict)

        assert "matches" in result and result["count"] > 0

    async def test_grep_case_insensitive(self, client_with_resource):
        """Test case insensitive search"""
        client, uri = client_with_resource

        result = await client.grep(uri, pattern="SAMPLE", case_insensitive=True)
        print(result)
        assert isinstance(result, dict)
        assert "matches" in result and result["count"] > 0

    async def test_grep_no_match(self, client_with_resource):
        """Test no matching results"""
        client, uri = client_with_resource

        result = await client.grep(uri, pattern="nonexistent_pattern_xyz123")
        assert isinstance(result, dict)
        matches = result.get("matches", [])
        assert len(matches) == 0


class TestGlob:
    """Test glob file pattern matching"""

    async def test_glob_basic(self, client_with_resource):
        """Test basic pattern matching"""
        client, _ = client_with_resource

        result = await client.glob(pattern="**/*.md")
        assert isinstance(result, dict)
        assert "matches" in result and result["count"] > 0

    async def test_glob_with_uri(self, client_with_resource):
        """Test pattern matching with specified URI"""
        client, uri = client_with_resource
        parent_uri = "/".join(uri.split("/")[:-1]) + "/"

        result = await client.glob(pattern="*.md", uri=parent_uri)
        assert isinstance(result, dict)
        assert "matches" in result and result["count"] > 0

    async def test_glob_txt_files(self, client: AsyncOpenViking, sample_text_file: Path):
        """Test matching txt files"""
        # Add txt file
        await client.add_resource(
            path=str(sample_text_file),
            reason="Test glob txt",
        )

        result = await client.glob(pattern="**/*.md")
        assert isinstance(result, dict) and result["count"] > 0
