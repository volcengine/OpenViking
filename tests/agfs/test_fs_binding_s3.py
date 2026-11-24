# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""AGFS Python Binding Tests for VikingFS interface with S3 backend

Tests the python binding mode of VikingFS with S3 backend (MinIO/TOS).
"""

import json
import os
import uuid
from pathlib import Path

import pytest

from openviking.storage.viking_fs import VikingFS
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.agfs_config import AGFSConfig

CONFIG_FILE = os.getenv(OPENVIKING_CONFIG_ENV)
if not CONFIG_FILE:
    default_conf = Path(__file__).parent / "ov.conf"
    if default_conf.exists():
        CONFIG_FILE = str(default_conf)


def load_agfs_config() -> AGFSConfig:
    """Load only AGFS configuration from the config file."""
    if not CONFIG_FILE or not Path(CONFIG_FILE).exists():
        return None

    try:
        with open(CONFIG_FILE, "r") as f:
            full_config = json.load(f)

        agfs_data = full_config.get("storage", {}).get("agfs") or full_config.get("agfs")
        if not agfs_data:
            return None

        return AGFSConfig(**agfs_data)
    except Exception:
        return None


AGFS_CONF = load_agfs_config()

pytestmark = pytest.mark.skipif(
    AGFS_CONF is None or AGFS_CONF.backend != "s3",
    reason="AGFS binding client install failed or S3 configuration not available",
)


@pytest.fixture(scope="module")
def viking_fs_binding_s3_instance(tmp_path_factory):
    """Initialize VikingFS with binding mode for S3 backend."""
    from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client

    config = AGFS_CONF.model_copy(update={"path": str(tmp_path_factory.mktemp("agfs-s3"))})
    client = create_agfs_client(RagfsBindingConfig(agfs=config))
    try:
        yield VikingFS(agfs=client)
    finally:
        client.close()


@pytest.mark.asyncio
class TestVikingFSBindingS3:
    """Test VikingFS operations with binding mode (S3 backend)."""

    async def test_s3_nested_directory_lifecycle_preserves_file_bytes(
        self, viking_fs_binding_s3_instance
    ):
        vfs = viking_fs_binding_s3_instance
        root_name = f"s3_binding_{uuid.uuid4().hex}"
        root = f"viking://temp/{root_name}/"
        sub_dir = f"{root}nested/deep/"
        text_uri = f"{sub_dir}note.txt"
        binary_uri = f"{sub_dir}data.bin"
        text = "Hello VikingFS S3 — 内容"
        binary = bytes(range(256))

        await vfs.mkdir(sub_dir)
        try:
            info = await vfs.stat(sub_dir)
            assert info["isDir"] and info["name"] == "deep"
            root_entries = await vfs.ls("viking://temp/")
            assert any(e["name"] == root_name and e["isDir"] for e in root_entries)

            await vfs.write(text_uri, text)
            await vfs.write(binary_uri, binary)
            tree = await vfs.tree(root, output="original")
            assert {entry["uri"] for entry in tree if not entry["isDir"]} == {text_uri, binary_uri}

            info = await vfs.stat(text_uri)
            assert info["name"] == "note.txt" and not info["isDir"]
            assert await vfs.read(text_uri) == text.encode("utf-8")
            assert await vfs.read(binary_uri) == binary

            await vfs.rm(text_uri)
            assert [entry["name"] for entry in await vfs.ls(sub_dir)] == ["data.bin"]
        finally:
            await vfs.rm(root, recursive=True)

        assert all(e["name"] != root_name for e in await vfs.ls("viking://temp/"))
