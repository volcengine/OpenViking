# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""AGFS Python Binding Tests for VikingFS interface

Tests the python binding mode of VikingFS which directly uses AGFS implementation
without HTTP server.
"""

import os
import shutil
import uuid

import pytest

from openviking.storage.viking_fs import init_viking_fs
from openviking.utils.resource_processor import ResourceProcessor
from openviking_cli.utils.config.agfs_config import AGFSConfig

# Direct configuration for testing
AGFS_CONF = AGFSConfig(path="/tmp/ov-test", backend="local")

# clean up test directory if it exists
if os.path.exists(AGFS_CONF.path):
    shutil.rmtree(AGFS_CONF.path)


@pytest.fixture(scope="module")
async def viking_fs_binding_instance():
    """Initialize VikingFS with binding mode."""
    from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client

    # Create AGFS client
    agfs_client = create_agfs_client(RagfsBindingConfig(agfs=AGFS_CONF))

    vfs = init_viking_fs(agfs=agfs_client)
    # make sure default/temp directory exists
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

    async def test_flat_file_lock_keeps_persisted_target_a_file(
        self,
        viking_fs_binding_instance,
    ):
        """An exact resource lock must not materialize a file target as a directory."""
        vfs = viking_fs_binding_instance
        unique = uuid.uuid4().hex
        temp_uri = f"viking://temp/flat_lock_{unique}.md"
        target_uri = f"viking://resources/flat_lock_{unique}.md"
        target_path = vfs._uri_to_path(target_uri)
        lease = None

        try:
            await vfs.write(temp_uri, "flat file content")
            lease = await ResourceProcessor.acquire_resource_lock(
                target_path,
                uri=target_uri,
                root_is_file=True,
            )
            await vfs.persist_temp_tree(
                temp_uri,
                target_uri,
                lease_ref=lease,
            )

            stat_info = await vfs.stat(target_uri)
            assert stat_info["isDir"] is False
            assert await vfs.read(target_uri) == b"flat file content"
        finally:
            if lease is not None:
                await vfs._async_agfs.pathlock_release(lease)
            for uri in (temp_uri, target_uri):
                if await vfs.exists(uri):
                    stat_info = await vfs.stat(uri)
                    await vfs.rm(uri, recursive=bool(stat_info.get("isDir")))

    async def test_borrowed_pathlock_cannot_release_via_raw_ref(self, viking_fs_binding_instance):
        """Reject borrowed lifecycle control through typed and raw lease refs."""
        vfs = viking_fs_binding_instance
        path = f"/local/default/temp/borrowed_release_{uuid.uuid4().hex}.txt"
        owned = await vfs._async_agfs.pathlock_acquire_exact(path)
        borrowed = await vfs._async_agfs.pathlock_as_borrowed(owned)

        try:
            with pytest.raises(ValueError):
                await vfs._async_agfs.pathlock_release(borrowed)
            with pytest.raises((TypeError, ValueError)):
                await vfs._async_agfs.pathlock_release(borrowed["lease_ref"])
        finally:
            await vfs._async_agfs.pathlock_release(owned)

    async def test_pathlock_adopt_rejects_forged_tree_coverage(self, viking_fs_binding_instance):
        """Reject handoff coverage that is stronger than the live token."""
        vfs = viking_fs_binding_instance
        path = f"/local/default/temp/handoff_forge_{uuid.uuid4().hex}.txt"
        owned = await vfs._async_agfs.pathlock_acquire_exact(path)
        handoff = await vfs._async_agfs.pathlock_to_handoff(owned)
        handoff["covered_paths"] = [{"path": path, "kind": "tree"}]
        adopted = None

        try:
            with pytest.raises(ValueError):
                adopted = await vfs._async_agfs.pathlock_adopt(handoff)
        finally:
            if adopted is not None:
                await vfs._async_agfs.pathlock_release(adopted)
            await vfs._async_agfs.pathlock_release(owned)

    async def test_pathlock_adopt_accepts_legacy_handle_id_handoff(
        self, viking_fs_binding_instance
    ):
        """Accept legacy durable handoffs that use handle_id instead of owner_id."""
        vfs = viking_fs_binding_instance
        path = f"/local/default/temp/handoff_legacy_{uuid.uuid4().hex}.txt"
        owned = await vfs._async_agfs.pathlock_acquire_exact(path)
        handoff = await vfs._async_agfs.pathlock_to_handoff(owned)
        legacy_handoff = {
            "handle_id": handoff["owner_id"],
            "lock_paths": handoff["lock_paths"],
        }
        adopted = None

        try:
            adopted = await vfs._async_agfs.pathlock_adopt(legacy_handoff)
            assert adopted["owner_id"] == owned["owner_id"]
            assert adopted["owned"] is True
        finally:
            if adopted is not None:
                await vfs._async_agfs.pathlock_release(adopted)
            await vfs._async_agfs.pathlock_release(owned)

    @pytest.mark.parametrize(
        "requests",
        [
            [],
            [{"kind": "exact"}],
            [{"path": "", "kind": "exact"}],
            [{"path": "relative", "kind": "tree"}],
            [{"path": "/local/default/temp/file", "kind": "other"}],
        ],
    )
    async def test_pathlock_batch_rejects_invalid_requests(
        self, viking_fs_binding_instance, requests
    ):
        """Reject malformed pathlock requests without applying defaults."""
        with pytest.raises(ValueError):
            await viking_fs_binding_instance._async_agfs.pathlock_acquire_batch(requests)

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

    async def test_glob_matches_deep_markdown_files(self, viking_fs_binding_instance):
        """Test glob recursively matches markdown files beyond tree's default depth."""
        vfs = viking_fs_binding_instance
        base_dir = f"binding_glob_test_{uuid.uuid4().hex}"
        deep_dir_uri = f"viking://temp/{base_dir}/events/2023/05/08/"
        deep_file_uri = f"{deep_dir_uri}entry.md"

        await vfs.mkdir(deep_dir_uri)
        await vfs.write(deep_file_uri, "# deep event")

        result = await vfs.glob("**/*.md", uri=f"viking://temp/{base_dir}/")

        assert deep_file_uri in result["matches"]

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
