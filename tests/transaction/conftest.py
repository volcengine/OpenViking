# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared fixtures for transaction tests using real AGFS and VectorDB backends."""

import os
import shutil
import uuid

import pytest

from openviking.agfs_manager import AGFSManager
from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.transaction.lock_manager import LockManager
from openviking.storage.transaction.path_lock import LOCK_FILE_NAME, _make_fencing_token
from openviking.storage.transaction.redo_log import RedoLog
from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend
from openviking.utils.agfs_utils import create_agfs_client
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

AGFS_CONF = AGFSConfig(
    path="/tmp/ov-tx-test", backend="local", port=1834, url="http://localhost:1834", timeout=10
)

VECTOR_DIM = 4
COLLECTION_NAME = "tx_test_ctx"

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
    path = f"/local/tx-tests/{uuid.uuid4().hex}"
    _mkdir_ok(agfs_client, "/local")
    _mkdir_ok(agfs_client, "/local/tx-tests")
    _mkdir_ok(agfs_client, path)
    yield path
    try:
        agfs_client.rm(path, recursive=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# VectorDB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def vector_store(tmp_path_factory):
    """Session-scoped real local VectorDB backend."""
    db_path = str(tmp_path_factory.mktemp("vectordb"))
    config = VectorDBBackendConfig(
        backend="local",
        name=COLLECTION_NAME,
        path=db_path,
        dimension=VECTOR_DIM,
    )
    store = VikingVectorIndexBackend(config=config)

    import asyncio

    schema = CollectionSchemas.context_collection(COLLECTION_NAME, VECTOR_DIM)
    asyncio.get_event_loop().run_until_complete(store.create_collection(COLLECTION_NAME, schema))

    yield store

    asyncio.get_event_loop().run_until_complete(store.close())


@pytest.fixture(scope="session")
def request_ctx():
    """Session-scoped RequestContext for VectorDB operations."""
    user = UserIdentifier("default", "test_user", "default")
    return RequestContext(user=user, role=Role.ROOT)


# ---------------------------------------------------------------------------
# Lock fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lock_manager(agfs_client):
    """Function-scoped LockManager with real AGFS backend."""
    return LockManager(agfs=agfs_client, lock_timeout=1.0, lock_expire=1.0)


@pytest.fixture
def redo_log(agfs_client):
    """Function-scoped RedoLog with real AGFS backend."""
    return RedoLog(agfs_client)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def file_exists(agfs_client, path) -> bool:
    """Check if a file/dir exists in AGFS."""
    try:
        agfs_client.stat(path)
        return True
    except Exception:
        return False


def make_lock_file(agfs_client, dir_path, tx_id, lock_type="P") -> str:
    """Create a real lock file in AGFS and return its path."""
    lock_path = f"{dir_path.rstrip('/')}/{LOCK_FILE_NAME}"
    token = _make_fencing_token(tx_id, lock_type)
    agfs_client.write(lock_path, token.encode("utf-8"))
    return lock_path
