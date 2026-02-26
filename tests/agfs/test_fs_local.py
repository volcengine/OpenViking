# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""AGFS Local Backend Tests for VikingFS interface"""

import os
import uuid

import pytest

from openviking.agfs_manager import AGFSManager
from openviking.storage.viking_fs import init_viking_fs
from openviking_cli.utils.config.agfs_config import AGFSConfig

# 1. Direct configuration for testing
AGFS_CONF = AGFSConfig(
    path="/tmp/ov-test", backend="local", port=1833, url="http://localhost:1833", timeout=10
)

# 2. Ensure test directory exists
os.makedirs(AGFS_CONF.path, exist_ok=True)


@pytest.fixture(scope="module")
async def viking_fs_instance():
    """Initialize AGFS Manager and VikingFS singleton."""
    from openviking.utils.agfs_utils import create_agfs_client

    manager = AGFSManager(config=AGFS_CONF)
    manager.start()

    # Create AGFS client
    agfs_client = create_agfs_client(AGFS_CONF)

    # Initialize VikingFS with client
    vfs = init_viking_fs(agfs=agfs_client)

    yield vfs

    # AGFSManager.stop is synchronous
    manager.stop()


@pytest.mark.asyncio
class TestVikingFSLocal:
    """Test VikingFS operations with local backend."""

    async def test_file_operations(self, viking_fs_instance):
        """Test VikingFS file operations: read, write, ls, stat."""
        vfs = viking_fs_instance
        test_filename = f"local_file_{uuid.uuid4().hex}.txt"
        test_content = "Hello VikingFS Local! " + uuid.uuid4().hex
        test_uri = f"viking://temp/{test_filename}"

        # 1. Write file
        await vfs.write(test_uri, test_content)

        # 2. Stat file
        stat_info = await vfs.stat(test_uri)
        assert stat_info["name"] == test_filename
        assert not stat_info["isDir"]

        # 3. List directory
        entries = await vfs.ls("viking://temp/")
        assert any(e["name"] == test_filename for e in entries)

        # 4. Read file
        read_data = await vfs.read(test_uri)
        assert read_data.decode("utf-8") == test_content

        # Cleanup
        await vfs.rm(test_uri)

    async def test_directory_operations(self, viking_fs_instance):
        """Test VikingFS directory operations: mkdir, rm, ls, stat."""
        vfs = viking_fs_instance
        test_dir = f"local_dir_{uuid.uuid4().hex}"
        test_dir_uri = f"viking://temp/{test_dir}/"

        # 1. Create directory
        await vfs.mkdir(test_dir_uri)

        # 2. Stat directory
        stat_info = await vfs.stat(test_dir_uri)
        assert stat_info["name"] == test_dir
        assert stat_info["isDir"]

        # 3. List root to see directory
        root_entries = await vfs.ls("viking://temp/")
        assert any(e["name"] == test_dir and e["isDir"] for e in root_entries)

        # 4. Write a file inside
        file_uri = f"{test_dir_uri}inner.txt"
        await vfs.write(file_uri, "inner content")

        # 5. List subdirectory
        sub_entries = await vfs.ls(test_dir_uri)
        assert any(e["name"] == "inner.txt" for e in sub_entries)

        # 6. Delete directory (recursive)
        await vfs.rm(test_dir_uri, recursive=True)

        # 7. Verify deletion
        root_entries = await vfs.ls("viking://temp/")
        assert not any(e["name"] == test_dir for e in root_entries)

    async def test_ensure_dirs(self, viking_fs_instance):
        """Test VikingFS ensure_dirs."""
        vfs = viking_fs_instance
        base_dir = f"local_tree_test_{uuid.uuid4().hex}"
        sub_dir = f"viking://temp/{base_dir}/a/b/"
        file_uri = f"{sub_dir}leaf.txt"

        await vfs.mkdir(sub_dir)
        await vfs.write(file_uri, "leaf content")

        # VikingFS.tree provides recursive listing
        entries = await vfs.tree(f"viking://temp/{base_dir}/")
        assert any("leaf.txt" in e["uri"] for e in entries)

        # Cleanup
        await vfs.rm(f"viking://temp/{base_dir}/", recursive=True)
