# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import inspect
import json
from types import SimpleNamespace

import pytest

from openviking.models.embedder.base import EmbedResult
from openviking.storage.collection_schemas import (
    CollectionSchemas,
    TextEmbeddingHandler,
    init_context_collection,
)
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg


class _DummyEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, text: str) -> EmbedResult:
        self.calls += 1
        return EmbedResult(dense_vector=[0.1, 0.2])


class _DummyConfig:
    def __init__(self, embedder: _DummyEmbedder, backend: str = "volcengine"):
        self.storage = SimpleNamespace(vectordb=SimpleNamespace(name="context", backend=backend))
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
async def test_embedding_handler_preserves_parent_uri_for_backend_upsert_logic(monkeypatch):
    captured = {}

    class _CapturingVikingDB:
        is_closing = False
        mode = "local"

        async def upsert(self, data, *, ctx):
            captured["data"] = dict(data)
            return "rec-1"

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    handler = TextEmbeddingHandler(_CapturingVikingDB())
    payload = _build_queue_payload()
    queue_data = json.loads(payload["data"])
    queue_data["context_data"]["parent_uri"] = "viking://resources"
    payload["data"] = json.dumps(queue_data)

    result = await handler.on_dequeue(payload)

    assert result is not None
    assert "data" in captured
    assert captured["data"]["parent_uri"] == "viking://resources"


def test_context_collection_excludes_parent_uri():
    schema = CollectionSchemas.context_collection("ctx", 8)

    field_names = [field["FieldName"] for field in schema["Fields"]]

    assert "parent_uri" not in field_names
    assert "parent_uri" not in schema["ScalarIndex"]


def test_context_collection_signature_has_no_include_parent_uri():
    signature = inspect.signature(CollectionSchemas.context_collection)

    assert "include_parent_uri" not in signature.parameters


@pytest.mark.asyncio
async def test_init_context_collection_uses_backend_specific_schema(monkeypatch):
    captured = {}

    class _Storage:
        async def create_collection(self, name, schema):
            captured["name"] = name
            captured["schema"] = schema
            return True

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder, backend="volcengine"),
    )

    created = await init_context_collection(_Storage())

    assert created is True
    field_names = [field["FieldName"] for field in captured["schema"]["Fields"]]
    assert "parent_uri" not in field_names
    assert "parent_uri" not in captured["schema"]["ScalarIndex"]


@pytest.mark.asyncio
async def test_init_context_collection_excludes_parent_uri_for_local_backend(monkeypatch):
    captured = {}

    class _Storage:
        async def create_collection(self, name, schema):
            captured["name"] = name
            captured["schema"] = schema
            return True

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder, backend="local"),
    )

    created = await init_context_collection(_Storage())

    assert created is True
    field_names = [field["FieldName"] for field in captured["schema"]["Fields"]]
    assert "parent_uri" not in field_names
    assert "parent_uri" not in captured["schema"]["ScalarIndex"]
