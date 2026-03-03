# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""AGFS Python Binding Tests for VikingFS interface

Tests the python binding mode of VikingFS which directly uses AGFS implementation
without HTTP server.
"""

import os
import shutil
import uuid

import pytest

from openviking.storage.viking_fs import init_viking_fs
from openviking_cli.utils.config.agfs_config import AGFSConfig

# Direct configuration for testing
AGFS_CONF = AGFSConfig(path="/tmp/ov-test", backend="local", mode="binding-client")

# clean up test directory if it exists
if os.path.exists(AGFS_CONF.path):
    shutil.rmtree(AGFS_CONF.path)


@pytest.fixture(scope="module")
async def viking_fs_binding_instance():
    """Initialize VikingFS with binding mode."""
    from openviking.utils.agfs_utils import create_agfs_client

    # Create AGFS client
    agfs_client = create_agfs_client(AGFS_CONF)

    # Initialize VikingFS with client
    vfs = init_viking_fs(agfs=agfs_client)
    # make sure default/temp directory exists
    await vfs.mkdir("viking://temp/", exist_ok=True)

    # Ensure test directory exists
    await vfs.mkdir("viking://temp/", exist_ok=True)

    yield vfs


@pytest.mark.asyncio
class TestVikingFSBindingLocal:
    """Test VikingFS operations with binding mode (local backend)."""

    async def test_file_operations(self, viking_fs_binding_instance):
        """Test VikingFS file operations: read, write, ls, stat."""
        vfs = viking_fs_binding_instance

        test_filename = f"binding_file_{uuid.uuid4().hex}.txt"
        test_content = "Hello VikingFS Binding! " + uuid.uuid4().hex
        test_uri = f"viking://temp/{test_filename}"

        await vfs.write(test_uri, test_content)

        stat_info = await vfs.stat(test_uri)
        assert stat_info["name"] == test_filename
        assert not stat_info["isDir"]

        entries = await vfs.ls("viking://temp/")
        assert any(e["name"] == test_filename for e in entries)

        read_data = await vfs.read(test_uri)
        assert read_data.decode("utf-8") == test_content

        await vfs.rm(test_uri)

    async def test_directory_operations(self, viking_fs_binding_instance):
        """Test VikingFS directory operations: mkdir, rm, ls, stat."""
        vfs = viking_fs_binding_instance
        test_dir = f"binding_dir_{uuid.uuid4().hex}"
        test_dir_uri = f"viking://temp/{test_dir}/"

        await vfs.mkdir(test_dir_uri)

        stat_info = await vfs.stat(test_dir_uri)
        assert stat_info["name"] == test_dir
        assert stat_info["isDir"]

        root_entries = await vfs.ls("viking://temp/")
        assert any(e["name"] == test_dir and e["isDir"] for e in root_entries)

        file_uri = f"{test_dir_uri}inner.txt"
        await vfs.write(file_uri, "inner content")

        sub_entries = await vfs.ls(test_dir_uri)
        assert any(e["name"] == "inner.txt" for e in sub_entries)

        await vfs.rm(test_dir_uri, recursive=True)

        root_entries = await vfs.ls("viking://temp/")
        assert not any(e["name"] == test_dir for e in root_entries)

    async def test_tree_operations(self, viking_fs_binding_instance):
        """Test VikingFS tree operations."""
        vfs = viking_fs_binding_instance
        base_dir = f"binding_tree_test_{uuid.uuid4().hex}"
        sub_dir = f"viking://temp/{base_dir}/a/b/"
        file_uri = f"{sub_dir}leaf.txt"

        await vfs.mkdir(sub_dir)
        await vfs.write(file_uri, "leaf content")

        entries = await vfs.tree(f"viking://temp/{base_dir}/")
        assert any("leaf.txt" in e["uri"] for e in entries)

        await vfs.rm(f"viking://temp/{base_dir}/", recursive=True)

    async def test_binary_operations(self, viking_fs_binding_instance):
        """Test VikingFS binary file operations."""
        vfs = viking_fs_binding_instance
        test_filename = f"binding_binary_{uuid.uuid4().hex}.bin"
        test_content = bytes([i % 256 for i in range(256)])
        test_uri = f"viking://temp/{test_filename}"

        await vfs.write(test_uri, test_content)

        read_data = await vfs.read(test_uri)
        assert read_data == test_content

        await vfs.rm(test_uri)
