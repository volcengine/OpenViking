# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Global test fixtures"""

import asyncio
import socket
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import set_task_tracker
from openviking.storage import viking_fs as viking_fs_module
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.utils.mock_agfs import MockLocalAGFS
from tests.utils.watch_fixtures import (  # noqa: F401
    watch_manager,
    watch_store,
    watch_tracker,
    watch_vfs,
)

# Test data root directory
PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def event_loop():
    """Create session-level event loop"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def temp_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Keep concurrent test runs from deleting each other's vector stores."""
    yield tmp_path


@pytest.fixture(scope="function")
def test_data_dir(temp_dir: Path) -> Path:
    """Create test data directory"""
    data_dir = temp_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def vector_backend_factory():
    """Construct local storage with an explicit account configuration boundary."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend

    def create(config):
        backend = VikingVectorIndexBackend(config)
        settings = SimpleNamespace(vectordb=config, dedicated_vectordb=False)
        backend.set_vector_config_resolver(
            SimpleNamespace(resolve=AsyncMock(return_value=settings))
        )
        return backend

    return create


# ============ Service Fixtures ============


@pytest_asyncio.fixture(scope="function")
async def service(
    test_data_dir: Path,
    monkeypatch,
) -> AsyncGenerator[OpenVikingService, None]:
    """Create an initialized service for domain-level tests."""

    previous_viking_fs = viking_fs_module._instance

    class FakeEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="test-fake-embedder")

        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            return EmbedResult(dense_vector=[0.1] * 1024)

        def get_dimension(self) -> int:
            return 1024

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: FakeEmbedder())
    mock_agfs = MockLocalAGFS(root_path=test_data_dir / "mock_agfs_root")
    monkeypatch.setattr(
        "openviking.utils.agfs_utils.create_agfs_client",
        lambda *args, **kwargs: mock_agfs,
    )
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            # Domain tests use a file-only fake; Watch leadership has dedicated fixtures.
            "enable_watch_scheduler": False,
            "storage": {
                "workspace": str(test_data_dir),
                "agfs": {"backend": "local"},
                "vectordb": {"backend": "local"},
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "test-embedder",
                    "api_key": "test-key",
                    "dimension": 1024,
                }
            },
        }
    )
    instance = OpenVikingService(
        path=str(test_data_dir),
        user=UserIdentifier.the_default_user(),
    )
    try:
        await instance.initialize()
        yield instance
    finally:
        await instance.close()
        set_task_tracker(None)
        viking_fs_module._instance = previous_viking_fs
        OpenVikingConfigSingleton.reset_instance()


@pytest.fixture(scope="function")
def request_context() -> RequestContext:
    return RequestContext(
        user=UserIdentifier.the_default_user(),
        role=Role.USER,
    )


@pytest.fixture
def working_memory_policy():
    """Explicit opt-in for tests exercising WM/checkpoint generation."""
    return {"working_memory": {"enabled": True}}


@pytest.fixture
def public_resource_dns(monkeypatch):
    """Resolve fixture URLs without relying on external DNS."""
    resolve = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        if host in {
            "example.com",
            "example.org",
            "example.feishu.cn",
            "example.larkoffice.com",
            "alidocs.dingtalk.com",
        }:
            host = "93.184.215.14"
        return resolve(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
