# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import hashlib
import inspect
import json
import logging
from types import SimpleNamespace

import pytest

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server.identity import RequestContext, Role, UserIdentifier
from openviking.storage.collection_schemas import (
    CollectionSchemas,
    TextEmbeddingHandler,
    _build_embedding_metadata,
    init_context_collection,
)
from openviking.storage.expr import Eq
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.vectordb import engine as vectordb_engine
from openviking.storage.vectordb.collection.result import UpsertDataResult
from openviking.storage.vectordb_adapters.base import (
    VIKINGDB_TEXT_FIELD_BYTE_LIMIT,
    _truncate_text_field,
)
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter
from openviking.storage.viking_vector_index_backend import (
    VIKINGDB_CONTENT_MAX_SIZE,
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.utils.config.vectordb_config import (
    QdrantConfig,
    VectorDBBackendConfig,
    VolcengineConfig,
)


class _DummyEmbedder:
    def __init__(self):
        self.calls = 0

    def prepare_embedding_input(self, content):
        return content

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        del is_query
        self.calls += 1
        return EmbedResult(dense_vector=[0.1, 0.2])

    async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
        return self.embed(text, is_query=is_query)


class _DummyConfig:
    def __init__(
        self,
        embedder: _DummyEmbedder,
        backend: str = "volcengine",
        volcengine_data_api_key: str | None = None,
        max_input_tokens: int = 4096,
    ):
        self.storage = SimpleNamespace(
            vectordb=SimpleNamespace(
                name="context",
                backend=backend,
                volcengine=SimpleNamespace(api_key=volcengine_data_api_key),
            )
        )
        self.log = SimpleNamespace(
            output="stdout",
            rotation=False,
            rotation_interval="midnight",
            rotation_days=3,
        )
        self.embedding = SimpleNamespace(
            dimension=2,
            get_embedder=lambda: embedder,
            dense=SimpleNamespace(
                provider="local",
                model="bge-small-zh-v1.5-f16",
                model_path=None,
            ),
            sparse=None,
            hybrid=None,
            max_input_tokens=max_input_tokens,
            circuit_breaker=SimpleNamespace(
                failure_threshold=5,
                reset_timeout=60.0,
                max_reset_timeout=600.0,
            ),
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


def _build_queue_payload_for_account(account_id: str) -> dict:
    msg = EmbeddingMsg(
        message="hello",
        context_data={
            "id": "id-1",
            "uri": "viking://resources/sample",
            "account_id": str(account_id),
            "abstract": "sample",
        },
        telemetry_id="telemetry-1",
    )
    return {"data": json.dumps(msg.to_dict())}


def test_embedding_handler_builds_circuit_breaker_from_config(monkeypatch):
    class _DummyVikingDB:
        is_closing = False

    embedder = _DummyEmbedder()
    config = _DummyConfig(embedder)
    config.embedding.circuit_breaker = SimpleNamespace(
        failure_threshold=7,
        reset_timeout=60.0,
        max_reset_timeout=600.0,
    )
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: config,
    )

    handler = TextEmbeddingHandler(_DummyVikingDB())

    assert handler._circuit_breaker._failure_threshold == 7
    assert handler._circuit_breaker._base_reset_timeout == 60.0
    assert handler._circuit_breaker._max_reset_timeout == 600.0


@pytest.mark.asyncio
async def test_init_context_collection_writes_embedding_metadata(monkeypatch):
    captured = {}

    class _FakeStorage:
        async def create_collection(self, name, schema):
            captured["name"] = name
            captured["schema"] = schema
            return True

    config = _DummyConfig(_DummyEmbedder())
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: config,
    )

    created = await init_context_collection(_FakeStorage())

    assert created is True
    description = captured["schema"]["Description"]
    assert "[openviking.embedding]" in description
    assert '"provider": "local"' in description
    assert '"model": "bge-small-zh-v1.5-f16"' in description


@pytest.mark.asyncio
async def test_init_context_collection_backfills_metadata_for_empty_legacy_collection(monkeypatch):
    updates = []

    class _FakeStorage:
        async def create_collection(self, name, schema):
            del name, schema
            return False

        async def get_collection_meta(self):
            return {"Description": "Unified context collection"}

        async def count(self):
            return 0

        async def update_collection_description(self, description):
            updates.append(description)
            return True

    config = _DummyConfig(_DummyEmbedder())
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: config,
    )

    created = await init_context_collection(_FakeStorage())

    assert created is False
    assert len(updates) == 1
    assert '"provider": "local"' in updates[0]


@pytest.mark.asyncio
async def test_init_context_collection_warns_on_mismatched_nonempty_collection(monkeypatch):
    """When embedding metadata mismatches for a non-empty collection, the function
    logs a warning and returns False (does not raise)."""

    class _FakeStorage:
        async def create_collection(self, name, schema):
            del name, schema
            return False

        async def get_collection_meta(self):
            return {
                "Description": (
                    "Unified context collection\n\n[openviking.embedding]\n"
                    '{"dimension": 1024, "model": "text-embedding-3-small", '
                    '"model_identity": "text-embedding-3-small", "provider": "openai"}'
                )
            }

        async def count(self):
            return 3

        async def update_collection_description(self, description):  # pragma: no cover
            del description
            raise AssertionError("should not update mismatched non-empty collection")

    config = _DummyConfig(_DummyEmbedder())
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: config,
    )

    result = await init_context_collection(_FakeStorage())
    assert result is False


def test_build_embedding_metadata_hashes_resolved_local_model_path(tmp_path):
    model_path = tmp_path / ".." / tmp_path.name / "model.gguf"
    expected = str(model_path.expanduser().resolve())
    config = _DummyConfig(_DummyEmbedder())
    config.embedding.dense.model_path = str(model_path)

    payload = _build_embedding_metadata(config)

    assert payload["provider"] == "local"
    assert payload["model"] == "bge-small-zh-v1.5-f16"
    assert payload["model_identity"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()


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
    status = {"success": 0, "requeue": 0, "error": 0}
    handler.set_callbacks(
        on_success=lambda: status.__setitem__("success", status["success"] + 1),
        on_requeue=lambda: status.__setitem__("requeue", status["requeue"] + 1),
        on_error=lambda *_: status.__setitem__("error", status["error"] + 1),
    )

    result = await handler.on_dequeue(_build_queue_payload())

    assert result is None
    assert embedder.calls == 0
    assert status["success"] == 1
    assert status["requeue"] == 0
    assert status["error"] == 0


@pytest.mark.asyncio
async def test_embedding_handler_open_breaker_logs_summary_instead_of_per_item_warning(
    monkeypatch, caplog
):
    from openviking.utils.circuit_breaker import CircuitBreakerOpen

    class _QueueingVikingDB:
        is_closing = False
        has_queue_manager = True

        def __init__(self):
            self.enqueued = []

        async def enqueue_embedding_msg(self, msg):
            self.enqueued.append(msg.id)
            return None

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    handler = TextEmbeddingHandler(_QueueingVikingDB())
    status = {"success": 0, "requeue": 0, "error": 0}
    handler.set_callbacks(
        on_success=lambda: status.__setitem__("success", status["success"] + 1),
        on_requeue=lambda: status.__setitem__("requeue", status["requeue"] + 1),
        on_error=lambda *_: status.__setitem__("error", status["error"] + 1),
    )
    monkeypatch.setattr(
        handler._circuit_breaker,
        "check",
        lambda: (_ for _ in ()).throw(CircuitBreakerOpen("open")),
    )

    import openviking.storage.collection_schemas as collection_schemas

    collection_schemas.logger.addHandler(caplog.handler)
    collection_schemas.logger.setLevel(logging.WARNING)
    try:
        with caplog.at_level(logging.WARNING):
            await handler.on_dequeue(_build_queue_payload())
            await handler.on_dequeue(_build_queue_payload())
    finally:
        collection_schemas.logger.removeHandler(caplog.handler)

    warnings = [record.message for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings.count("Embedding circuit breaker is open; re-enqueueing messages") == 1
    assert status == {"success": 2, "requeue": 2, "error": 0}


@pytest.mark.asyncio
async def test_embedding_handler_treats_shutdown_write_lock_as_success(monkeypatch):
    class _ClosingDuringUpsertVikingDB:
        def __init__(self):
            self.is_closing = False
            self.calls = 0

        async def upsert(self, _data, *, ctx, partial_update=False):
            assert partial_update is True
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
    status = {"success": 0, "requeue": 0, "error": 0}
    handler.set_callbacks(
        on_success=lambda: status.__setitem__("success", status["success"] + 1),
        on_requeue=lambda: status.__setitem__("requeue", status["requeue"] + 1),
        on_error=lambda *_: status.__setitem__("error", status["error"] + 1),
    )

    result = await handler.on_dequeue(_build_queue_payload())

    assert result is None
    assert vikingdb.calls == 1
    assert embedder.calls == 1
    assert status["success"] == 1
    assert status["requeue"] == 0
    assert status["error"] == 0


@pytest.mark.asyncio
async def test_embedding_handler_propagates_account_id_on_success(monkeypatch):
    class _DummyVikingDB:
        is_closing = False

        async def upsert(self, _data, *, ctx):
            return None

    captured: dict[str, object] = {}
    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )
    monkeypatch.setattr(
        "openviking.metrics.datasources.EmbeddingEventDataSource.record_success",
        staticmethod(lambda **kwargs: captured.update(kwargs)),
    )

    handler = TextEmbeddingHandler(_DummyVikingDB())
    await handler.on_dequeue(_build_queue_payload_for_account("acct-embed-success"))

    assert captured["account_id"] == "acct-embed-success"


@pytest.mark.asyncio
async def test_embedding_handler_propagates_account_id_on_error(monkeypatch):
    class _DummyVikingDB:
        is_closing = False
        has_queue_manager = False

    class _BrokenEmbedder:
        def prepare_embedding_input(self, content):
            return content

        def embed(self, text: str) -> EmbedResult:
            raise RuntimeError("boom")

        async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
            del is_query
            return self.embed(text)

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(_BrokenEmbedder()),
    )
    monkeypatch.setattr(
        "openviking.metrics.datasources.EmbeddingEventDataSource.record_error",
        staticmethod(lambda **kwargs: captured.update(kwargs)),
    )
    monkeypatch.setattr(
        "openviking.storage.collection_schemas.classify_api_error",
        lambda _err: "unknown",
    )

    handler = TextEmbeddingHandler(_DummyVikingDB())
    await handler.on_dequeue(_build_queue_payload_for_account("acct-embed-error"))

    assert captured["account_id"] == "acct-embed-error"


@pytest.mark.asyncio
async def test_embedding_handler_truncates_queue_input_before_embed(monkeypatch):
    class _CapturingVikingDB:
        is_closing = False

        async def upsert(self, _data, *, ctx):
            return "rec-1"

    class _CapturingEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__("capturing-test", config={"max_input_tokens": 10})
            self.text = None

        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            del is_query
            self.text = text
            return EmbedResult(dense_vector=[0.1, 0.2])

        def get_dimension(self) -> int:
            return 2

    embedder = _CapturingEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder, max_input_tokens=10),
    )

    handler = TextEmbeddingHandler(_CapturingVikingDB())
    payload = _build_queue_payload()
    queue_data = json.loads(payload["data"])
    queue_data["message"] = " ".join(f"token-{idx}" for idx in range(200))
    payload["data"] = json.dumps(queue_data)

    await handler.on_dequeue(payload)

    assert embedder.text is not None
    assert embedder.text.endswith("...(truncated for embedding)")
    assert "token-199" not in embedder.text


@pytest.mark.asyncio
async def test_embedding_handler_drops_input_too_large_without_requeue(monkeypatch):
    class _QueueingVikingDB:
        is_closing = False
        has_queue_manager = True

        def __init__(self):
            self.enqueued = []

        async def enqueue_embedding_msg(self, msg):
            self.enqueued.append(msg)
            return None

    class _OversizedInputEmbedder:
        def prepare_embedding_input(self, content):
            return content

        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            del text, is_query
            raise RuntimeError("Malformed input request: expected maxLength: 50000, actual: 75000")

        async def embed_async(self, text: str, is_query: bool = False) -> EmbedResult:
            return self.embed(text, is_query=is_query)

    vikingdb = _QueueingVikingDB()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(_OversizedInputEmbedder()),
    )

    handler = TextEmbeddingHandler(vikingdb)
    status = {"success": 0, "requeue": 0, "error": 0}
    handler.set_callbacks(
        on_success=lambda: status.__setitem__("success", status["success"] + 1),
        on_requeue=lambda: status.__setitem__("requeue", status["requeue"] + 1),
        on_error=lambda *_: status.__setitem__("error", status["error"] + 1),
    )

    result = await handler.on_dequeue(_build_queue_payload())

    assert result is None
    assert vikingdb.enqueued == []
    assert status == {"success": 0, "requeue": 0, "error": 1}
    assert handler._circuit_breaker._failure_count == 0


@pytest.mark.asyncio
async def test_embedding_handler_preserves_parent_uri_for_backend_upsert_logic(monkeypatch):
    captured = {}

    class _CapturingVikingDB:
        is_closing = False
        mode = "local"

        async def upsert(self, data, *, ctx, partial_update=False):
            assert partial_update is True
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

        async def upsert(self, _data, *, ctx, partial_update=False):
            assert partial_update is True
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
    status = {"success": 0, "requeue": 0, "error": 0}
    handler.set_callbacks(
        on_success=lambda: status.__setitem__("success", status["success"] + 1),
        on_requeue=lambda: status.__setitem__("requeue", status["requeue"] + 1),
        on_error=lambda *_: status.__setitem__("error", status["error"] + 1),
    )

    payload = _build_queue_payload()
    queue_data = json.loads(payload["data"])
    queue_data["semantic_msg_id"] = "semantic-1"
    payload["data"] = json.dumps(queue_data)

    task = asyncio.create_task(handler.on_dequeue(payload))
    await decrement_started.wait()

    assert status["success"] == 0
    assert status["requeue"] == 0
    assert status["error"] == 0

    allow_decrement_finish.set()
    await task

    assert status["success"] == 1
    assert status["requeue"] == 0
    assert status["error"] == 0


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


@pytest.mark.asyncio
async def test_init_context_collection_skips_bootstrap_for_api_key_auth_mode_on_volcengine(
    monkeypatch,
):
    class _Storage:
        async def create_collection(self, name, schema):  # pragma: no cover
            del name, schema
            raise AssertionError("create_collection should not be called for data-plane backend")

        async def get_collection_meta(self):  # pragma: no cover
            raise AssertionError("get_collection_meta should not be called for data-plane backend")

        async def update_collection_description(self, description):  # pragma: no cover
            del description
            raise AssertionError(
                "update_collection_description should not be called for data-plane backend"
            )

    embedder = _DummyEmbedder()
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(
            embedder,
            backend="volcengine",
            volcengine_data_api_key="vk-test-token",
        ),
    )

    created = await init_context_collection(_Storage())

    assert created is False


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
async def test_single_account_backend_truncates_content_only_at_vector_write():
    captured = {}
    full_content = "x" * (1024 * 1024 + 17)

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "uri"},
                    {"FieldName": "abstract"},
                    {"FieldName": "content", "FieldType": "text"},
                    {"FieldName": "account_id"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def upsert(self, data):
            captured["data"] = dict(data)
            return [data["id"]]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )
    source_data = {
        "id": "rec-large",
        "uri": "viking://resources/large.txt",
        "abstract": "sample",
        "content": full_content,
        "account_id": "acc1",
    }

    record_id = await backend.upsert(source_data)

    assert record_id == "rec-large"
    assert source_data["content"] == full_content
    assert VIKINGDB_CONTENT_MAX_SIZE == 1024 * 1024
    assert captured["data"]["content"] == full_content[:VIKINGDB_CONTENT_MAX_SIZE]


def test_vikingdb_text_field_byte_limit_is_one_mb_and_utf8_safe():
    text = "a" * (1024 * 1024) + "😀"

    truncated = _truncate_text_field(text)

    assert VIKINGDB_TEXT_FIELD_BYTE_LIMIT == 1024 * 1024
    assert len(truncated.encode("utf-8")) == VIKINGDB_TEXT_FIELD_BYTE_LIMIT
    assert truncated == "a" * VIKINGDB_TEXT_FIELD_BYTE_LIMIT


@pytest.mark.asyncio
async def test_single_account_backend_collection_exists_runs_in_threadpool(monkeypatch):
    called = {}

    class _Adapter:
        mode = "local"

        def collection_exists(self):
            return True

    async def _fake_to_thread(func, /, *args, **kwargs):
        called["func"] = func
        called["args"] = args
        called["kwargs"] = kwargs
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.asyncio.to_thread", _fake_to_thread
    )

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    assert await backend.collection_exists() is True
    assert called["func"].__self__ is backend._adapter
    assert called["func"].__name__ == "collection_exists"
    assert called["args"] == ()
    assert called["kwargs"] == {}


@pytest.mark.asyncio
async def test_single_account_backend_upsert_runs_adapter_in_threadpool(monkeypatch):
    calls = []

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

        def upsert(self, data):
            return [data["id"]]

    async def _fake_to_thread(func, /, *args, **kwargs):
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.asyncio.to_thread", _fake_to_thread
    )

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    record_id = await backend.upsert(
        {
            "id": "rec-1",
            "uri": "viking://resources/sample",
            "abstract": "sample",
            "account_id": "acc1",
            "unknown": "legacy",
        }
    )

    assert record_id == "rec-1"
    assert [call[0] for call in calls] == ["_prepare_upsert_payload", "upsert"]
    assert calls[-1][1] == (
        {
            "id": "rec-1",
            "uri": "viking://resources/sample",
            "abstract": "sample",
            "account_id": "acc1",
        },
    )


@pytest.mark.asyncio
async def test_single_account_backend_update_runs_adapter_in_threadpool(monkeypatch):
    calls = []

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

        def update_data(self, data):
            return [data[0]["id"]]

    async def _fake_to_thread(func, /, *args, **kwargs):
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.asyncio.to_thread", _fake_to_thread
    )

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.update(
        {
            "id": "rec-1",
            "uri": "viking://resources/sample",
            "abstract": "sample",
            "account_id": "acc1",
            "unknown": "legacy",
        }
    )

    assert result.ok is True
    assert result.ids == ["rec-1"]
    assert result.updated_count == 1
    assert result.error_code is None
    assert result.error_message is None
    assert [call[0] for call in calls] == ["_prepare_upsert_payload", "update_data"]
    assert calls[-1][1] == (
        [
            {
                "id": "rec-1",
                "uri": "viking://resources/sample",
                "abstract": "sample",
                "account_id": "acc1",
            }
        ],
    )


@pytest.mark.asyncio
async def test_local_backend_update_preserves_omitted_fields_end_to_end(tmp_path):
    if not getattr(vectordb_engine, "PersistStore", None):
        pytest.skip("local persistent vectordb engine is not available in this environment")

    backend = VikingVectorIndexBackend(
        config=VectorDBBackendConfig(
            backend="local",
            name="context",
            dimension=4,
            path=str(tmp_path),
        )
    )
    ctx = SimpleNamespace(account_id="acc1")

    created = await backend.create_collection(
        "context", CollectionSchemas.context_collection("context", 4)
    )
    assert created is True

    record_id = await backend.upsert(
        {
            "id": "rec-1",
            "uri": "viking://resources/sample",
            "account_id": "acc1",
            "abstract": "before",
            "name": "keep-me",
            "vector": [0.1, 0.2, 0.3, 0.4],
        },
        ctx=ctx,
    )

    assert record_id == "rec-1"

    result = await backend.update(
        {
            "id": "rec-1",
            "account_id": "acc1",
            "abstract": "after",
        },
        ctx=ctx,
    )

    assert result.ok is True
    assert result.ids == ["rec-1"]
    assert result.updated_count == 1

    records = await backend.get(["rec-1"], ctx=ctx)

    assert len(records) == 1
    assert records[0]["abstract"] == "after"
    assert records[0]["name"] == "keep-me"
    assert records[0]["account_id"] == "acc1"
    assert records[0]["uri"] == "viking://resources/sample"
    assert records[0]["vector"] == pytest.approx([0.1, 0.2, 0.3, 0.4])


@pytest.mark.asyncio
async def test_local_backend_update_can_clear_string_field_end_to_end(tmp_path):
    if not getattr(vectordb_engine, "PersistStore", None):
        pytest.skip("local persistent vectordb engine is not available in this environment")

    backend = VikingVectorIndexBackend(
        config=VectorDBBackendConfig(
            backend="local",
            name="context",
            dimension=4,
            path=str(tmp_path),
        )
    )
    ctx = SimpleNamespace(account_id="acc1")

    created = await backend.create_collection(
        "context", CollectionSchemas.context_collection("context", 4)
    )
    assert created is True

    record_id = await backend.upsert(
        {
            "id": "rec-1",
            "uri": "viking://resources/sample",
            "account_id": "acc1",
            "name": "keep-me",
            "tags": "alpha,beta",
            "vector": [0.1, 0.2, 0.3, 0.4],
        },
        ctx=ctx,
    )

    assert record_id == "rec-1"

    result = await backend.update(
        {
            "id": "rec-1",
            "account_id": "acc1",
            "tags": "",
        },
        ctx=ctx,
    )

    assert result.ok is True
    assert result.ids == ["rec-1"]
    assert result.updated_count == 1

    records = await backend.get(["rec-1"], ctx=ctx)

    assert len(records) == 1
    assert records[0]["tags"] == ""
    assert records[0]["name"] == "keep-me"
    assert records[0]["vector"] == pytest.approx([0.1, 0.2, 0.3, 0.4])


def test_local_collection_adapter_update_data_returns_ids():
    adapter = LocalCollectionAdapter(
        collection_name="context", project_path="", index_name="default"
    )

    class _Collection:
        def update_data(self, data_list):
            assert data_list == [{"id": "doc-1", "name": "updated"}]
            return UpsertDataResult(ids=["doc-1"])

    adapter._collection = _Collection()

    result = adapter.update_data([{"id": "doc-1", "name": "updated"}])

    assert result == ["doc-1"]


@pytest.mark.asyncio
async def test_single_account_backend_update_injects_bound_account_id(monkeypatch):
    calls = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "abstract"},
                    {"FieldName": "account_id"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def update_data(self, data):
            calls.append(("update_data_payload", data))
            return [data[0]["id"]]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.update({"id": "rec-1", "abstract": "patched"})

    assert result.ok is True
    assert result.ids == ["rec-1"]
    assert result.updated_count == 1
    assert calls == [
        (
            "update_data_payload",
            [{"id": "rec-1", "abstract": "patched", "account_id": "acc1"}],
        )
    ]


@pytest.mark.asyncio
async def test_single_account_backend_update_requires_id_before_adapter_call():
    class _Collection:
        def get_meta_data(self):
            return {"Fields": [{"FieldName": "id"}, {"FieldName": "account_id"}]}

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def update_data(self, data):  # pragma: no cover - should never run
            raise AssertionError("update_data should not be called without id")

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.update({"abstract": "patched"})

    assert result.ok is False
    assert result.ids == []
    assert result.updated_count == 0
    assert result.error_code == "INVALID_ARGUMENT"
    assert "id is required for update" in (result.error_message or "")


@pytest.mark.asyncio
async def test_single_account_backend_update_rejects_invalid_context_type_without_adapter_call():
    calls = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "abstract"},
                    {"FieldName": "account_id"},
                    {"FieldName": "context_type"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def update_data(self, data):  # pragma: no cover - should never run
            calls.append(data)
            raise AssertionError("update_data should not be called for invalid context_type")

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.update(
        {
            "id": "rec-1",
            "abstract": "patched",
            "context_type": "not-a-real-type",
        }
    )

    assert result.ok is False
    assert result.ids == []
    assert result.updated_count == 0
    assert result.error_code == "INVALID_ARGUMENT"
    assert "Invalid context_type" in (result.error_message or "")
    assert calls == []


@pytest.mark.asyncio
async def test_single_account_backend_update_returns_structured_error_when_adapter_update_fails():
    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "abstract"},
                    {"FieldName": "account_id"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def update_data(self, data):
            del data
            raise RuntimeError("backend exploded")

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.update({"id": "rec-1", "abstract": "patched"})

    assert result.ok is False
    assert result.ids == []
    assert result.updated_count == 0
    assert result.error_code == "UPDATE_FAILED"
    assert "backend exploded" in (result.error_message or "")


@pytest.mark.asyncio
async def test_single_account_backend_update_returns_not_found_when_adapter_reports_missing_record():
    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "abstract"},
                    {"FieldName": "account_id"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def update_data(self, data):
            del data
            raise ValueError("record not found for primary key(s): ['rec-404']")

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.update({"id": "rec-404", "abstract": "patched"})

    assert result.ok is False
    assert result.ids == []
    assert result.updated_count == 0
    assert result.error_code == "NOT_FOUND"
    assert "record not found" in (result.error_message or "")


@pytest.mark.asyncio
async def test_single_account_backend_upsert_partial_update_reads_then_upserts_existing_record():
    calls = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def get(self, ids):
            calls.append(("get", ids))
            return [
                {
                    "id": "rec-1",
                    "abstract": "before",
                    "account_id": "acc1",
                    "uri": "viking://resources/old",
                }
            ]

        def upsert(self, data):
            calls.append(("upsert", data))
            return ["rec-1"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.upsert(
        {"id": "rec-1", "abstract": "patched"},
        partial_update=True,
    )

    assert result == "rec-1"
    assert calls == [
        ("get", ["rec-1"]),
        (
            "upsert",
            {
                "id": "rec-1",
                "abstract": "patched",
                "account_id": "acc1",
                "uri": "viking://resources/old",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_single_account_backend_upsert_partial_update_creates_when_record_does_not_exist():
    calls = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "vector", "FieldType": "vector", "Dim": 2},
                    {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
                    {"FieldName": "active_count", "FieldType": "int64"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def get(self, ids):
            calls.append(("get", ids))
            return []

        def upsert(self, data):
            calls.append(("upsert", data))
            return ["rec-404"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.upsert(
        {
            "id": "rec-404",
            "uri": "viking://resources/new",
            "abstract": "created",
            "unknown": "ignored",
        },
        partial_update=True,
    )

    assert result == "rec-404"
    assert calls[0] == (
        "get",
        ["rec-404"],
    )
    assert calls[1] == (
        "upsert",
        {
            "id": "rec-404",
            "uri": "viking://resources/new",
            "abstract": "created",
            "account_id": "acc1",
        },
    )


@pytest.mark.asyncio
async def test_single_account_backend_upsert_partial_update_returns_empty_when_get_fails():
    class _Adapter:
        mode = "local"

        def get(self, ids):
            del ids
            raise RuntimeError("backend exploded")

        def upsert(self, data):  # pragma: no cover - should never run
            raise AssertionError("upsert should not be called when get fails")

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.upsert(
        {"id": "rec-1", "abstract": "patched"},
        partial_update=True,
    )

    assert result == ""


@pytest.mark.asyncio
async def test_single_account_backend_upsert_without_partial_update_keeps_legacy_upsert_behavior():
    calls = []

    class _Adapter:
        mode = "local"

        def upsert(self, data):
            calls.append(data)
            return ["rec-1"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.upsert({"id": "rec-1", "abstract": "patched"})

    assert result == "rec-1"
    assert calls == [{"id": "rec-1", "abstract": "patched", "account_id": "acc1"}]


@pytest.mark.asyncio
async def test_viking_vector_index_backend_upsert_partial_update_delegates_to_account_backend():
    backend = VikingVectorIndexBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2)
    )
    ctx = SimpleNamespace(account_id="acc1")
    calls = []

    class _BoundBackend:
        async def upsert(self, data, partial_update=False):
            calls.append((data, partial_update))
            return data["id"]

    backend._get_backend_for_context = lambda _ctx: _BoundBackend()

    result = await backend.upsert(
        {"id": "rec-1", "abstract": "patched"},
        ctx=ctx,
        partial_update=True,
    )

    assert result == "rec-1"
    assert calls == [({"id": "rec-1", "abstract": "patched"}, True)]


@pytest.mark.asyncio
async def test_vikingdb_manager_proxy_upsert_partial_update_forwards_bound_context():
    ctx = SimpleNamespace(account_id="acc1")
    captured = {}

    class _Manager:
        collection_name = "context"
        mode = "local"
        queue_manager = None
        embedding_queue = None
        has_queue_manager = False
        is_closing = False

        async def upsert(self, data, *, ctx, partial_update=False):
            captured["data"] = data
            captured["ctx"] = ctx
            captured["partial_update"] = partial_update
            return data["id"]

    from openviking.storage.vikingdb_manager import VikingDBManagerProxy

    proxy = VikingDBManagerProxy(_Manager(), ctx)
    result = await proxy.upsert(
        {"id": "rec-1", "abstract": "patched"},
        partial_update=True,
    )

    assert result == "rec-1"
    assert captured == {
        "data": {"id": "rec-1", "abstract": "patched"},
        "ctx": ctx,
        "partial_update": True,
    }


@pytest.mark.asyncio
async def test_qdrant_backend_upsert_partial_update_reads_then_upserts_existing_record():
    calls = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "qdrant"

        def get(self, ids):
            calls.append(("get", ids))
            return [
                {
                    "id": "doc-1",
                    "uri": "viking://resources/qdrant",
                    "abstract": "before",
                    "account_id": "acc1",
                }
            ]

        def upsert(self, data):
            calls.append(("upsert", data))
            return ["doc-1"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(
            backend="qdrant",
            name="context",
            dimension=2,
            qdrant=QdrantConfig(url="http://qdrant:6333"),
        ),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.upsert(
        {"id": "doc-1", "uri": "viking://resources/qdrant", "abstract": "patched"},
        partial_update=True,
    )

    assert result == "doc-1"
    assert calls == [
        (
            "get",
            ["doc-1"],
        ),
        (
            "upsert",
            {
                "id": "doc-1",
                "uri": "viking://resources/qdrant",
                "abstract": "patched",
                "account_id": "acc1",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_qdrant_backend_upsert_partial_update_creates_when_record_does_not_exist():
    calls = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "vector", "FieldType": "vector", "Dim": 2},
                    {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
                    {"FieldName": "active_count", "FieldType": "int64"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "qdrant"

        def get(self, ids):
            calls.append(("get", ids))
            return []

        def upsert(self, data):
            calls.append(("upsert", data))
            return ["doc-404"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(
            backend="qdrant",
            name="context",
            dimension=2,
            qdrant=QdrantConfig(url="http://qdrant:6333"),
        ),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.upsert(
        {"id": "doc-404", "uri": "viking://resources/qdrant/new", "abstract": "created"},
        partial_update=True,
    )

    assert result == "doc-404"
    assert calls[0] == (
        "get",
        ["doc-404"],
    )
    assert calls[1] == (
        "upsert",
        {
            "id": "doc-404",
            "uri": "viking://resources/qdrant/new",
            "abstract": "created",
            "account_id": "acc1",
        },
    )


@pytest.mark.asyncio
async def test_volcengine_backend_upsert_partial_update_reads_then_upserts_existing_record():
    calls = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "volcengine"

        def get(self, ids):
            calls.append(("get", ids))
            return [
                {
                    "id": "doc-1",
                    "uri": "viking://resources/volc",
                    "abstract": "before",
                    "account_id": "acc1",
                }
            ]

        def upsert(self, data):
            calls.append(("upsert", data))
            return ["doc-1"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(
            backend="volcengine",
            name="context",
            dimension=2,
            volcengine=VolcengineConfig(ak="ak", sk="sk", region="cn-beijing"),
        ),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.upsert(
        {"id": "doc-1", "uri": "viking://resources/volc", "abstract": "patched"},
        partial_update=True,
    )

    assert result == "doc-1"
    assert calls == [
        (
            "get",
            ["doc-1"],
        ),
        (
            "upsert",
            {
                "id": "doc-1",
                "uri": "viking://resources/volc",
                "abstract": "patched",
                "account_id": "acc1",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_volcengine_backend_upsert_partial_update_creates_when_record_does_not_exist():
    calls = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "uri", "FieldType": "path"},
                    {"FieldName": "abstract", "FieldType": "string"},
                    {"FieldName": "vector", "FieldType": "vector", "Dim": 2},
                    {"FieldName": "sparse_vector", "FieldType": "sparse_vector"},
                    {"FieldName": "active_count", "FieldType": "int64"},
                    {"FieldName": "account_id", "FieldType": "string"},
                ]
            }

    class _Adapter:
        mode = "volcengine"

        def get(self, ids):
            calls.append(("get", ids))
            return []

        def upsert(self, data):
            calls.append(("upsert", data))
            return ["doc-404"]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(
            backend="volcengine",
            name="context",
            dimension=2,
            volcengine=VolcengineConfig(ak="ak", sk="sk", region="cn-beijing"),
        ),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.upsert(
        {"id": "doc-404", "uri": "viking://resources/volc/new", "abstract": "created"},
        partial_update=True,
    )

    assert result == "doc-404"
    assert calls[0] == (
        "get",
        ["doc-404"],
    )
    assert calls[1] == (
        "upsert",
        {
            "id": "doc-404",
            "uri": "viking://resources/volc/new",
            "abstract": "created",
            "account_id": "acc1",
        },
    )


@pytest.mark.asyncio
async def test_viking_vector_index_backend_update_search_tags_updates_exact_uri_only():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    backend = object.__new__(VikingVectorIndexBackend)
    calls = {"fetch_by_uri": [], "upsert": []}

    resource_uri = "viking://resources/demo/doc.md"

    async def _fake_fetch_by_uri(uri, *, ctx):
        calls["fetch_by_uri"].append((uri, ctx.account_id))
        return {"id": "root-id", "uri": resource_uri, "search_tags": ["old=root"]}

    async def _fake_upsert(data, *, ctx, partial_update=False):
        del ctx, partial_update
        calls["upsert"].append(dict(data))
        return data["id"]

    backend.fetch_by_uri = _fake_fetch_by_uri
    backend.upsert = _fake_upsert

    updated = await backend.update_search_tags(
        resource_uri,
        ["team=search"],
        mode="append",
        ctx=ctx,
    )

    assert updated == [
        {"id": "root-id", "uri": resource_uri, "search_tags": ["old=root", "team=search"]}
    ]
    assert calls["fetch_by_uri"] == [(resource_uri, ctx.account_id)]
    assert calls["upsert"] == [
        {"id": "root-id", "uri": resource_uri, "search_tags": ["old=root", "team=search"]}
    ]


@pytest.mark.asyncio
async def test_update_search_tags_for_leaf_uri_queries_exact_uri_only(monkeypatch):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    overview_uri = "viking://resources/demo/doc.md/.overview.md"
    calls = {"fetch_by_uri": [], "upsert": []}

    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)

    async def _fake_fetch_by_uri(uri, *, ctx):
        calls["fetch_by_uri"].append((uri, ctx.account_id))
        assert uri == overview_uri
        return {"id": "overview-id", "uri": overview_uri, "search_tags": ["existing=1"]}

    async def _fake_upsert(data, *, ctx, partial_update=False):
        del ctx, partial_update
        calls["upsert"].append(dict(data))
        return data["id"]

    backend.fetch_by_uri = _fake_fetch_by_uri
    backend.upsert = _fake_upsert

    updated = await backend.update_search_tags(
        overview_uri,
        ["team=search"],
        mode="append",
        ctx=ctx,
    )

    assert updated == [
        {"id": "overview-id", "uri": overview_uri, "search_tags": ["existing=1", "team=search"]}
    ]
    assert calls["fetch_by_uri"] == [(overview_uri, ctx.account_id)]
    assert calls["upsert"] == [
        {"id": "overview-id", "uri": overview_uri, "search_tags": ["existing=1", "team=search"]}
    ]


@pytest.mark.asyncio
async def test_update_search_tags_with_levels_queries_directory_uri_only():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    directory_uri = "viking://resources/demo/doc.md"
    calls = {"filter": [], "upsert": []}

    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)

    async def _fake_filter(*, filter, limit, output_fields, ctx):
        calls["filter"].append(
            {
                "filter": filter,
                "limit": limit,
                "output_fields": list(output_fields),
                "account_id": ctx.account_id,
            }
        )
        return [
            {"id": "dir-l0", "uri": directory_uri, "level": 0, "search_tags": ["old=0"]},
            {"id": "dir-l1", "uri": directory_uri, "level": 1, "search_tags": ["old=1"]},
        ]

    async def _fake_upsert(data, *, ctx, partial_update=False):
        del ctx, partial_update
        calls["upsert"].append(dict(data))
        return data["id"]

    backend.filter = _fake_filter
    backend.upsert = _fake_upsert

    updated = await backend.update_search_tags(
        directory_uri,
        ["team=search"],
        mode="append",
        levels=[0, 1],
        ctx=ctx,
    )

    assert len(updated) == 2
    assert len(calls["filter"]) == 1
    assert calls["filter"][0]["limit"] == 2
    assert "id" in calls["filter"][0]["output_fields"]
    assert calls["upsert"] == [
        {
            "id": "dir-l0",
            "uri": directory_uri,
            "level": 0,
            "search_tags": ["old=0", "team=search"],
        },
        {
            "id": "dir-l1",
            "uri": directory_uri,
            "level": 1,
            "search_tags": ["old=1", "team=search"],
        },
    ]


@pytest.mark.asyncio
async def test_update_search_tags_with_levels_skips_records_without_id_and_private_helper_is_removed():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    calls = {"filter": [], "upsert": []}

    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)

    async def _fake_filter(*, filter, limit, output_fields, ctx):
        del filter, limit, output_fields, ctx
        calls["filter"].append(True)
        return [
            {
                "id": "r1",
                "uri": "viking://resources/demo/doc.md",
                "level": 0,
                "search_tags": ["old=1"],
            },
            {"uri": "viking://resources/demo/missing-id.md", "level": 1, "search_tags": ["old=2"]},
            {"id": "r2", "uri": "viking://resources/demo/doc.md", "level": 2, "search_tags": None},
        ]

    async def _fake_upsert(data, *, ctx, partial_update=False):
        del ctx, partial_update
        calls["upsert"].append(dict(data))
        return data["id"]

    backend.filter = _fake_filter
    backend.upsert = _fake_upsert

    updated = await backend.update_search_tags(
        "viking://resources/demo/doc.md",
        ["team=search"],
        mode="append",
        levels=[0, 1, 2],
        ctx=ctx,
    )

    assert not hasattr(VikingVectorIndexBackend, "_apply_search_tags_to_records")
    assert calls["filter"] == [True]
    assert updated == [
        {
            "id": "r1",
            "uri": "viking://resources/demo/doc.md",
            "level": 0,
            "search_tags": ["old=1", "team=search"],
        },
        {
            "id": "r2",
            "uri": "viking://resources/demo/doc.md",
            "level": 2,
            "search_tags": ["team=search"],
        },
    ]
    assert calls["upsert"] == updated


@pytest.mark.asyncio
async def test_update_search_tags_rejects_invalid_mode_before_fetch():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    calls = {"fetch_by_uri": 0}

    async def _fake_fetch_by_uri(uri, *, ctx):
        del uri, ctx
        calls["fetch_by_uri"] += 1
        return None

    backend.fetch_by_uri = _fake_fetch_by_uri

    with pytest.raises(ValueError, match="unsupported tag mode"):
        await backend.update_search_tags(
            "viking://resources/demo/doc.md",
            ["team=search"],
            mode="invalid",
            ctx=ctx,
        )

    assert calls["fetch_by_uri"] == 0


@pytest.mark.asyncio
async def test_update_search_tags_with_levels_rejects_invalid_mode_before_filter():
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    calls = {"filter": 0}

    async def _fake_filter(*, filter, limit, output_fields, ctx):
        del filter, limit, output_fields, ctx
        calls["filter"] += 1
        return []

    backend.filter = _fake_filter

    with pytest.raises(ValueError, match="unsupported tag mode"):
        await backend.update_search_tags(
            "viking://resources/demo",
            ["team=search"],
            mode="invalid",
            levels=[0, 1],
            ctx=ctx,
        )

    assert calls["filter"] == 0


@pytest.mark.asyncio
async def test_single_account_backend_mutations_run_adapter_in_threadpool(monkeypatch):
    calls = []

    class _Adapter:
        mode = "local"

        def drop_collection(self):
            return True

        def delete(self, **kwargs):
            calls.append(("adapter_delete_kwargs", kwargs))
            return 2

        def count(self, **kwargs):
            calls.append(("adapter_count_kwargs", kwargs))
            return 3

        def clear(self):
            return True

        def close(self):
            return None

    async def _fake_to_thread(func, /, *args, **kwargs):
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.asyncio.to_thread", _fake_to_thread
    )

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id=None,
        shared_adapter=_Adapter(),
    )
    filter_expr = Eq("account_id", "acc1")

    assert await backend.drop_collection() is True
    assert await backend.delete(["rec-1"]) == 2
    assert await backend.delete_by_filter(filter_expr) == 2
    assert await backend.count(filter=filter_expr) == 3
    assert await backend.clear() is True
    await backend.close()

    assert [call[0] for call in calls if not call[0].startswith("adapter_")] == [
        "drop_collection",
        "delete",
        "delete",
        "count",
        "clear",
        "close",
    ]


@pytest.mark.asyncio
async def test_single_account_backend_query_runs_adapter_in_threadpool(monkeypatch):
    called = {}

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

        def query(self, **kwargs):
            called["query_kwargs"] = kwargs
            return [{"id": "rec-1", "uri": "viking://resources/sample", "account_id": "acc1"}]

    async def _fake_to_thread(func, /, *args, **kwargs):
        called["func"] = func
        called["args"] = args
        called["kwargs"] = kwargs
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.asyncio.to_thread", _fake_to_thread
    )

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )

    result = await backend.query(
        query_vector=[0.1, 0.2],
        limit=5,
        output_fields=["uri"],
    )

    assert result == [{"id": "rec-1", "uri": "viking://resources/sample", "account_id": "acc1"}]
    assert called["func"].__self__ is backend._adapter
    assert called["func"].__name__ == "query"
    assert called["args"] == ()
    assert called["kwargs"]["query_vector"] == [0.1, 0.2]
    assert called["kwargs"]["limit"] == 5
    assert called["kwargs"]["output_fields"] == ["uri"]
    query_filter = called["kwargs"]["filter"]
    assert isinstance(query_filter, Eq)
    assert query_filter.field == "account_id"
    assert query_filter.value == "acc1"
