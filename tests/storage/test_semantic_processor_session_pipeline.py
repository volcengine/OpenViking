import pytest
from unittest.mock import AsyncMock

from openviking.storage.queuefs.semantic_processor import SemanticProcessor


class _FakeVikingFS:
    def __init__(self, overview: str, abstract: str):
        self._overview = overview
        self._abstract = abstract

    async def read_file(self, uri: str, ctx=None) -> str:
        if uri.endswith("/.overview.md"):
            return self._overview
        if uri.endswith("/.abstract.md"):
            return self._abstract
        raise FileNotFoundError(uri)

    async def write_file(self, uri: str, content: str, ctx=None) -> None:
        raise AssertionError(f"Session pipeline must not write files: {uri}")


class _BoomDagExecutor:
    def __init__(self, *args, **kwargs):
        raise AssertionError("Session pipeline must not create SemanticDagExecutor")


@pytest.mark.asyncio
async def test_session_context_type_vectorizes_existing_overview_abstract(monkeypatch):
    fake_fs = _FakeVikingFS(overview="ov", abstract="ab")
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fake_fs
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor", _BoomDagExecutor
    )

    processor = SemanticProcessor(max_concurrent_llm=1)
    processor._vectorize_directory = AsyncMock()

    await processor.on_dequeue(
        {
            "uri": "viking://session/test-session",
            "context_type": "session",
            "recursive": False,
        }
    )

    processor._vectorize_directory.assert_awaited_once()
    _, kwargs = processor._vectorize_directory.await_args
    assert kwargs["uri"] == "viking://session/test-session"
    assert kwargs["context_type"] == "session"
    assert kwargs["overview"] == "ov"
    assert kwargs["abstract"] == "ab"


@pytest.mark.asyncio
async def test_session_context_type_skips_when_missing_files(monkeypatch):
    fake_fs = _FakeVikingFS(overview="", abstract="")
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fake_fs
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor", _BoomDagExecutor
    )

    processor = SemanticProcessor(max_concurrent_llm=1)
    processor._vectorize_directory = AsyncMock()

    await processor.on_dequeue(
        {
            "uri": "viking://session/test-session",
            "context_type": "session",
            "recursive": False,
        }
    )

    processor._vectorize_directory.assert_not_awaited()
