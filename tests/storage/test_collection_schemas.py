# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
from types import SimpleNamespace

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.storage.collection_schemas import TextEmbeddingHandler, _truncate_text_to_token_limit
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg


class _DummyEmbedder:
    def __init__(self):
        self.calls = 0
        self.model_name = "test-embedding-model"

    def embed(self, text: str) -> EmbedResult:
        self.calls += 1
        return EmbedResult(dense_vector=[0.1, 0.2])

    def get_dimension(self) -> int:
        return 2


class _DummyConfig:
    def __init__(self, embedder: _DummyEmbedder):
        self.storage = SimpleNamespace(vectordb=SimpleNamespace(name="context"))
        self.embedding = SimpleNamespace(
            dimension=2,
            get_embedder=lambda: embedder,
        )


def _build_queue_payload() -> dict:
    msg = EmbeddingMsg(
        message="hello",
        context_data={
            "id": "id-1",
            "uri": "viking://resources/sample",
            "account_id": "default",
            "abstract": "sample",
        },
    )
    return {"data": json.dumps(msg.to_dict())}


class _RecordingVikingDB:
    def __init__(self):
        self.is_closing = False
        self.payloads = []
        self.has_queue_manager = False

    async def upsert(self, data, *, ctx):
        self.payloads.append(data)
        return data["id"]


@pytest.mark.asyncio
async def test_embedding_handler_skip_all_work_when_manager_is_closing(monkeypatch):
    class _ClosingVikingDB:
        is_closing = True

        async def upsert(self, _data, *, ctx):  # pragma: no cover - should never run
            raise AssertionError("upsert should not be called during shutdown")

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    handler = TextEmbeddingHandler(_ClosingVikingDB())
    status = {"success": 0, "error": 0}
    handler.set_callbacks(
        on_success=lambda: status.__setitem__("success", status["success"] + 1),
        on_error=lambda *_: status.__setitem__("error", status["error"] + 1),
    )

    result = await handler.on_dequeue(_build_queue_payload())

    assert result is None
    assert embedder.calls == 0
    assert status["success"] == 1
    assert status["error"] == 0


@pytest.mark.asyncio
async def test_embedding_handler_treats_shutdown_write_lock_as_success(monkeypatch):
    class _ClosingDuringUpsertVikingDB:
        def __init__(self):
            self.is_closing = False
            self.calls = 0

        async def upsert(self, _data, *, ctx):
            self.calls += 1
            self.is_closing = True
            raise RuntimeError("IO error: lock /tmp/LOCK: already held by process")

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    vikingdb = _ClosingDuringUpsertVikingDB()
    handler = TextEmbeddingHandler(vikingdb)
    status = {"success": 0, "error": 0}
    handler.set_callbacks(
        on_success=lambda: status.__setitem__("success", status["success"] + 1),
        on_error=lambda *_: status.__setitem__("error", status["error"] + 1),
    )

    result = await handler.on_dequeue(_build_queue_payload())

    assert result is None
    assert vikingdb.calls == 1
    assert embedder.calls == 1
    assert status["success"] == 1
    assert status["error"] == 0


@pytest.mark.asyncio
async def test_embedding_handler_uses_embedder_dimension_when_config_dimension_is_stale(
    monkeypatch,
):
    class _MismatchedConfig:
        def __init__(self, embedder):
            self.storage = SimpleNamespace(vectordb=SimpleNamespace(name="context"))
            self.embedding = SimpleNamespace(
                dimension=2048,
                get_embedder=lambda: embedder,
            )

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _MismatchedConfig(embedder),
    )

    vikingdb = _RecordingVikingDB()
    handler = TextEmbeddingHandler(vikingdb)

    result = await handler.on_dequeue(_build_queue_payload())

    assert result is not None
    assert result["vector"] == [0.1, 0.2]
    assert vikingdb.payloads[0]["vector"] == [0.1, 0.2]


@pytest.mark.asyncio
async def test_embedding_handler_retries_with_truncated_text_after_token_limit_error(monkeypatch):
    token_limit_error = (
        "OpenAI API error: Error code: 400 - {'error': {'message': "
        "\"You passed 16385 input tokens and requested 0 output tokens. "
        "However, the model's context length is only 16384 tokens, resulting in "
        "a maximum input length of 16384 tokens. Please reduce the length of the "
        "input prompt. (parameter=input_tokens, value=16385)\", 'type': "
        "'BadRequestError', 'param': None, 'code': 400}}"
    )

    class _RetryingEmbedder:
        def __init__(self):
            self.calls = []
            self.model_name = "text-embedding-3-large"

        def embed(self, text: str) -> EmbedResult:
            self.calls.append(text)
            if len(self.calls) == 1:
                raise RuntimeError(token_limit_error)
            return EmbedResult(dense_vector=[0.1, 0.2])

        def get_dimension(self) -> int:
            return 2

    embedder = _RetryingEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    msg = EmbeddingMsg(
        message="token " * 20000,
        context_data={
            "id": "id-2",
            "uri": "viking://resources/large-file",
            "account_id": "default",
            "abstract": "sample",
        },
    )

    handler = TextEmbeddingHandler(_RecordingVikingDB())
    result = await handler.on_dequeue({"data": json.dumps(msg.to_dict())})

    assert result is not None
    assert len(embedder.calls) == 2
    assert len(embedder.calls[1]) < len(embedder.calls[0])


@pytest.mark.asyncio
async def test_embedding_handler_falls_back_to_summary_after_retry_exhaustion(monkeypatch):
    token_limit_error = (
        "OpenAI API error: Error code: 400 - {'error': {'message': "
        "\"You passed 32769 input tokens and requested 0 output tokens. "
        "However, the model's context length is only 32768 tokens, resulting in "
        "a maximum input length of 32768 tokens. Please reduce the length of the "
        "input prompt. (parameter=input_tokens, value=32769)\", 'type': "
        "'BadRequestError', 'param': None, 'code': 400}}"
    )

    class _AlwaysTooLongEmbedder:
        def __init__(self):
            self.calls = []
            self.model_name = "Qwen3-Embedding-0.6B"

        def embed(self, text: str) -> EmbedResult:
            self.calls.append(text)
            if text != "short summary":
                raise RuntimeError(token_limit_error)
            return EmbedResult(dense_vector=[0.1, 0.2])

        def get_dimension(self) -> int:
            return 2

    embedder = _AlwaysTooLongEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    msg = EmbeddingMsg(
        message="very long content " * 5000,
        context_data={
            "id": "id-3",
            "uri": "viking://resources/large-lockfile",
            "account_id": "default",
            "abstract": "short summary",
        },
    )

    handler = TextEmbeddingHandler(_RecordingVikingDB())
    result = await handler.on_dequeue({"data": json.dumps(msg.to_dict())})

    assert result is not None
    assert embedder.calls[-1] == "short summary"
def test_truncate_text_to_token_limit_uses_provider_observed_tokens_when_tokenizer_disagrees():
    text = "x" * 1000

    truncated = _truncate_text_to_token_limit(
        text,
        "Qwen3-Embedding-0.6B",
        900,
        observed_input_tokens=1800,
    )

    assert len(truncated) < len(text)
