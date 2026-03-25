# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Context retrieval tests"""

import asyncio
import json
from unittest.mock import patch

import pytest_asyncio

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session
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
                "encryption": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    return config_path


@pytest_asyncio.fixture(scope="function")
async def client(test_data_dir, monkeypatch, tmp_path):
    config_path = _write_test_config(tmp_path)
    mock_agfs = MockLocalAGFS(root_path=tmp_path / "mock_agfs_root")

    OpenVikingConfigSingleton.reset_instance()
    await AsyncOpenViking.reset()
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(config_path))
    _install_fake_embedder(monkeypatch)
    _install_fake_vlm(monkeypatch)

    with patch("openviking.utils.agfs_utils.create_agfs_client", return_value=mock_agfs):
        client = AsyncOpenViking(path=str(test_data_dir))
        await client.initialize()
        yield client
        await client.close()

    OpenVikingConfigSingleton.reset_instance()
    await AsyncOpenViking.reset()


def _estimate_tokens(text: str) -> int:
    return -(-len(text) // 4)


async def _wait_for_task(task_id: str, timeout: float = 30.0) -> dict:
    tracker = get_task_tracker()
    for _ in range(int(timeout / 0.1)):
        task = tracker.get(task_id)
        if task and task.status.value in ("completed", "failed"):
            return task.to_dict()
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


class TestGetContextForSearch:
    """Test get_context_for_search"""

    async def test_get_context_basic(self, session_with_messages: Session):
        """Test basic context retrieval"""
        context = await session_with_messages.get_context_for_search(query="testing help")

        assert isinstance(context, dict)
        assert "latest_archive_overview" in context
        assert "current_messages" in context

    async def test_get_context_with_max_messages(self, session_with_messages: Session):
        """Test limiting max messages"""
        context = await session_with_messages.get_context_for_search(query="test", max_messages=2)

        assert isinstance(context, dict)
        assert len(context["current_messages"]) <= 2

    async def test_get_context_returns_latest_completed_archive_only(self, client: AsyncOpenViking):
        """Current context should expose only the latest completed archive overview."""
        session = client.session(session_id="archive_context_test")

        session.add_message("user", [TextPart("First message")])
        session.add_message("assistant", [TextPart("First response")])
        result1 = await session.commit_async()
        await _wait_for_task(result1["task_id"])

        session.add_message("user", [TextPart("Second message")])
        session.add_message("assistant", [TextPart("Second response")])
        session.add_message("user", [TextPart("Third message")])
        result2 = await session.commit_async()
        await _wait_for_task(result2["task_id"])
        latest_overview = await session._viking_fs.read_file(
            f"{result2['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )

        session.add_message("user", [TextPart("Current message")])
        context = await session.get_context_for_search(query="test")

        assert isinstance(context, dict)
        assert context["latest_archive_overview"] == latest_overview
        assert len(context["current_messages"]) == 1

    async def test_get_context_skips_incomplete_latest_archive(self, client: AsyncOpenViking):
        """Incomplete archives without .done must not replace the latest completed overview."""
        session = client.session(session_id="archive_context_incomplete_test")

        session.add_message("user", [TextPart("First message")])
        session.add_message("assistant", [TextPart("First response")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        completed_overview = await session._viking_fs.read_file(
            f"{result['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )
        await session._viking_fs.write_file(
            uri=f"{session.uri}/history/archive_999/.overview.md",
            content="INCOMPLETE OVERVIEW",
            ctx=session.ctx,
        )

        context = await session.get_context_for_search(query="test")

        assert context["latest_archive_overview"] == completed_overview

    async def test_get_context_empty_session(self, session: Session):
        """Test getting context from empty session"""
        context = await session.get_context_for_search(query="test")

        assert isinstance(context, dict)
        assert context["latest_archive_overview"] == ""
        assert context["current_messages"] == []

    async def test_get_context_after_commit(self, client: AsyncOpenViking):
        """Test getting context after commit"""
        session = client.session(session_id="post_commit_context_test")

        session.add_message("user", [TextPart("Test message before commit")])
        session.add_message("assistant", [TextPart("Response before commit")])

        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("New message after commit")])

        context = await session.get_context_for_search(query="test")

        assert isinstance(context, dict)
        assert context["latest_archive_overview"]
        assert len(context["current_messages"]) == 1


class TestGetContextForAssemble:
    """Test get_context_for_assemble"""

    async def test_get_context_for_assemble_trims_archives_from_newest(
        self, client: AsyncOpenViking, monkeypatch
    ):
        session = client.session(session_id="assemble_trim_test")
        summaries = [
            "# Session Summary\n\n" + ("A" * 80),
            "# Session Summary\n\n" + ("B" * 20),
        ]

        async def fake_generate(_messages, latest_archive_overview=""):
            del latest_archive_overview
            return summaries.pop(0)

        monkeypatch.setattr(session, "_generate_archive_summary_async", fake_generate)

        session.add_message("user", [TextPart("first turn")])
        session.add_message("assistant", [TextPart("first reply")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("second turn")])
        session.add_message("assistant", [TextPart("second reply")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("active tail")])

        newest_summary = "# Session Summary\n\n" + ("B" * 20)
        active_tokens = sum(message.estimated_tokens for message in session.messages)
        token_budget = active_tokens + _estimate_tokens(newest_summary)

        context = await session.get_context_for_assemble(token_budget=token_budget)

        assert [archive["index"] for archive in context["archives"]] == [2]
        assert len(context["messages"]) == 1
        assert context["messages"][0]["parts"][0]["text"] == "active tail"
        assert context["estimatedTokens"] == token_budget
        assert context["stats"] == {
            "totalArchives": 2,
            "includedArchives": 1,
            "droppedArchives": 1,
            "failedArchives": 0,
            "activeTokens": active_tokens,
            "archiveTokens": _estimate_tokens(newest_summary),
        }

    async def test_get_context_for_assemble_counts_active_tool_parts(
        self, session_with_tool_call: tuple[Session, str, str]
    ):
        session, _message_id, tool_id = session_with_tool_call

        context = await session.get_context_for_assemble()

        assert len(context["messages"]) == 1
        tool_parts = [part for part in context["messages"][0]["parts"] if part["type"] == "tool"]
        assert tool_parts[0]["tool_id"] == tool_id
        assert context["stats"]["activeTokens"] == session.messages[0].estimated_tokens
        assert context["stats"]["activeTokens"] > _estimate_tokens("Executing tool...")

    async def test_get_context_for_assemble_skips_old_archives_on_budget(
        self, client: AsyncOpenViking, monkeypatch
    ):
        """When budget only fits the newest archive, older archives' overview/abstract
        should never be read (lazy-read optimisation)."""
        session = client.session(session_id="assemble_lazy_read_test")
        summaries = [
            "# Summary\n\n" + ("A" * 80),
            "# Summary\n\n" + ("B" * 80),
            "# Summary\n\n" + ("C" * 80),
        ]

        async def fake_generate(_messages, latest_archive_overview=""):
            del latest_archive_overview
            return summaries.pop(0)

        monkeypatch.setattr(session, "_generate_archive_summary_async", fake_generate)

        for word in ("first", "second", "third"):
            session.add_message("user", [TextPart(f"{word} turn")])
            session.add_message("assistant", [TextPart(f"{word} reply")])
            result = await session.commit_async()
            await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("active tail")])

        newest_summary = "# Summary\n\n" + ("C" * 80)
        active_tokens = sum(m.estimated_tokens for m in session.messages)
        token_budget = active_tokens + _estimate_tokens(newest_summary)

        original_read_file = session._viking_fs.read_file
        read_uris: list[str] = []

        async def tracking_read_file(*args, **kwargs):
            uri = args[0] if args else kwargs.get("uri")
            read_uris.append(uri)
            return await original_read_file(*args, **kwargs)

        monkeypatch.setattr(session._viking_fs, "read_file", tracking_read_file)

        context = await session.get_context_for_assemble(token_budget=token_budget)

        assert [a["index"] for a in context["archives"]] == [3]
        assert context["stats"]["includedArchives"] == 1
        assert context["stats"]["droppedArchives"] == 2

        overview_reads = [u for u in read_uris if u.endswith(".overview.md")]
        abstract_reads = [u for u in read_uris if u.endswith(".abstract.md")]
        assert all("archive_003" in u for u in overview_reads), (
            f"Only newest archive overview should be read, got: {overview_reads}"
        )
        assert all("archive_003" in u for u in abstract_reads), (
            f"Only newest archive abstract should be read, got: {abstract_reads}"
        )
        assert not any("archive_001" in u for u in read_uris if not u.endswith(".meta.json")), (
            "Oldest archive should not be read at all (budget exhausted before reaching it)"
        )

    async def test_get_context_for_assemble_tracks_failed_archives(
        self, client: AsyncOpenViking, monkeypatch
    ):
        session = client.session(session_id="assemble_failed_archive_test")
        summaries = [
            "# Session Summary\n\narchive one",
            "# Session Summary\n\narchive two",
        ]

        async def fake_generate(_messages, latest_archive_overview=""):
            del latest_archive_overview
            return summaries.pop(0)

        monkeypatch.setattr(session, "_generate_archive_summary_async", fake_generate)

        session.add_message("user", [TextPart("turn one")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        session.add_message("user", [TextPart("turn two")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        original_read_file = session._viking_fs.read_file

        async def flaky_read_file(*args, **kwargs):
            uri = args[0] if args else kwargs.get("uri")
            if isinstance(uri, str) and uri.endswith("archive_002/.overview.md"):
                raise RuntimeError("simulated archive read failure")
            return await original_read_file(*args, **kwargs)

        monkeypatch.setattr(session._viking_fs, "read_file", flaky_read_file)

        context = await session.get_context_for_assemble(token_budget=128_000)

        assert [archive["index"] for archive in context["archives"]] == [1]
        assert context["stats"]["totalArchives"] == 2
        assert context["stats"]["includedArchives"] == 1
        assert context["stats"]["droppedArchives"] == 0
        assert context["stats"]["failedArchives"] == 1
