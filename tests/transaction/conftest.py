# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for transaction tests using real AGFS backend."""

import os
import shutil
import uuid

import pytest

from openviking.agfs_manager import AGFSManager
from openviking.utils.agfs_utils import create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig

AGFS_CONF = AGFSConfig(
    path="/tmp/ov-tx-test", backend="local", port=1834, url="http://localhost:1834", timeout=10
)

# Clean slate before session starts
if os.path.exists(AGFS_CONF.path):
    shutil.rmtree(AGFS_CONF.path)


@pytest.fixture(scope="session")
def agfs_manager():
    manager = AGFSManager(config=AGFS_CONF)
    manager.start()
    yield manager
    manager.stop()


@pytest.fixture(scope="session")
def agfs_client(agfs_manager):
    return create_agfs_client(AGFS_CONF)


def _mkdir_ok(agfs_client, path):
    """Create directory, ignoring already-exists errors."""
    try:
        agfs_client.mkdir(path)
    except Exception:
        pass  # already exists


@pytest.fixture
def test_dir(agfs_client):
    """每个测试独享隔离目录，自动清理。"""
    path = f"/local/tx-tests/{uuid.uuid4().hex}"
    _mkdir_ok(agfs_client, "/local")
    _mkdir_ok(agfs_client, "/local/tx-tests")
    _mkdir_ok(agfs_client, path)
    yield path
    try:
        agfs_client.rm(path, recursive=True)
    except Exception:
        pass
