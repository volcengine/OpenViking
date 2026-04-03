# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
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
from openviking.storage.vectordb.utils.data_processor import DataProcessor
from openviking.storage.viking_vector_index_backend import _SingleAccountBackend
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


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


def _build_queue_payload(*, tags=None) -> dict:
    context_data = {
        "id": "id-1",
        "uri": "viking://resources/sample",
        "account_id": "default",
        "abstract": "sample",
    }
    if tags is not None:
        context_data["tags"] = tags
    msg = EmbeddingMsg(
        message="hello",
        context_data=context_data,
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


@pytest.mark.asyncio
async def test_embedding_handler_marks_success_only_after_tracker_completion(monkeypatch):
    class _CapturingVikingDB:
        is_closing = False
        mode = "local"

        async def upsert(self, _data, *, ctx):
            return "rec-1"

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    decrement_started = asyncio.Event()
    allow_decrement_finish = asyncio.Event()

    class _FakeTracker:
        async def decrement(self, _semantic_msg_id):
            decrement_started.set()
            await allow_decrement_finish.wait()
            return 0

    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _FakeTracker(),
    )

    handler = TextEmbeddingHandler(_CapturingVikingDB())
    status = {"success": 0, "error": 0}
    handler.set_callbacks(
        on_success=lambda: status.__setitem__("success", status["success"] + 1),
        on_error=lambda *_: status.__setitem__("error", status["error"] + 1),
    )

    payload = _build_queue_payload()
    queue_data = json.loads(payload["data"])
    queue_data["semantic_msg_id"] = "semantic-1"
    payload["data"] = json.dumps(queue_data)

    task = asyncio.create_task(handler.on_dequeue(payload))
    await decrement_started.wait()

    assert status["success"] == 0
    assert status["error"] == 0

    allow_decrement_finish.set()
    await task

    assert status["success"] == 1
    assert status["error"] == 0


def test_context_collection_excludes_parent_uri():
    schema = CollectionSchemas.context_collection("ctx", 8)

    field_names = [field["FieldName"] for field in schema["Fields"]]

    assert "parent_uri" not in field_names
    assert "parent_uri" not in schema["ScalarIndex"]


def test_context_collection_uses_list_string_tags():
    schema = CollectionSchemas.context_collection("ctx", 8)
    tags_field = next(field for field in schema["Fields"] if field["FieldName"] == "tags")

    assert tags_field["FieldType"] == "list<string>"
    assert "tags" in schema["ScalarIndex"]


def test_context_collection_accepts_list_and_legacy_string_tags():
    schema = CollectionSchemas.context_collection("ctx", 8)
    fields = {field["FieldName"]: field for field in schema["Fields"]}
    processor = DataProcessor(fields, collection_name="ctx")

    processed = processor.validate_and_process(
        {
            "uri": "viking://resources/sample",
            "vector": [0.1] * 8,
            "tags": ["user:model-training", "auto:feature-store"],
        }
    )
    legacy = processor.validate_and_process(
        {
            "uri": "viking://resources/sample",
            "vector": [0.1] * 8,
            "tags": "user:model-training;auto:feature-store",
        }
    )
    empty = processor.validate_and_process(
        {
            "uri": "viking://resources/sample",
            "vector": [0.1] * 8,
            "tags": [],
        }
    )

    assert processed["tags"] == ["user:model-training", "auto:feature-store"]
    assert legacy["tags"] == ["user:model-training", "auto:feature-store"]
    assert empty["tags"] == []


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


def test_single_account_backend_filters_parent_uri_against_current_schema():
    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "uri"},
                    {"FieldName": "abstract"},
                    {"FieldName": "account_id"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    filtered = backend._filter_known_fields(
        {
            "id": "rec-1",
            "uri": "viking://resources/sample",
            "abstract": "sample",
            "account_id": "acc1",
            "parent_uri": "viking://resources",
        }
    )

    assert filtered == {
        "id": "rec-1",
        "uri": "viking://resources/sample",
        "abstract": "sample",
        "account_id": "acc1",
    }


@pytest.mark.asyncio
async def test_single_account_backend_upsert_drops_legacy_parent_uri_before_write():
    captured = {}

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "uri"},
                    {"FieldName": "abstract"},
                    {"FieldName": "active_count"},
                    {"FieldName": "account_id"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def upsert(self, data):
            captured["data"] = dict(data)
            return ["rec-legacy"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    record_id = await backend.upsert(
        {
            "id": "rec-legacy",
            "uri": "viking://resources/sample",
            "abstract": "sample",
            "active_count": 2,
            "account_id": "acc1",
            "parent_uri": "viking://resources",
        }
    )

    assert record_id == "rec-legacy"
    assert captured["data"] == {
        "id": "rec-legacy",
        "uri": "viking://resources/sample",
        "abstract": "sample",
        "active_count": 2,
        "account_id": "acc1",
    }


@pytest.mark.asyncio
async def test_single_account_backend_serializes_tag_lists_for_legacy_string_schema():
    captured = {}

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "tags", "FieldType": "string"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def upsert(self, data):
            captured["data"] = dict(data)
            return ["rec-tags"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    record_id = await backend.upsert(
        {
            "id": "rec-tags",
            "uri": "viking://resources/sample",
            "abstract": "sample",
            "tags": ["user:model-training", "auto:feature-store"],
            "account_id": "acc1",
        }
    )

    assert record_id == "rec-tags"
    assert captured["data"]["tags"] == "user:model-training;auto:feature-store"


@pytest.mark.asyncio
async def test_single_account_backend_drops_empty_tag_lists_for_legacy_string_schema():
    captured = {}

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "tags", "FieldType": "string"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def upsert(self, data):
            captured["data"] = dict(data)
            return ["rec-tags"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    record_id = await backend.upsert(
        {
            "id": "rec-tags",
            "uri": "viking://resources/sample",
            "abstract": "sample",
            "tags": [],
            "account_id": "acc1",
        }
    )

    assert record_id == "rec-tags"
    assert "tags" not in captured["data"]


@pytest.mark.asyncio
async def test_single_account_backend_keeps_tag_lists_for_list_schema():
    captured = {}

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "tags", "FieldType": "list<string>"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def upsert(self, data):
            captured["data"] = dict(data)
            return ["rec-tags"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    record_id = await backend.upsert(
        {
            "id": "rec-tags",
            "uri": "viking://resources/sample",
            "abstract": "sample",
            "tags": ["user:model-training", "auto:feature-store"],
            "account_id": "acc1",
        }
    )

    assert record_id == "rec-tags"
    assert captured["data"]["tags"] == ["user:model-training", "auto:feature-store"]


@pytest.mark.asyncio
async def test_embedding_handler_forwards_list_tags_to_backend_upsert(monkeypatch):
    captured = {}

    class _CapturingVikingDB:
        is_closing = False
        mode = "local"

        async def upsert(self, data, *, ctx):
            captured["data"] = dict(data)
            return "rec-tags"

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    handler = TextEmbeddingHandler(_CapturingVikingDB())
    result = await handler.on_dequeue(
        _build_queue_payload(tags=["user:model-training", "auto:feature-store"])
    )

    assert result is not None
    assert captured["data"]["tags"] == ["user:model-training", "auto:feature-store"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_tags", "expected_written_tags"),
    [
        (["user:model-training", "auto:feature-store"], "user:model-training;auto:feature-store"),
        ([], None),
    ],
)
async def test_embedding_handler_preserves_list_tags_while_writing_legacy_string_schema(
    monkeypatch,
    input_tags,
    expected_written_tags,
):
    captured = {}

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "vector", "FieldType": "vector"},
                    {"FieldName": "tags", "FieldType": "string"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def upsert(self, data):
            captured["data"] = dict(data)
            return ["rec-tags"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="default",
        shared_adapter=_Adapter(),
    )

    class _BackendFacade:
        is_closing = False
        mode = "local"

        async def upsert(self, data, *, ctx):
            del ctx
            return await backend.upsert(data)

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    handler = TextEmbeddingHandler(_BackendFacade())
    result = await handler.on_dequeue(_build_queue_payload(tags=input_tags))

    assert result is not None
    assert result["tags"] == input_tags
    if expected_written_tags is None:
        assert "tags" not in captured["data"]
    else:
        assert captured["data"]["tags"] == expected_written_tags
