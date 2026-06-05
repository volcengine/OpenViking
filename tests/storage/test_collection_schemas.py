# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import hashlib
import inspect
import json
import logging
import math
import os
from types import SimpleNamespace

import pytest

from openviking.models.embedder.base import (
    CompositeHybridEmbedder,
    DenseEmbedderBase,
    EmbedResult,
)
from openviking.models.embedder.local_bm25_embedder import LocalBM25Embedder
from openviking.server.identity import RequestContext, Role
from openviking.storage.collection_schemas import (
    CollectionSchemas,
    TextEmbeddingHandler,
    _build_embedding_metadata,
    _LocalBM25RebuildState,
    init_context_collection,
)
from openviking.storage.errors import EmbeddingRebuildRequiredError
from openviking.storage.expr import Eq
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.viking_vector_index_backend import (
    LOCAL_BM25_REBUILD_BATCH_SIZE,
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


class _LocalBM25SchedulerMixin:
    """Reuses the real VikingVectorIndexBackend scheduler methods on fake VikingDBs.

    The scheduler only touches `rebuild_local_bm25_sparse_vectors` and an instance
    dict `_local_bm25_rebuilds`, so the bound methods drop in cleanly. Each fake
    must call `_LocalBM25SchedulerMixin.__init__(self)` to initialise state.
    """

    schedule_local_bm25_rebuild = VikingVectorIndexBackend.schedule_local_bm25_rebuild
    _start_local_bm25_rebuild = VikingVectorIndexBackend._start_local_bm25_rebuild
    _finish_local_bm25_rebuild = VikingVectorIndexBackend._finish_local_bm25_rebuild
    _drain_local_bm25_rebuilds = VikingVectorIndexBackend._drain_local_bm25_rebuilds

    def __init__(self):
        self._local_bm25_rebuilds: dict = {}

skip_if_not_manual = pytest.mark.skipif(
    os.environ.get("RUN_MANUAL") != "1", reason="manual 10k local BM25 rebuild test"
)


class _DummyEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        del is_query
        self.calls += 1
        return EmbedResult(dense_vector=[0.1, 0.2])


class _FakeDenseEmbedder(DenseEmbedderBase):
    def __init__(self):
        super().__init__("fake-dense")
        self.calls: list[tuple[str, bool]] = []

    def embed(self, text: str, is_query: bool = False) -> EmbedResult:
        self.calls.append((text, is_query))
        return EmbedResult(dense_vector=[0.1, 0.2])

    def get_dimension(self) -> int:
        return 2


class _DummyConfig:
    def __init__(
        self,
        embedder: _DummyEmbedder,
        backend: str = "volcengine",
        volcengine_data_api_key: str | None = None,
        max_input_tokens: int = 4096,
    ):
        if not hasattr(embedder, "prepare_embedding_input"):
            embedder.prepare_embedding_input = lambda text: text
        if not hasattr(embedder, "prepare_embedding_inputs"):
            embedder.prepare_embedding_inputs = lambda texts: texts
        if not hasattr(embedder, "embed_async"):

            async def _embed_async(text: str, is_query: bool = False) -> EmbedResult:
                try:
                    return embedder.embed(text, is_query=is_query)
                except TypeError:
                    return embedder.embed(text)

            embedder.embed_async = _embed_async

        self.storage = SimpleNamespace(
            vectordb=SimpleNamespace(
                name="context",
                backend=backend,
                volcengine=SimpleNamespace(api_key=volcengine_data_api_key),
            )
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
async def test_init_context_collection_rejects_mismatched_nonempty_collection(monkeypatch):
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

    with pytest.raises(EmbeddingRebuildRequiredError, match="Rebuild is required"):
        await init_context_collection(_FakeStorage())


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
        def embed(self, text: str) -> EmbedResult:
            raise RuntimeError("boom")

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
async def test_embedding_handler_local_bm25_write_embeds_dense_then_rebuilds(monkeypatch):
    class _CapturingVikingDB(_LocalBM25SchedulerMixin):
        is_closing = False
        mode = "local"

        def __init__(self):
            super().__init__()
            self.rebuilt_with = None

        async def upsert(self, _data, *, ctx):
            return "rec-1"

        async def rebuild_local_bm25_sparse_vectors(self, sparse_embedder, *, ctx):
            self.rebuilt_with = sparse_embedder
            return 1

    dense = _FakeDenseEmbedder()
    sparse = LocalBM25Embedder()
    embedder = CompositeHybridEmbedder(dense, sparse)
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    vikingdb = _CapturingVikingDB()
    handler = TextEmbeddingHandler(vikingdb)

    await handler.on_dequeue(_build_queue_payload())
    rebuild_task = vikingdb._local_bm25_rebuilds["default"].task
    assert rebuild_task is not None
    await rebuild_task

    assert dense.calls == [("hello", False)]
    assert sparse.stats.doc_count == 0
    assert vikingdb.rebuilt_with is sparse


@pytest.mark.asyncio
async def test_embedding_handler_local_bm25_rebuilds_are_coalesced(monkeypatch):
    class _SlowVikingDB(_LocalBM25SchedulerMixin):
        is_closing = False
        mode = "local"

        def __init__(self):
            super().__init__()
            self.calls = 0
            self.active = 0
            self.max_active = 0
            self.started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def rebuild_local_bm25_sparse_vectors(self, sparse_embedder, *, ctx):
            del sparse_embedder, ctx
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                if self.calls == 1:
                    self.started.set()
                    await self.release_first.wait()
                return 1
            finally:
                self.active -= 1

    dense = _FakeDenseEmbedder()
    sparse = LocalBM25Embedder()
    embedder = CompositeHybridEmbedder(dense, sparse)
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    vikingdb = _SlowVikingDB()
    handler = TextEmbeddingHandler(vikingdb)
    ctx = RequestContext(
        user=UserIdentifier(account_id="default", user_id="default", agent_id="default"),
        role=Role.ROOT,
    )

    handler._schedule_local_bm25_sparse_rebuild(ctx)
    await vikingdb.started.wait()
    handler._schedule_local_bm25_sparse_rebuild(ctx)

    state = vikingdb._local_bm25_rebuilds["default"]
    assert state.task is not None
    assert vikingdb.calls == 1

    vikingdb.release_first.set()
    await state.task

    assert vikingdb.calls == 2
    assert vikingdb.max_active == 1


@pytest.mark.asyncio
async def test_embedding_handler_local_bm25_restarts_after_late_pending(monkeypatch):
    class _CountingVikingDB(_LocalBM25SchedulerMixin):
        is_closing = False
        mode = "local"

        def __init__(self):
            super().__init__()
            self.calls = 0

        async def rebuild_local_bm25_sparse_vectors(self, sparse_embedder, *, ctx):
            del sparse_embedder, ctx
            self.calls += 1
            return 1

    dense = _FakeDenseEmbedder()
    sparse = LocalBM25Embedder()
    embedder = CompositeHybridEmbedder(dense, sparse)
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )

    vikingdb = _CountingVikingDB()
    TextEmbeddingHandler(vikingdb)
    ctx = RequestContext(
        user=UserIdentifier(account_id="default", user_id="default", agent_id="default"),
        role=Role.ROOT,
    )
    state = vikingdb._local_bm25_rebuilds.setdefault(
        "default", _LocalBM25RebuildState()
    )
    completed_task = asyncio.create_task(asyncio.sleep(0))
    await completed_task
    state.task = completed_task
    state.pending = True

    vikingdb._finish_local_bm25_rebuild(completed_task, ctx, sparse, state)

    assert state.task is not completed_task
    assert state.task is not None
    await state.task
    assert vikingdb.calls == 1
    assert state.task is None


class _RebuildCountingVikingDB(_LocalBM25SchedulerMixin):
    """Counts rebuild calls; returns a configurable corpus size per rebuild."""

    is_closing = False
    mode = "local"

    def __init__(self, sizes: list[int] | None = None):
        super().__init__()
        self.calls = 0
        self._sizes = sizes or []

    async def rebuild_local_bm25_sparse_vectors(self, sparse_embedder, *, ctx):
        del sparse_embedder, ctx
        idx = self.calls
        self.calls += 1
        return self._sizes[idx] if idx < len(self._sizes) else 0


def _build_rebuild_handler(monkeypatch, sparse, vikingdb):
    dense = _FakeDenseEmbedder()
    embedder = CompositeHybridEmbedder(dense, sparse)
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: _DummyConfig(embedder),
    )
    handler = TextEmbeddingHandler(vikingdb)
    ctx = RequestContext(
        user=UserIdentifier(account_id="default", user_id="default", agent_id="default"),
        role=Role.ROOT,
    )
    return handler, ctx


@pytest.mark.asyncio
async def test_rebuild_fires_below_min_docs(monkeypatch):
    """Below min_docs, every insert triggers a rebuild (warm-up behavior)."""
    sparse = LocalBM25Embedder(rebuild_min_docs=10, rebuild_growth_factor=1.5)
    vikingdb = _RebuildCountingVikingDB(sizes=[1, 2, 3])
    handler, ctx = _build_rebuild_handler(monkeypatch, sparse, vikingdb)

    for _ in range(3):
        handler._schedule_local_bm25_sparse_rebuild(ctx)
        task = vikingdb._local_bm25_rebuilds["default"].task
        if task is not None:
            await task

    assert vikingdb.calls == 3


@pytest.mark.asyncio
async def test_rebuild_skips_when_growth_threshold_not_met(monkeypatch):
    """After warm-up, inserts below the 1.5x threshold don't trigger rebuild."""
    sparse = LocalBM25Embedder(rebuild_min_docs=10, rebuild_growth_factor=1.5)
    vikingdb = _RebuildCountingVikingDB(sizes=[10])
    handler, ctx = _build_rebuild_handler(monkeypatch, sparse, vikingdb)

    state = vikingdb._local_bm25_rebuilds.setdefault(
        "default", _LocalBM25RebuildState()
    )
    state.last_rebuild_size = 10
    # last_rebuild_at uses time.monotonic(); set to "just now" so the
    # max_interval staleness check does NOT fire on its own. This isolates
    # the growth-threshold branch as the only thing being tested.
    import time as _time
    state.last_rebuild_at = _time.monotonic()

    # 4 inserts: approx_size grows 11, 12, 13, 14. Threshold is 10 * 1.5 = 15.
    # None should fire (above min_docs, below growth threshold, time bound not exceeded).
    for _ in range(4):
        handler._schedule_local_bm25_sparse_rebuild(ctx)

    assert vikingdb.calls == 0
    assert state.pending_inserts == 4


@pytest.mark.asyncio
async def test_rebuild_fires_at_growth_threshold(monkeypatch):
    """Insert that pushes approx_size to last_rebuild_size * 1.5 fires rebuild."""
    sparse = LocalBM25Embedder(rebuild_min_docs=10, rebuild_growth_factor=1.5)
    vikingdb = _RebuildCountingVikingDB(sizes=[15])
    handler, ctx = _build_rebuild_handler(monkeypatch, sparse, vikingdb)

    state = vikingdb._local_bm25_rebuilds.setdefault(
        "default", _LocalBM25RebuildState()
    )
    state.last_rebuild_size = 10
    state.last_rebuild_at = 1.0

    # 5 inserts: approx_size = 11, 12, 13, 14, 15. 15 >= 10 * 1.5 → fire on the 5th.
    for _ in range(5):
        handler._schedule_local_bm25_sparse_rebuild(ctx)

    task = vikingdb._local_bm25_rebuilds["default"].task
    assert task is not None
    await task

    assert vikingdb.calls == 1
    assert state.last_rebuild_size == 15


@pytest.mark.asyncio
async def test_rebuild_fires_at_max_interval(monkeypatch):
    """When growth threshold not met, time staleness still triggers rebuild."""
    import time as _time

    sparse = LocalBM25Embedder(
        rebuild_min_docs=10,
        rebuild_growth_factor=1000.0,  # effectively disable growth trigger
        rebuild_max_interval_seconds=60,
    )
    vikingdb = _RebuildCountingVikingDB(sizes=[11])
    handler, ctx = _build_rebuild_handler(monkeypatch, sparse, vikingdb)

    state = vikingdb._local_bm25_rebuilds.setdefault(
        "default", _LocalBM25RebuildState()
    )
    state.last_rebuild_size = 10
    # last_rebuild_at well in the past — 120s ago should exceed the 60s bound.
    state.last_rebuild_at = _time.monotonic() - 120.0

    handler._schedule_local_bm25_sparse_rebuild(ctx)
    task = vikingdb._local_bm25_rebuilds["default"].task
    assert task is not None
    await task

    assert vikingdb.calls == 1


@pytest.mark.asyncio
async def test_rebuild_coalescing_still_works_with_triggers(monkeypatch):
    """In-flight rebuild + new inserts past the threshold still coalesce to 2 calls, not N."""

    class _SlowCountingVikingDB(_LocalBM25SchedulerMixin):
        is_closing = False
        mode = "local"

        def __init__(self):
            super().__init__()
            self.calls = 0
            self.started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def rebuild_local_bm25_sparse_vectors(self, sparse_embedder, *, ctx):
            del sparse_embedder, ctx
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await self.release_first.wait()
            return 15 if self.calls == 1 else 30

    sparse = LocalBM25Embedder(rebuild_min_docs=10, rebuild_growth_factor=1.5)
    vikingdb = _SlowCountingVikingDB()
    handler, ctx = _build_rebuild_handler(monkeypatch, sparse, vikingdb)

    state = vikingdb._local_bm25_rebuilds.setdefault(
        "default", _LocalBM25RebuildState()
    )
    state.last_rebuild_size = 10
    # Recent rebuild — pin time so only the growth trigger drives this test.
    import time as _time
    state.last_rebuild_at = _time.monotonic()

    # Fire enough inserts to trigger the first rebuild.
    for _ in range(5):
        handler._schedule_local_bm25_sparse_rebuild(ctx)
    await vikingdb.started.wait()

    # While rebuild is in flight, fire 10 more inserts. They all set pending,
    # but only one second rebuild should run after the first completes.
    for _ in range(10):
        handler._schedule_local_bm25_sparse_rebuild(ctx)
    assert vikingdb.calls == 1  # second hasn't started yet

    vikingdb.release_first.set()
    await state.task

    assert vikingdb.calls == 2  # not 11 — coalescing held
    assert state.last_rebuild_size == 30


@pytest.mark.asyncio
async def test_bulk_delete_coalesces_to_one_rebuild():
    """A bulk delta_docs=-N from rm fires at most one rebuild, not N."""
    sparse = LocalBM25Embedder(rebuild_min_docs=10, rebuild_growth_factor=1.5)
    vikingdb = _RebuildCountingVikingDB(sizes=[0])
    ctx = RequestContext(
        user=UserIdentifier(account_id="default", user_id="default", agent_id="default"),
        role=Role.ROOT,
    )
    state = vikingdb._local_bm25_rebuilds.setdefault(
        "default", _LocalBM25RebuildState()
    )
    state.last_rebuild_size = 100
    import time as _time
    state.last_rebuild_at = _time.monotonic()

    # Bulk delete of 200 records — far past the shrinkage trigger.
    vikingdb.schedule_local_bm25_rebuild(sparse, ctx=ctx, delta_docs=-200)

    task = vikingdb._local_bm25_rebuilds["default"].task
    assert task is not None
    await task

    assert vikingdb.calls == 1


@pytest.mark.asyncio
async def test_single_deletes_skip_rebuild_until_shrinkage_threshold():
    """One-by-one deletes after a rebuild at 100 only fire once corpus dips past 1/1.5."""
    sparse = LocalBM25Embedder(rebuild_min_docs=10, rebuild_growth_factor=1.5)
    # 100 / 1.5 ≈ 66.67 → trigger fires when approx_size <= 66 (the 34th delete).
    vikingdb = _RebuildCountingVikingDB(sizes=[66])
    ctx = RequestContext(
        user=UserIdentifier(account_id="default", user_id="default", agent_id="default"),
        role=Role.ROOT,
    )
    state = vikingdb._local_bm25_rebuilds.setdefault(
        "default", _LocalBM25RebuildState()
    )
    state.last_rebuild_size = 100
    import time as _time
    state.last_rebuild_at = _time.monotonic()

    for _ in range(33):
        vikingdb.schedule_local_bm25_rebuild(sparse, ctx=ctx, delta_docs=-1)
    assert vikingdb.calls == 0  # 100 - 33 = 67, still above 100 / 1.5

    vikingdb.schedule_local_bm25_rebuild(sparse, ctx=ctx, delta_docs=-1)  # 34th
    task = vikingdb._local_bm25_rebuilds["default"].task
    assert task is not None
    await task

    assert vikingdb.calls == 1
    assert state.last_rebuild_size == 66


@pytest.mark.asyncio
async def test_empty_corpus_rebuild_always_fires():
    """If pending deletes empty the corpus, rebuild fires even when min_docs is small."""
    sparse = LocalBM25Embedder(rebuild_min_docs=10, rebuild_growth_factor=1.5)
    vikingdb = _RebuildCountingVikingDB(sizes=[0])
    ctx = RequestContext(
        user=UserIdentifier(account_id="default", user_id="default", agent_id="default"),
        role=Role.ROOT,
    )
    state = vikingdb._local_bm25_rebuilds.setdefault(
        "default", _LocalBM25RebuildState()
    )
    state.last_rebuild_size = 100
    import time as _time
    state.last_rebuild_at = _time.monotonic()

    vikingdb.schedule_local_bm25_rebuild(sparse, ctx=ctx, delta_docs=-100)

    task = vikingdb._local_bm25_rebuilds["default"].task
    assert task is not None
    await task

    assert vikingdb.calls == 1


@pytest.mark.asyncio
async def test_schedule_with_zero_delta_is_noop():
    """delta_docs=0 (mv-style no-op) must not allocate state or schedule."""
    sparse = LocalBM25Embedder(rebuild_min_docs=10, rebuild_growth_factor=1.5)
    vikingdb = _RebuildCountingVikingDB(sizes=[1])
    ctx = RequestContext(
        user=UserIdentifier(account_id="default", user_id="default", agent_id="default"),
        role=Role.ROOT,
    )
    vikingdb.schedule_local_bm25_rebuild(sparse, ctx=ctx, delta_docs=0)
    assert vikingdb.calls == 0
    assert "default" not in vikingdb._local_bm25_rebuilds


@pytest.mark.asyncio
async def test_update_uri_mapping_preserves_sparse_vector():
    """mv invariant: URI rename preserves the precomputed sparse_vector.

    The mv code path in viking_fs (_update_vector_store_uris) skips the BM25
    rebuild scheduler on the assumption that update_uri_mapping carries the
    pre-computed sparse_vector forward to the new record. If a future change
    to update_uri_mapping strips sparse_vector (e.g., narrowing the spread
    to drop "dead" fields), the mv path silently produces records with no
    sparse_vector and BM25 retrieval degrades. This test pins down the
    spread contract so that regression fails loudly here instead.
    """

    class _StubFacade:
        """Minimal stand-in that exposes only what update_uri_mapping needs."""

        update_uri_mapping = VikingVectorIndexBackend.update_uri_mapping

        def __init__(self, record):
            self._record = dict(record)
            self.upserts: list[dict] = []
            self.deletes: list[str] = []

        async def filter(self, *, filter=None, limit=10, offset=0,
                         output_fields=None, order_by=None, order_desc=False, ctx):
            del filter, limit, offset, order_by, order_desc, ctx
            projected = {k: self._record.get(k) for k in (output_fields or self._record.keys())}
            return [projected]

        async def get(self, ids, *, ctx):
            del ctx
            return [dict(self._record) for rid in ids if rid == self._record["id"]]

        async def upsert(self, data, *, ctx):
            del ctx
            self.upserts.append(dict(data))
            return data.get("id", "")

        async def delete(self, ids, *, ctx):
            del ctx
            self.deletes.extend(ids)
            return len(ids)

    sparse = {"hash_x": 0.42, "hash_y": 0.13}
    original = {
        "id": "old-id-1",
        "uri": "viking://resources/old",
        "level": 2,
        "account_id": "default",
        "sparse_vector": sparse,
        "vector": [0.1] * 8,
        "abstract": "sample abstract",
    }
    facade = _StubFacade(original)
    ctx = RequestContext(
        user=UserIdentifier(account_id="default", user_id="default", agent_id="default"),
        role=Role.ROOT,
    )

    success = await facade.update_uri_mapping(
        ctx=ctx,
        uri="viking://resources/old",
        new_uri="viking://resources/new",
    )

    assert success is True
    assert len(facade.upserts) == 1
    upserted = facade.upserts[0]
    assert upserted["uri"] == "viking://resources/new"
    assert upserted["sparse_vector"] == sparse
    assert upserted["vector"] == original["vector"]
    assert upserted["abstract"] == original["abstract"]
    # The old id was queued for deletion (since new_id differs from old).
    assert facade.deletes == [original["id"]]


@pytest.mark.asyncio
async def test_below_threshold_schedule_during_inflight_preserves_residue():
    """In-flight rebuild + concurrent below-threshold delta: residue is preserved.

    Locks in the documented amortizing-trigger semantic: when a schedule() call
    lands during an in-flight rebuild and the call alone does not cross
    growth_factor, the delta accumulates in pending_inserts / pending_deletes
    rather than firing an extra rebuild. The next schedule() will then evaluate
    against the residue + new delta, so the increment is not lost.

    Codex flagged this as P1 ("late deltas dropped from scheduler accounting");
    the test pins down that residue IS preserved and no spurious extra rebuild
    fires.
    """
    sparse = LocalBM25Embedder(rebuild_min_docs=10, rebuild_growth_factor=1.5)
    vikingdb = _RebuildCountingVikingDB(sizes=[1])
    ctx = RequestContext(
        user=UserIdentifier(account_id="default", user_id="default", agent_id="default"),
        role=Role.ROOT,
    )

    import time as _time

    state = vikingdb._local_bm25_rebuilds.setdefault(
        "default", _LocalBM25RebuildState()
    )
    # last_rebuild_size=100 → growth threshold = 150, shrink threshold = ~67.
    state.last_rebuild_size = 100
    state.last_rebuild_at = _time.monotonic()

    # Simulate an in-flight rebuild by attaching a never-done future as the task.
    loop = asyncio.get_event_loop()
    fake_inflight = loop.create_future()
    state.task = fake_inflight

    # Fire a below-threshold delta: approx_size = 100 + 1 = 101, far below 150
    # and far above shrink threshold. Should NOT set state.pending and should
    # NOT start a new rebuild — the residue accumulates in pending_inserts.
    vikingdb.schedule_local_bm25_rebuild(sparse, ctx=ctx, delta_docs=1)

    assert state.pending is False
    assert state.pending_inserts == 1
    assert vikingdb.calls == 0  # no extra rebuild fired for the residue
    assert state.task is fake_inflight  # in-flight task untouched

    # A subsequent schedule call evaluates against accumulated residue. Now bring
    # the corpus DOWN past the shrinkage threshold to confirm the residue still
    # feeds future trigger evaluations correctly.
    state.task = None  # simulate the in-flight rebuild having completed
    # 33 more deletes: pending_deletes = 33, approx_size = 100 + 1 - 33 = 68 > 67.
    for _ in range(33):
        vikingdb.schedule_local_bm25_rebuild(sparse, ctx=ctx, delta_docs=-1)
    assert vikingdb.calls == 0  # still above shrinkage threshold

    # One more delete: approx_size = 67. 67 * 1.5 = 100.5 > 100 → not yet.
    # Two more: approx_size = 66. 66 * 1.5 = 99 <= 100 → fires.
    vikingdb.schedule_local_bm25_rebuild(sparse, ctx=ctx, delta_docs=-1)
    assert vikingdb.calls == 0
    vikingdb.schedule_local_bm25_rebuild(sparse, ctx=ctx, delta_docs=-1)
    task = vikingdb._local_bm25_rebuilds["default"].task
    assert task is not None
    await task

    # The residue from the in-flight era was carried into this evaluation.
    assert vikingdb.calls == 1

    fake_inflight.cancel()


@pytest.mark.asyncio
async def test_invalid_growth_factor_rejected_at_config():
    from openviking_cli.utils.config.embedding_config import EmbeddingModelConfig

    with pytest.raises(ValueError, match="rebuild_growth_factor"):
        EmbeddingModelConfig(provider="local_bm25", rebuild_growth_factor=1.0)
    with pytest.raises(ValueError, match="rebuild_growth_factor"):
        EmbeddingModelConfig(provider="local_bm25", rebuild_growth_factor=0.5)
    with pytest.raises(ValueError, match="rebuild_max_interval_seconds"):
        EmbeddingModelConfig(provider="local_bm25", rebuild_max_interval_seconds=0)
    with pytest.raises(ValueError, match="rebuild_min_docs"):
        EmbeddingModelConfig(provider="local_bm25", rebuild_min_docs=-1)


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
        def embed(self, text: str, is_query: bool = False) -> EmbedResult:
            del text, is_query
            raise RuntimeError("Malformed input request: expected maxLength: 50000, actual: 75000")

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
async def test_single_account_backend_rebuilds_local_bm25_from_scanned_tenant_corpus():
    captured = {"batches": []}

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "uri"},
                    {"FieldName": "abstract"},
                    {"FieldName": "vector"},
                    {"FieldName": "sparse_vector"},
                    {"FieldName": "account_id"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def scan_all(self):
            return [
                {
                    "id": "rec-1",
                    "uri": "viking://resources/a",
                    "abstract": "foo bar",
                    "vector": [0.1, 0.2],
                    "account_id": "acc1",
                    "_score": 1.0,
                },
                {
                    "id": "rec-2",
                    "uri": "viking://resources/b",
                    "abstract": "foo foo",
                    "vector": [0.3, 0.4],
                    "account_id": "acc1",
                },
                {
                    "id": "rec-other",
                    "uri": "viking://resources/other",
                    "abstract": "other tenant",
                    "vector": [0.5, 0.6],
                    "account_id": "acc2",
                },
            ]

        def upsert(self, data):
            captured["batches"].append(data)
            return [item["id"] for item in data]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )
    sparse = LocalBM25Embedder()

    rebuilt = await backend.rebuild_local_bm25_sparse_vectors(sparse)

    assert rebuilt == 2
    assert sparse.stats.doc_count == 2
    data = [item for batch in captured["batches"] for item in batch]
    assert [item["id"] for item in data] == ["rec-1", "rec-2"]
    assert all("_score" not in item for item in data)
    assert all(item["sparse_vector"] for item in data)


@skip_if_not_manual
@pytest.mark.asyncio
async def test_manual_local_bm25_rebuild_10k_documents_batches_sparse_upserts():
    document_count = 10_000
    captured_batches = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "uri"},
                    {"FieldName": "abstract"},
                    {"FieldName": "vector"},
                    {"FieldName": "sparse_vector"},
                    {"FieldName": "account_id"},
                ]
            }

    class _Adapter:
        mode = "local"

        def get_collection(self):
            return _Collection()

        def scan_all(self):
            return [
                {
                    "id": f"rec-{idx}",
                    "uri": f"viking://resources/doc-{idx}",
                    "abstract": f"common term-{idx % 100} shard-{idx % 7}",
                    "vector": [float(idx % 3), float(idx % 5)],
                    "account_id": "acc1",
                }
                for idx in range(document_count)
            ]

        def upsert(self, data):
            captured_batches.append(data)
            return [item["id"] for item in data]

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )
    sparse = LocalBM25Embedder()

    rebuilt = await backend.rebuild_local_bm25_sparse_vectors(sparse)

    assert rebuilt == document_count
    assert sparse.stats.doc_count == document_count
    assert len(captured_batches) == math.ceil(document_count / LOCAL_BM25_REBUILD_BATCH_SIZE)
    assert all(len(batch) <= LOCAL_BM25_REBUILD_BATCH_SIZE for batch in captured_batches)
    assert len(captured_batches[0]) == LOCAL_BM25_REBUILD_BATCH_SIZE
    assert len(captured_batches[-1]) == document_count % LOCAL_BM25_REBUILD_BATCH_SIZE
    assert all(item["sparse_vector"] for batch in captured_batches for item in batch)


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
