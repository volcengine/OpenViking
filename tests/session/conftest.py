# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Session test fixtures"""

import asyncio
import json
from typing import AsyncGenerator
from unittest.mock import patch

import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.message import TextPart, ToolPart
from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.service.task_tracker import TaskStatus, get_task_tracker, reset_task_tracker
from openviking.session import Session
from openviking.storage.transaction import reset_lock_manager
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from openviking_cli.utils.config.vlm_config import VLMConfig
from tests.utils.mock_agfs import MockLocalAGFS


def _install_fake_embedder(monkeypatch):
    class FakeEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="test-fake-embedder")

        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            return EmbedResult(dense_vector=[0.1] * 1024)

        def embed_batch(self, texts: list[str], is_query: bool = False) -> list[EmbedResult]:
            return [self.embed(text, is_query=is_query) for text in texts]

        def get_dimension(self) -> int:
            return 1024

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: FakeEmbedder())


def _install_fake_vlm(monkeypatch):
    async def _fake_get_completion(self, prompt, thinking=False, max_retries=0):
        return "# Test Summary\n\nFake summary for testing.\n\n## Details\nTest content."

    async def _fake_get_vision_completion(self, prompt, images, thinking=False):
        return "Fake image description for testing."

    monkeypatch.setattr(VLMConfig, "is_available", lambda self: True)
    monkeypatch.setattr(VLMConfig, "get_completion_async", _fake_get_completion)
    monkeypatch.setattr(VLMConfig, "get_vision_completion_async", _fake_get_vision_completion)


def _write_test_config(tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(tmp_path / "workspace"),
                    "agfs": {"backend": "local", "mode": "binding-client"},
                    "vectordb": {"backend": "local"},
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "test-embedder",
                        "api_base": "http://127.0.0.1:11434/v1",
                        "dimension": 1024,
                    }
                },
                "memory": {"extraction_enabled": False},
                "encryption": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    return config_path


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def client(test_data_dir, monkeypatch, tmp_path):
    """Create initialized OpenViking client with in-process test doubles."""
    config_path = _write_test_config(tmp_path)
    mock_agfs = MockLocalAGFS(root_path=tmp_path / "mock_agfs_root")

    reset_lock_manager()
    OpenVikingConfigSingleton.reset_instance()
    await AsyncOpenViking.reset()
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    _install_fake_embedder(monkeypatch)
    _install_fake_vlm(monkeypatch)

    with patch("openviking.utils.agfs_utils.create_agfs_client", return_value=mock_agfs):
        with patch(
            "openviking.service.session_service.SessionService._start_archive_finalize_worker",
            lambda self: None,
        ):
            client = AsyncOpenViking(path=str(test_data_dir))
            await client.initialize()
            yield client
            await client.close()

    OpenVikingConfigSingleton.reset_instance()
    await AsyncOpenViking.reset()
    reset_lock_manager()


@pytest_asyncio.fixture(loop_scope="function")
async def _drain_background_tasks(client: AsyncOpenViking):
    """Wait for background commit tasks to finish before client teardown."""
    del client
    reset_task_tracker()
    yield
    # Drain asyncio.create_task() background tasks BEFORE client.close()
    tracker = get_task_tracker()
    for _ in range(100):  # up to 10s
        pending = [
            t
            for t in await tracker.list_tasks()
            if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
        ]
        if not pending:
            break
        await asyncio.sleep(0.1)
    reset_task_tracker()


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def session(client: AsyncOpenViking) -> AsyncGenerator[Session, None]:
    """Create new Session"""
    session = client.session()
    yield session


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def session_with_id(client: AsyncOpenViking) -> AsyncGenerator[Session, None]:
    """Create Session with specified ID"""
    session = client.session(session_id="test_session_001")
    yield session


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def session_with_messages(client: AsyncOpenViking) -> AsyncGenerator[Session, None]:
    """Create Session with existing messages"""
    session = client.session(session_id="test_session_with_messages")

    session.add_message("user", [TextPart("Hello, this is a test message.")])
    session.add_message("assistant", [TextPart("Hello! How can I help you today?")])
    session.add_message("user", [TextPart("I need help with testing.")])
    session.add_message("assistant", [TextPart("I can help you with testing.")])

    yield session


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def session_with_tool_call(
    client: AsyncOpenViking,
) -> AsyncGenerator[tuple[Session, str, str], None]:
    """Create Session with tool call"""
    session = client.session(session_id="test_session_with_tool")

    tool_id = "test_tool_001"
    tool_part = ToolPart(
        tool_id=tool_id,
        tool_name="test_tool",
        tool_uri=f"viking://session/{session.session_id}/tools/{tool_id}",
        skill_uri="viking://agent/skills/test_skill",
        tool_input={"param": "value"},
        tool_status="running",
    )

    msg = session.add_message("assistant", [TextPart("Executing tool..."), tool_part])

    yield session, msg.id, tool_id
