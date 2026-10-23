"""Real local Redis + native AGFS. Set TTL_TEST_REDIS to opt into integration."""

import os
from types import SimpleNamespace

import pytest

from openviking.storage.viking_fs import VikingFS
from openviking.ttl.adapter import TTLBackfillRequest, TTLRecycleRequest
from openviking.ttl.runtime import DirectoryTTL
from openviking.ttl.wiring import install_ttl_hook
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.ttl_config import TTLConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.skipif(not os.environ.get("TTL_TEST_REDIS"), reason="requires an isolated local Redis")
async def test_real_queue_writes_keep_ttl_on_service_loop(tmp_path, enabled):
    import asyncio
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from openviking.storage.queuefs.named_queue import DequeueHandlerBase
    from openviking.storage.queuefs.process_result import ProcessResult
    from openviking.storage.queuefs.queue_manager import QueueManager

    agfs = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
    )
    fs = VikingFS(agfs=agfs)
    engine = DirectoryTTL(
        resource_id="queue-loop-" + uuid4().hex,
        redis={"endpoints": [os.environ["TTL_TEST_REDIS"]]},
        timezone_name="UTC",
    )
    pair = (TTLConfig(sessions=1, user_events=1) if enabled else TTLConfig(), None)
    engine.bind_runtime(
        SimpleNamespace(
            viking_fs=fs,
            runtime_config_manager=SimpleNamespace(resolve_account=AsyncMock(return_value=pair)),
        ),
        None,
    )
    install_ttl_hook(fs, engine)
    await engine.start()
    owner = asyncio.get_running_loop()
    session = "viking://user/u/sessions/one"
    event = "viking://user/u/memories/events/" + datetime.now(timezone.utc).strftime("%Y/%m/%d")
    queue_loops = []

    class Writer(DequeueHandlerBase):
        async def on_dequeue(self, data):
            queue_loops.append(asyncio.get_running_loop())
            await fs.write_file(session + "/messages.jsonl", "first", ctx=engine.context("a"))
            await fs.append_file(session + "/messages.jsonl", "second", ctx=engine.context("a"))
            await fs.write_file(event + "/event.md", "event", ctx=engine.context("a"))
            return ProcessResult.success()

    queues = QueueManager(agfs, max_concurrent_session_commit=2)
    queues._REQUEUE_POLL_INTERVAL = 0.01
    queue = queues.get_queue(queues.SESSION_COMMIT, dequeue_handler=Writer(), allow_create=True)
    raw = None
    try:
        if enabled:
            raw = await engine.client()
            await raw.ping()  # Bind a real connection to the service loop before the queue writes.
        await queue.enqueue({"test": "ttl-write"})
        queues.start()
        async with asyncio.timeout(5):
            while (await queue.get_status()).processed != 1:
                await asyncio.sleep(0.01)
        assert queue_loops and queue_loops[0] is not owner
        await asyncio.to_thread(queues.stop)
        if enabled:
            async with asyncio.timeout(5):
                while engine._retry or (await engine.read_initial("a"))["done"] is not True:
                    await asyncio.sleep(0.01)
            assert engine._task.get_loop() is owner
            assert engine._registration_task.get_loop() is owner
            assert await raw.zscore(engine.prefix("a") + "sessions", session) is not None
            assert await raw.zscore(engine.prefix("a") + "user_events", event) is not None
            echo = await engine.resolve_expires_at([session, event], engine.context("a"))
            assert echo[session] and echo[event]
        else:
            assert engine.redis is engine._task is engine._registration_task is None
            assert not engine._retry and not engine._initial_requests
    finally:
        await asyncio.to_thread(queues.stop)
        if raw is not None:
            async for key in raw.scan_iter(match=engine.prefix("a") + "*"):
                await raw.delete(key)
        await engine.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("TTL_TEST_REDIS"), reason="requires an isolated local Redis")
async def test_real_registration_reset_recycle(tmp_path):
    from uuid import uuid4

    agfs = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
    )
    fs = VikingFS(agfs=agfs)
    e = DirectoryTTL(
        resource_id="ttl-test-" + uuid4().hex,
        redis={"endpoints": [os.environ["TTL_TEST_REDIS"]]},
        delete_interval=0,
    )
    e.fs = fs

    async def policies(_):
        return TTLConfig(sessions=1, user_events=1), None

    e.policies = policies
    e.account_manager = SimpleNamespace(list_accounts=lambda **kw: [])
    ctx = e.context("a")
    root = "viking://user/u/sessions"
    uri = root + "/one/messages.jsonl"
    install_ttl_hook(fs, e)
    await fs.write_file(uri, "first\n", ctx=ctx)
    await e._retry_writes()
    redis = await e.client()
    score = await redis.zscore(e.prefix("a") + "sessions", root + "/one")
    assert score is not None
    await fs.append_file(uri, "second\n", ctx=ctx)
    await e._retry_writes()
    assert await redis.zscore(e.prefix("a") + "sessions", root + "/one") >= score
    # Simulate a missed registration, not a process-kill window.
    fs.set_post_write_hook(None)
    await fs.write_file(root + "/missing/messages.jsonl", "history", ctx=ctx)
    fs.set_post_write_hook(e.on_post_write)
    await e.register_initial_backfill(TTLBackfillRequest(e.resource_id, "a", (root,)))
    await e._scan("a")
    prefix = e.prefix("a") + "backfill:"
    assert await redis.get(prefix + "initial:done") == "1"
    historical = await redis.zscore(e.prefix("a") + "sessions", root + "/missing")
    assert historical == e.modtime(await fs.stat(root + "/missing/messages.jsonl", ctx=ctx))
    await redis.zrem(e.prefix("a") + "sessions", root + "/missing")
    await e.register_initial_backfill(TTLBackfillRequest(e.resource_id, "a", (root,), True))
    assert await redis.get(prefix + "initial:done") is None
    await e._scan("a")
    assert await redis.get(prefix + "initial:done") == "1"
    assert await redis.zscore(e.prefix("a") + "sessions", root + "/missing") == historical
    # Old score cannot delete a renewed root; actual ModTime wins under its lock.
    await redis.zadd(e.prefix("a") + "sessions", {root + "/one": 1})
    result = await e.recycle_due(TTLRecycleRequest(e.resource_id, "a", 100))
    assert result.skipped == 1
    assert await fs.read_file(uri, ctx=ctx) == "first\nsecond\n"
    echo = await e.resolve_expires_at([root + "/one", uri], ctx)
    assert echo[uri] == echo[root + "/one"] and echo[uri]
    # Only delete this test's scoped keys.
    keys = [
        e.prefix("a") + suffix
        for suffix in (
            "sessions",
            "backfill:initial",
            "backfill:initial:done",
            "backfill:pending",
        )
    ]
    await redis.delete(*keys)
    await e.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("TTL_TEST_REDIS"), reason="requires local Redis")
async def test_commit_writes_and_stale_file_vector_delivery(tmp_path, monkeypatch):
    import json
    import time
    from datetime import datetime
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from openviking.message import TextPart
    from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
    from openviking.server.identity import RequestContext, Role
    from openviking.service.task_work_index import (
        bind_task_context,
        prepare_task_payload,
    )
    from openviking.session.memory.dataclass import (
        MemoryField,
        MemoryTypeSchema,
        ResolvedOperation,
    )
    from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
    from openviking.session.memory.memory_updater import (
        ExtractContext,
        MemoryUpdater,
        MemoryUpdateResult,
    )
    from openviking.session.memory.merge_op import FieldType, MergeOp
    from openviking.session.session import Session
    from openviking.storage.collection_schemas import TextEmbeddingHandler
    from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
    from openviking.storage.queuefs.process_result import ProcessOutcome
    from openviking.ttl import wiring
    from openviking_cli.exceptions import NotFoundError
    from openviking_cli.session.user_id import UserIdentifier

    fs = VikingFS(
        agfs=create_agfs_client(
            RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
        )
    )
    e = DirectoryTTL(
        resource_id="commit-" + uuid4().hex,
        redis={"endpoints": [os.environ["TTL_TEST_REDIS"]]},
        delete_interval=0,
    )
    e.fs = fs
    e.policies = AsyncMock(return_value=(TTLConfig(sessions=1, user_events=1, peer_events=1), None))
    install_ttl_hook(fs, e)
    monkeypatch.setattr(wiring, "_adapter", e)
    ctx = RequestContext(user=UserIdentifier("a", "u"), role=Role.USER)
    root = "viking://user/u/sessions/one"
    session = Session(viking_fs=fs, session_id="one", session_uri=root, user=ctx.user)
    await session.ensure_exists()
    await session.add_messages_async(
        [{"role": "user", "parts": [TextPart(text="TTL integration")]}]
    )
    source = root + "/history/archive_001"
    meta = source + "/.meta.json"
    await fs.write_file(
        meta, json.dumps({"phase1": {"queue_message": {"task_id": "new"}}}), ctx=ctx
    )
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.register(
        MemoryTypeSchema(
            memory_type="events",
            description="test",
            fields=[
                MemoryField(name="content", field_type=FieldType.STRING, merge_op=MergeOp.PATCH)
            ],
        )
    )
    queued = []

    async def enqueue(message):
        # The existing NamedQueue attaches task ownership to serialized work.
        payload, _ = prepare_task_payload(message.to_dict())
        queued.append(EmbeddingMsg.from_dict(payload))
        return True

    db = SimpleNamespace(
        enqueue_embedding_msg=AsyncMock(side_effect=enqueue),
        is_closing=False,
        account_uses_content_field=AsyncMock(return_value=False),
        get_strict=AsyncMock(return_value=[]),
        upsert=AsyncMock(return_value="vector-id"),
    )
    updater = MemoryUpdater(registry=registry, vikingdb=db)
    updater._get_viking_fs = lambda: fs
    extract = ExtractContext([], source_archive_uri=source)
    date = datetime.now(e.zone).strftime("%Y/%m/%d")
    uris = [
        "viking://user/u/" + suffix + "/" + date + "/event.md"
        for suffix in ("memories/events", "peers/p/memories/events")
    ]
    with bind_task_context("new", "a", "u", work_id="commit-work"):
        await updater._apply_upsert(
            ResolvedOperation(
                memory_fields={"content": "normal commit"},
                memory_type="events",
                uris=uris,
            ),
            ctx,
            extract_context=extract,
        )
        result = MemoryUpdateResult()
        for uri in uris:
            result.add_written(uri)
        assert await updater._vectorize_memories(result, ctx, extract_context=extract) == 2
    redis = await e.client()
    await e._retry_writes()
    for kind, uri in zip(("user_events", "peer_events"), uris, strict=True):
        assert await redis.zscore(e.prefix("a") + kind, uri.rsplit("/", 1)[0]) is not None
    message = queued[0]
    assert message.context_data["created_at"] and message.task_id == "new"
    assert message.context_data["_write_source"] == source

    class Embedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="ttl-test")

        def embed(self, text, is_query=False):
            return EmbedResult(dense_vector=[1.0, 0.0, 0.0, 0.0])

        def get_dimension(self):
            return 4

    embedder = Embedder()
    handler = TextEmbeddingHandler(db, SimpleNamespace(bind=lambda _: embedder))
    assert (await handler.on_dequeue({"data": message.to_json()})).outcome is ProcessOutcome.SUCCESS
    db.upsert.assert_awaited_once()
    # Reused archive has a different owner. Neither file nor queued vector may commit.
    await fs.write_file(
        meta,
        json.dumps({"phase1": {"queue_message": {"task_id": "replacement"}}}),
        ctx=ctx,
    )
    with bind_task_context("new", "a", "u", work_id="commit-work"):
        with pytest.raises(NotFoundError):
            await updater._apply_upsert(
                ResolvedOperation(
                    memory_fields={"content": "stale"}, memory_type="events", uris=uris
                ),
                ctx,
                extract_context=extract,
            )
        with pytest.raises(NotFoundError):
            await fs.write_file(source + "/.done", "stale", ctx=ctx)
    assert (await handler.on_dequeue({"data": message.to_json()})).outcome is ProcessOutcome.FAILED
    db.upsert.assert_awaited_once()
    # Actual recursive deletion after a clock advance; no stat/rm mocks.
    await redis.set(e.prefix("a") + "backfill:initial:done", "1")
    later = time.time_ns() + 3 * 86400 * 1_000_000_000
    monkeypatch.setattr("openviking.ttl.runtime.time.time_ns", lambda: later)
    deleted = await e.recycle_due(TTLRecycleRequest(e.resource_id, "a", later // 1_000_000))
    assert deleted.deleted == 3
    assert not await session.exists()
    with pytest.raises(NotFoundError):
        await session.add_messages_async(
            [{"role": "user", "parts": [TextPart(text="late append")]}]
        )
    with pytest.raises(NotFoundError):
        await fs.write_file(source + "/.done", "late summary", ctx=ctx)
    assert not await session.exists()
    await redis.delete(
        *(
            e.prefix("a") + suffix
            for suffix in (
                "sessions",
                "user_events",
                "peer_events",
                "backfill:initial:done",
            )
        )
    )
    await e.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("TTL_TEST_REDIS"), reason="requires native vector engine")
async def test_event_filter_real_vector_ranking(tmp_path):
    from datetime import datetime, timedelta, timezone

    from openviking.storage.vectordb.collection.local_collection import (
        get_or_create_local_collection,
    )
    from openviking.storage.vectordb_adapters.local_adapter import (
        LocalCollectionAdapter,
    )

    e = DirectoryTTL(resource_id="filter-test")

    async def policies(_):
        return TTLConfig(
            user_events=7,
            peer_events=7,
            roots={"viking://user/u/peers/long/memories/events": 30},
        ), None

    e.policies = policies
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(days=10)).isoformat()
    fresh = now.isoformat()
    meta = {
        "CollectionName": "ttl",
        "Fields": [
            {"FieldName": "id", "FieldType": "int64", "IsPrimaryKey": True},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "created_at", "FieldType": "date_time"},
            {"FieldName": "updated_at", "FieldType": "date_time"},
            {"FieldName": "account_id", "FieldType": "string"},
        ],
    }
    collection = get_or_create_local_collection(meta_data=meta, path=str(tmp_path))
    collection.upsert_data(
        [
            {
                "id": 1,
                "uri": "/user/u/memories/events/2020/01/01/e.md",
                "created_at": stale,
                "vector": [1.0, 0.0, 0.0, 0.0],
            },
            {
                "id": 2,
                "uri": "/user/u/memories/events/2026/10/20/e.md",
                "created_at": fresh,
                "vector": [0.9, 0.1, 0.0, 0.0],
            },
            {
                "id": 3,
                "uri": "/user/u/peers/p/memories/events/2020/01/01/e.md",
                "created_at": stale,
                "vector": [1.0, 0.0, 0.0, 0.0],
            },
            {
                "id": 4,
                "uri": "/user/u/peers/long/memories/events/2020/01/01/e.md",
                "created_at": stale,
                "vector": [0.8, 0.2, 0.0, 0.0],
            },
            {
                "id": 5,
                "uri": "/resources/a.md",
                "created_at": stale,
                "vector": [0.7, 0.3, 0.0, 0.0],
            },
        ]
    )
    collection.create_index(
        "ttl",
        {
            "IndexName": "ttl",
            "VectorIndex": {"IndexType": "flat", "Distance": "ip"},
            "ScalarIndex": ["uri", "created_at", "account_id", "updated_at"],
        },
    )
    adapter = LocalCollectionAdapter("ttl", str(tmp_path), "ttl")
    adapter._collection = collection
    query_filter = adapter._compile_filter(await e.event_filter(e.context("a")))
    result = collection.search_by_vector(
        "ttl", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=1, filters=query_filter
    )
    assert [row.id for row in result.data] == [2]  # Highest stale result is excluded before limit.
    result = collection.search_by_vector(
        "ttl", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=10, filters=query_filter
    )
    assert {row.id for row in result.data} == {
        2,
        4,
        5,
    }  # No memory_type labels are present.
    # Missing AGFS directories still remove every descendant vector, while
    # preserving another account and sibling URIs in the same collection.
    from unittest.mock import AsyncMock

    from openviking.storage.viking_vector_index_backend import VikingVectorIndexBackend

    target = "viking://user/u/sessions/gone"
    collection.upsert_data(
        [
            {
                "id": i,
                "uri": uri.replace("viking://", "/"),
                "account_id": account,
                "updated_at": fresh,
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
            for i, uri, account in (
                (10, target + "/nested/event.md", "a"),
                (11, target + "/nested/event.md", "b"),
                (12, target + "-sibling/file.md", "a"),
            )
        ]
    )

    async def delete_filter(expr):
        return adapter.delete(filter=expr)

    backend = object.__new__(VikingVectorIndexBackend)
    backend._get_backend_for_context = AsyncMock(
        return_value=SimpleNamespace(delete_by_filter=delete_filter)
    )
    backend.count = AsyncMock(return_value=0)
    fs = VikingFS(
        agfs=create_agfs_client(
            RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path / "agfs"), backend="local"))
        )
    )
    fs._get_vector_store = lambda: backend
    ctx = e.context("a")
    lease = await fs._async_agfs.pathlock_acquire_tree(fs._uri_to_path(target, ctx=ctx))
    try:
        await fs.rm(target, recursive=True, ctx=ctx, lease_ref=lease)
    finally:
        await fs._async_agfs.pathlock_release(lease)
    remaining = collection.search_by_vector("ttl", dense_vector=[1.0, 0.0, 0.0, 0.0], limit=20)
    ids = {row.id for row in remaining.data}
    assert 10 not in ids and {11, 12}.issubset(ids)
    collection.drop()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get("TTL_TEST_REDIS"), reason="requires local Redis")
async def test_http_configuration_initial_and_lifecycle(tmp_path, monkeypatch):
    import asyncio
    from uuid import uuid4

    import httpx

    from openviking.server.app import _initialize_ttl_adapter
    from openviking.server.config import ServerConfig, TTLAdapterConfig
    from openviking.ttl import wiring
    from openviking_cli.utils.config.open_viking_config import (
        OpenVikingConfig,
        OpenVikingConfigSingleton,
    )
    from tests.server.test_admin_api import ROOT_KEY, _build_lightweight_admin_test_app

    monkeypatch.setattr(
        OpenVikingConfigSingleton,
        "_instance",
        OpenVikingConfig.from_dict({"storage": {"workspace": str(tmp_path)}}),
    )
    monkeypatch.setattr(wiring, "_adapter", None)
    app = _build_lightweight_admin_test_app()
    service = app.state.fake_service
    service.viking_fs = VikingFS(
        agfs=create_agfs_client(
            RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
        )
    )
    await service.runtime_config_manager.initialize()
    await app.state.api_key_manager.load()
    rid = "http-" + uuid4().hex
    config = ServerConfig(
        ttl_adapter=TTLAdapterConfig(
            enabled=True,
            config={
                "resource_id": rid,
                "redis": {"endpoints": [os.environ["TTL_TEST_REDIS"]]},
            },
        )
    )
    await _initialize_ttl_adapter(app, service, config)
    e = app.state.ttl_adapter
    assert e.redis is None  # Start does no Redis or account enumeration.
    await e.start()  # The real service starts TTL after its expiry runtime is ready.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": ROOT_KEY},
    ) as client:
        response = await client.post(
            "/api/v1/admin/accounts",
            json={"account_id": "space", "admin_user_id": "admin"},
        )
        assert response.status_code == 200, response.text
        response = await client.patch(
            "/api/v1/admin/accounts/space/configuration",
            json={"settings": {"ttl": {"sessions": 2}}},
        )
        assert response.status_code == 200, response.text
        response = await client.get(
            "/api/v1/admin/ttl/configuration", params={"account_id": "space"}
        )
        assert response.json()["result"]["account"]["sessions"] == 2
        root = "viking://user/u/sessions"
        await service.viking_fs.write_file(root + "/s/messages.jsonl", "", ctx=e.context("space"))
        body = {
            "resource_id": rid,
            "account_id": "space",
            "roots": [root],
            "reset": True,
        }
        response = await client.post("/api/v1/admin/ttl/initial", json=body)
        assert response.status_code == 200, response.text
        redis = await e.client()
        async with asyncio.timeout(5):
            while await redis.get(e.prefix("space") + "backfill:initial:done") != "1":
                await asyncio.sleep(0.01)
        echo = await e.resolve_expires_at([root + "/s"], e.context("space"))
        assert echo[root + "/s"]
        response = await client.post(
            "/api/v1/admin/ttl/initial", json=body, headers={"X-API-Key": "invalid"}
        )
        assert response.status_code == 401
        response = await client.patch(
            "/api/v1/admin/accounts/space/configuration",
            json={"settings": {"ttl": {"sessions": "disabled"}}},
        )
        assert response.status_code == 200, response.text
        assert await e.resolve_expires_at([root + "/s"], e.context("space")) == {root + "/s": None}
        await redis.delete(
            *(
                e.prefix("space") + s
                for s in (
                    "sessions",
                    "backfill:initial",
                    "backfill:initial:done",
                    "backfill:pending",
                )
            )
        )
    await e.aclose()
    assert e._task is None and e.redis is None


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint_env", ["TTL_TEST_REDIS", "TTL_TEST_CLUSTER"])
async def test_consumer_starts_on_registration_and_recovers_activation(endpoint_env):
    import asyncio
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from openviking.scheduling.expiry.api import ExpiryRegistration
    from openviking.scheduling.expiry.config import ExpiryConfig
    from openviking.scheduling.expiry.keys import ExpiryKeys, encode_component
    from openviking.scheduling.expiry.runtime import ExpiryRuntime, ExpiryRuntimeRoles

    endpoint = os.environ.get(endpoint_env)
    if not endpoint:
        pytest.skip("requires an isolated local Redis")
    rid = "ttl-activation-" + uuid4().hex
    config = ExpiryConfig(
        service_name=rid,
        shard_count=2,
        redis={
            "mode": "cluster" if endpoint_env.endswith("CLUSTER") else "standalone",
            "endpoints": [endpoint],
        },
    )
    engine = DirectoryTTL(resource_id=rid, redis=config.redis.model_dump())
    raw = await engine.client()
    runtimes = []
    try:
        for replacement in (False, True):
            runtime = ExpiryRuntime(
                config, roles=ExpiryRuntimeRoles(report=True, publish=False, consume=True)
            )
            runtimes.append(runtime)
            runtime.scheduler.register_handler("session", SimpleNamespace(handle=AsyncMock()))
            engine.install_expiry(runtime.scheduler)
            await runtime.start_storage()
            await runtime.start_roles()
            if not replacement:
                assert list(runtime._consumers) == ["session"]
                assert list(runtime._tasks) == ["callback:session"]
                # Producers really report from their own event loops (queue workers).
                await asyncio.to_thread(
                    lambda runtime=runtime: asyncio.run(
                        runtime.scheduler.report(
                            ExpiryRegistration(
                                engine._identity("a"), due_at_ms=1, timeout_seconds=3600
                            )
                        )
                    )
                )
                assert (
                    await raw.hget(
                        ExpiryKeys(encode_component(rid)).namespace(), "consumer_enabled:ttl"
                    )
                    == "1"
                )
            assert list(runtime._consumers) == ["session", "ttl"]
            assert runtime._tasks["callback:ttl"].get_loop() is asyncio.get_running_loop()
            await runtime.stop_roles(keep_reporting=True)
            await engine._schedule("b", force=True)
            assert not runtime._tasks  # Producer drain must not restart consumption.
            await runtime.close()
    finally:
        for runtime in runtimes:
            await runtime.close()
        async for key in raw.scan_iter(match=f"expiry:*{encode_component(rid)}*"):
            await raw.delete(key)
        await engine.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint_env", ["TTL_TEST_REDIS", "TTL_TEST_CLUSTER"])
async def test_real_expiry_resumes_initial_and_recycles_without_server(tmp_path, endpoint_env):
    import asyncio
    import time
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from openviking.scheduling.expiry.config import ExpiryConfig
    from openviking.scheduling.expiry.keys import (
        ExpiryKeys,
        encode_component,
        encode_item_id,
        shard_for,
    )
    from openviking.scheduling.expiry.runtime import ExpiryRuntime, ExpiryRuntimeRoles
    from openviking_cli.exceptions import NotFoundError

    endpoint = os.environ.get(endpoint_env)
    if not endpoint:
        pytest.skip("requires an isolated local Redis")
    rid = "ttl-expiry-" + uuid4().hex
    config = ExpiryConfig(
        service_name=rid,
        shard_count=2,
        callback_poll_seconds=0.01,
        processing_timeout_seconds=1,
        publisher_separate_process=False,
        redis={
            "mode": "cluster" if endpoint_env.endswith("CLUSTER") else "standalone",
            "endpoints": [endpoint],
        },
    )
    fs = VikingFS(
        agfs=create_agfs_client(
            RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
        )
    )
    root = "viking://user/u/memories/events"
    unit = root + "/2020/01/01"
    runtimes, engines = [], []

    async def replacement(*, consume):
        e = DirectoryTTL(resource_id=rid, redis=config.redis.model_dump(), delete_interval=0)
        e.fs = fs
        e.policies = AsyncMock(return_value=(TTLConfig(user_events=1), None))
        runtime = ExpiryRuntime(
            config, roles=ExpiryRuntimeRoles(report=True, publish=consume, consume=consume)
        )
        engines.append(e)
        runtimes.append(runtime)
        e.install_expiry(runtime.scheduler)
        await runtime.start_storage()
        await runtime.start_roles()
        return e

    try:
        first = await replacement(consume=False)
        await fs.write_file(unit + "/history.md", "event", ctx=first.context("a"))
        await first.register_initial_backfill(TTLBackfillRequest(rid, "a", (root,)))
        assert (await first.read_initial("a"))["pending"]
        raw = await first.client()
        identity = encode_item_id(first._identity("a"))
        keys = ExpiryKeys(encode_component(rid))
        source = keys.source(shard_for(identity, config.shard_count))
        assert await raw.zscore(source, identity) is not None
        # Make the persisted task overdue; the publisher/registry/consumer remain real.
        await raw.zadd(source, {identity: time.time_ns() // 1_000_000 - 60000})
        await first.aclose()
        await runtimes[0].close()

        resumed = await replacement(consume=True)
        report = resumed._scheduler.report
        attempts = 0

        async def flaky_report(task):
            nonlocal attempts
            attempts += 1
            if attempts <= 4:
                raise ConnectionError("renewal unavailable")
            return await report(task)

        resumed._scheduler.report = flaky_report
        await resumed.start()
        resumed._recycle_after_ms = 0  # Stable hourly gating is covered by the unit test.
        raw = await resumed.client()
        async with asyncio.timeout(10):
            while not (await resumed.read_initial("a"))["done"]:
                await asyncio.sleep(0.01)
            while not resumed._task.done():
                await asyncio.sleep(0.01)
        with pytest.raises(NotFoundError):
            await fs.stat(unit, ctx=resumed.context("a"))
        assert await raw.zcard(resumed.prefix("a") + "user_events") == 0
        assert attempts >= 5  # Recurrence survives more than the default three failures.
        await runtimes[-1].stop_roles(keep_reporting=True)
        await resumed._schedule("a", force=True)  # Draining writers can still register.
        assert await raw.zscore(source, identity) > time.time_ns() // 1_000_000
    finally:
        for e in engines:
            await e.aclose()
        for runtime in runtimes:
            await runtime.close()
        if engines:
            raw = await engines[-1].client()
            for pattern in (f"expiry:*{encode_component(rid)}*", engines[-1].prefix("a") + "*"):
                async for key in raw.scan_iter(match=pattern):
                    await raw.delete(key)
            await engines[-1].aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("TTL_TEST_CLUSTER"), reason="requires isolated Redis Cluster"
)
async def test_cluster_registration_reset_and_scope_isolation():
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from openviking.ttl.runtime import REMOVE

    e = DirectoryTTL(
        resource_id="cluster/{" + uuid4().hex + "}",
        redis={"mode": "cluster", "endpoints": [os.environ["TTL_TEST_CLUSTER"]]},
    )
    e.policies = AsyncMock(return_value=(TTLConfig(sessions=1), None))
    e.fs = SimpleNamespace(ls=AsyncMock(return_value=[]))
    root = "viking://user/u/sessions"
    client = await e.client()
    for account in ("a_one", "b_two"):
        await e.register_initial_backfill(TTLBackfillRequest(e.resource_id, account, (root,), True))
        await e._scan(account)
        await e.on_post_write(root + "/s/messages.jsonl", e.context(account), 123)
        key = e.prefix(account) + "sessions"
        await e.on_post_write(root + "/s/messages.jsonl", e.context(account), 100)
        await e._retry_writes()
        assert await client.zscore(key, root + "/s") == 123
        assert await client.eval(REMOVE, 1, key, root + "/s", "100") == 0
        assert await client.get(e.prefix(account) + "backfill:initial:done") == "1"
    assert e.prefix("a_one") != e.prefix("b_two")
    for account in ("a_one", "b_two"):
        await client.delete(
            *(
                e.prefix(account) + s
                for s in (
                    "sessions",
                    "backfill:initial",
                    "backfill:initial:done",
                    "backfill:pending",
                )
            )
        )
    await e.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["write", "stat", "ls"])
@pytest.mark.parametrize("cluster_days", [None, 7])
async def test_disabled_ttl_reuses_existing_acl_configuration(
    tmp_path, monkeypatch, operation, cluster_days
):
    """Compare real filesystem/ACL paths, including a disabled account in an enabled library."""
    from unittest.mock import AsyncMock

    from openviking.config.account_config import AccountConfig
    from openviking.config.manager import RuntimeConfigManager
    from openviking.config.source.file_source import FileConfigSource
    from openviking.storage.acl import AclManager
    from openviking.ttl import wiring

    fs = VikingFS(
        agfs=create_agfs_client(
            RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="local"))
        )
    )
    engine = DirectoryTTL(resource_id="disabled-baseline")
    ctx = engine.context("a")
    uri = "viking://user/u/sessions/s/messages.jsonl"
    await fs.write_file(uri, "message", ctx=ctx)
    config_path = "/local/a/_system/setting.json"
    await fs._async_agfs.ensure_parent_dirs(config_path)
    await fs._async_agfs.write(config_path, b'{"ttl":{"sessions":"disabled"}}')
    read = AsyncMock(wraps=fs._async_agfs.read)
    monkeypatch.setattr(fs._async_agfs, "read", read)
    monkeypatch.setattr(wiring, "_adapter", None)
    engine.client = AsyncMock(side_effect=AssertionError("disabled TTL used Redis"))
    reads = []
    for installed in (False, True):
        cluster = SimpleNamespace(ttl=TTLConfig(sessions=cluster_days))
        manager = RuntimeConfigManager(
            FileConfigSource(fs._async_agfs),
            base_config=cluster,
            get_config=lambda cluster=cluster: cluster,
            set_config=lambda _: None,
            build_config=lambda base, _: base,
            build_account=lambda settings: AccountConfig.model_validate(settings or {}),
        )
        fs.acl_manager = AclManager(None, manager)
        if installed:
            engine.bind_runtime(SimpleNamespace(viking_fs=fs, runtime_config_manager=manager), None)
            wiring.set_ttl_adapter(engine)
            install_ttl_hook(fs, engine)
        read.reset_mock()
        if operation == "write":
            await fs.write_file(uri, "message", ctx=ctx)
        elif operation == "stat":
            await fs.stat(uri, ctx=ctx, skip_count=True)
        else:
            await fs.ls("viking://user/u/sessions", ctx=ctx, include_abstract=False)
        reads.append(sum(call.args[0] == config_path for call in read.await_args_list))
    assert reads == [1, 1]  # ACL already loads the cold setting; TTL adds no read.
    engine.client.assert_not_called()
