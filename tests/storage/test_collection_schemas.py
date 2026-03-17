# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg


class _DummyEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, text: str) -> EmbedResult:
        self.calls += 1
        return EmbedResult(dense_vector=[0.1, 0.2])


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


@pytest.mark.asyncio
async def test_embedding_handler_skip_all_work_when_manager_is_closing(monkeypatch):
    class _ClosingVikingDB:
        is_closing = True

        async def upsert(self, _data, *, ctx):  # pragma: no cover - should never run
            raise AssertionError("upsert should not be called during shutdown")

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking.storage.collection_schemas.get_openviking_config",
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
        "openviking.storage.collection_schemas.get_openviking_config",
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


# ---------------------------------------------------------------------------
# Multimodal path tests
# ---------------------------------------------------------------------------


class _MultimodalEmbedder:
    def __init__(self):
        self.embed_calls = 0
        self.embed_multimodal_calls = 0

    @property
    def supports_multimodal(self):
        return True

    def embed(self, text: str) -> EmbedResult:
        self.embed_calls += 1
        return EmbedResult(dense_vector=[0.1, 0.2])

    def embed_multimodal(self, vectorize) -> EmbedResult:
        self.embed_multimodal_calls += 1
        return EmbedResult(dense_vector=[0.9, 0.8])


class _DummyMultimodalConfig:
    def __init__(self, embedder):
        self.storage = SimpleNamespace(vectordb=SimpleNamespace(name="context"))
        self.embedding = SimpleNamespace(dimension=2, get_embedder=lambda: embedder)


def _build_media_payload(media_uri: str, media_mime_type: str) -> dict:
    msg = EmbeddingMsg(
        message="a dashboard screenshot",
        context_data={
            "id": "id-2",
            "uri": media_uri,
            "account_id": "default",
            "abstract": "screenshot",
        },
        media_uri=media_uri,
        media_mime_type=media_mime_type,
    )
    return {"data": json.dumps(msg.to_dict())}


@pytest.mark.asyncio
async def test_handler_uses_embed_multimodal_when_media_uri_present(monkeypatch):
    embedder = _MultimodalEmbedder()
    monkeypatch.setattr(
        "openviking.storage.collection_schemas.get_openviking_config",
        lambda: _DummyMultimodalConfig(embedder),
    )

    class _FakeVikingDB:
        is_closing = False

        async def upsert(self, data):
            pass

    class _FakeVikingFS:
        async def read_file_bytes(self, uri, ctx=None):
            return b"\x89PNG\r\n"

    with patch("openviking.storage.viking_fs.get_viking_fs", return_value=_FakeVikingFS()):
        handler = TextEmbeddingHandler(_FakeVikingDB())
        handler.set_callbacks(on_success=lambda: None, on_error=lambda *_: None)

        await handler.on_dequeue(
            _build_media_payload(
                media_uri="viking://agent/resources/shot.png",
                media_mime_type="image/png",
            )
        )

    assert embedder.embed_multimodal_calls == 1
    assert embedder.embed_calls == 0


@pytest.mark.asyncio
async def test_handler_falls_back_to_text_when_read_file_fails(monkeypatch):
    embedder = _MultimodalEmbedder()
    monkeypatch.setattr(
        "openviking.storage.collection_schemas.get_openviking_config",
        lambda: _DummyMultimodalConfig(embedder),
    )

    class _FakeVikingDB:
        is_closing = False

        async def upsert(self, data):
            pass

    class _FailingVikingFS:
        async def read_file_bytes(self, uri, ctx=None):
            raise OSError("file not found")

    with patch("openviking.storage.viking_fs.get_viking_fs", return_value=_FailingVikingFS()):
        handler = TextEmbeddingHandler(_FakeVikingDB())
        handler.set_callbacks(on_success=lambda: None, on_error=lambda *_: None)

        await handler.on_dequeue(
            _build_media_payload(
                media_uri="viking://agent/resources/shot.png",
                media_mime_type="image/png",
            )
        )

    assert embedder.embed_calls == 1
    assert embedder.embed_multimodal_calls == 0


@pytest.mark.asyncio
async def test_handler_rejects_media_uri_not_matching_context_uri(monkeypatch):
    """media_uri that differs from context_data['uri'] must fall back to text embed (security)."""
    embedder = _MultimodalEmbedder()
    monkeypatch.setattr(
        "openviking.storage.collection_schemas.get_openviking_config",
        lambda: _DummyMultimodalConfig(embedder),
    )

    class _FakeVikingDB:
        is_closing = False

        async def upsert(self, data):
            pass

    # media_uri points to a DIFFERENT file than context_data["uri"]
    msg = EmbeddingMsg(
        message="caption",
        context_data={"id": "x", "uri": "viking://owner/legit.png", "account_id": "default", "abstract": "a"},
        media_uri="viking://attacker/secret.png",  # DIFFERENT from uri
        media_mime_type="image/png",
    )
    payload = {"data": json.dumps(msg.to_dict())}

    handler = TextEmbeddingHandler(_FakeVikingDB())
    handler.set_callbacks(on_success=lambda: None, on_error=lambda *_: None)
    await handler.on_dequeue(payload)

    # Must fall back to text — NOT call embed_multimodal
    assert embedder.embed_multimodal_calls == 0
    assert embedder.embed_calls == 1
