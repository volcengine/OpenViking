# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""AGFS Python Binding Tests for VikingFS interface

Tests the python binding mode of VikingFS which directly uses AGFS implementation
without HTTP server.
"""

import os
import platform
import uuid
from pathlib import Path

import pytest

from openviking.storage.viking_fs import init_viking_fs
from openviking_cli.utils.config.agfs_config import AGFSConfig

# Direct configuration for testing
AGFS_CONF = AGFSConfig(path="/tmp/ov-test", backend="local", mode="binding-client")

# Ensure test directory exists
os.makedirs(AGFS_CONF.path, exist_ok=True)


def get_lib_path() -> str:
    """Get the path to AGFS binding shared library."""
    system = platform.system()
    if system == "Darwin":
        lib_name = "libagfsbinding.dylib"
    elif system == "Windows":
        lib_name = "libagfsbinding.dll"
    else:
        lib_name = "libagfsbinding.so"

    project_root = Path(__file__).parent.parent.parent
    lib_path = project_root / "third_party" / "agfs" / "bin" / lib_name

    if lib_path.exists():
        return str(lib_path)

    env_path = os.environ.get("AGFS_LIB_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    return None


LIB_PATH = get_lib_path()


pytestmark = pytest.mark.skipif(
    LIB_PATH is None,
    reason="AGFS binding library not found. Build it first: make -C third_party/agfs/agfs-server/cmd/pybinding",
)


@pytest.fixture(scope="module")
async def viking_fs_binding_instance():
    """Initialize VikingFS with binding mode."""
    from openviking.utils.agfs_utils import create_agfs_client

    # Set lib_path for the test
    AGFS_CONF.lib_path = LIB_PATH

    # Create AGFS client
    agfs_client = create_agfs_client(AGFS_CONF)

    # Initialize VikingFS with client
    vfs = init_viking_fs(agfs=agfs_client)

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
