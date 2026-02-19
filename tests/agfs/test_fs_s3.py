# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""AGFS S3 Backend Tests for VikingFS interface with S3 client verification"""

import json
import os
import uuid
from pathlib import Path

import boto3
import botocore
import pytest

from openviking.agfs_manager import AGFSManager
from openviking.storage.viking_fs import VikingFS, init_viking_fs
from openviking_cli.utils.config.agfs_config import AGFSConfig

# 1. Simplified Config loading logic
# Only extract the AGFS part for focused testing
CONFIG_FILE = os.getenv("OPENVIKING_CONFIG_FILE")
if not CONFIG_FILE:
    # Try default ov.conf in tests/agfs
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

        # Support both 'storage.agfs' and top-level 'agfs' structures
        agfs_data = full_config.get("storage", {}).get("agfs") or full_config.get("agfs")
        if not agfs_data:
            return None

        return AGFSConfig(**agfs_data)
    except Exception:
        return None


AGFS_CONF = load_agfs_config()

# 2. Skip tests if no S3 config found or backend is not S3
pytestmark = pytest.mark.skipif(
    AGFS_CONF is None or AGFS_CONF.backend != "s3",
    reason="AGFS S3 configuration not found in ov.conf",
)


@pytest.fixture(scope="module")
def s3_client():
    """Boto3 client for S3 verification."""

    s3_conf = AGFS_CONF.s3
    return boto3.client(
        "s3",
        aws_access_key_id=s3_conf.access_key,
        aws_secret_access_key=s3_conf.secret_key,
        region_name=s3_conf.region,
        endpoint_url=s3_conf.endpoint,
        use_ssl=s3_conf.use_ssl,
    )


@pytest.fixture(scope="module")
async def viking_fs_instance():
    """Initialize AGFS Manager and VikingFS singleton."""
    manager = AGFSManager(config=AGFS_CONF)
    manager.start()

    # Initialize VikingFS with agfs_url (only basic IO needed)
    vfs = init_viking_fs(agfs_url=AGFS_CONF.url, timeout=AGFS_CONF.timeout)

    yield vfs

    # AGFSManager.stop is synchronous
    manager.stop()


@pytest.mark.asyncio
class TestVikingFSS3:
    """Test VikingFS operations with S3 backend and verify via S3 client."""

    async def test_file_operations(self, viking_fs_instance: "VikingFS", s3_client):
        """Test VikingFS file operations and verify with S3 client."""
        vfs = viking_fs_instance
        s3_conf = AGFS_CONF.s3
        bucket = s3_conf.bucket
        prefix = s3_conf.prefix or ""

        test_filename = f"verify_{uuid.uuid4().hex}.txt"
        test_content = "Hello VikingFS S3! " + uuid.uuid4().hex
        test_uri = f"viking://{test_filename}"

        # 1. Write via VikingFS
        await vfs.write(test_uri, test_content)

        # 2. Verify existence and content via S3 client
        s3_key = f"{prefix}{test_filename}"
        response = s3_client.get_object(Bucket=bucket, Key=s3_key)
        s3_content = response["Body"].read().decode("utf-8")
        assert s3_content == test_content

        # 3. Stat via VikingFS
        stat_info = await vfs.stat(test_uri)
        assert stat_info["name"] == test_filename
        assert not stat_info["isDir"]

        # 4. List via VikingFS
        entries = await vfs.ls("viking://")
        assert any(e["name"] == test_filename for e in entries)

        # 5. Read back via VikingFS
        read_data = await vfs.read(test_uri)
        assert read_data.decode("utf-8") == test_content

        # 6. Cleanup via VikingFS
        await vfs.rm(test_uri)

        # 7. Verify deletion via S3 client
        with pytest.raises(botocore.exceptions.ClientError) as excinfo:
            s3_client.get_object(Bucket=bucket, Key=s3_key)
        assert excinfo.value.response["Error"]["Code"] in ["NoSuchKey", "404"]

    async def test_directory_operations(self, viking_fs_instance, s3_client):
        """Test VikingFS directory operations and verify with S3 client."""
        vfs = viking_fs_instance
        s3_conf = AGFS_CONF.s3
        bucket = s3_conf.bucket
        prefix = s3_conf.prefix or ""

        test_dir = f"test_dir_{uuid.uuid4().hex}"
        test_dir_uri = f"viking://{test_dir}/"

        # 1. Create directory via VikingFS
        await vfs.mkdir(test_dir_uri)

        # 2. Verify via S3 client by writing a file inside
        file_uri = f"{test_dir_uri}inner.txt"
        file_content = "inner content"
        await vfs.write(file_uri, file_content)

        s3_key = f"{prefix}{test_dir}/inner.txt"
        response = s3_client.get_object(Bucket=bucket, Key=s3_key)
        assert response["Body"].read().decode("utf-8") == file_content

        # 3. List via VikingFS
        root_entries = await vfs.ls("viking://")
        assert any(e["name"] == test_dir and e["isDir"] for e in root_entries)

        # 4. Delete directory recursively via VikingFS
        await vfs.rm(test_dir_uri, recursive=True)

        # 5. Verify deletion via S3 client
        with pytest.raises(botocore.exceptions.ClientError):
            s3_client.get_object(Bucket=bucket, Key=s3_key)

    async def test_ensure_dirs(self, viking_fs_instance: "VikingFS"):
        """Test VikingFS ensure_dirs."""
        vfs = viking_fs_instance
        base_dir = f"tree_test_{uuid.uuid4().hex}"
        sub_dir = f"viking://{base_dir}/a/b/"
        file_uri = f"{sub_dir}leaf.txt"

        await vfs.mkdir(sub_dir)
        await vfs.write(file_uri, "leaf content")

        # VikingFS.tree provides recursive listing
        entries = await vfs.tree(f"viking://{base_dir}/")
        assert any("leaf.txt" in e["uri"] for e in entries)

        # Cleanup
        await vfs.rm(f"viking://{base_dir}/", recursive=True)
