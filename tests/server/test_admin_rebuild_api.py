"""Tests for reindex admin endpoint and executor behavior."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.process_result import ProcessResult
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_admin_api import ROOT_KEY
from tests.server.test_admin_api import admin_app as _admin_app_fixture
from tests.server.test_admin_api import admin_client as _admin_client_fixture
from tests.server.test_admin_api import admin_service as _admin_service_fixture

admin_service = _admin_service_fixture
admin_app = _admin_app_fixture
admin_client = _admin_client_fixture

ROOT_ACCOUNT_HEADERS = {
    "X-API-Key": ROOT_KEY,
    "X-OpenViking-Account": "default",
}


def _make_reindex_run(ctx, counters):
    from openviking.service.reindex_executor import _ReindexRunContext

    return _ReindexRunContext(ctx=ctx, counters=counters)


async def test_reindex_requires_admin_role(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "mode": "vectors_only"},
    )
    assert resp.status_code == 401


async def test_reindex_user_can_only_target_own_user_scope(monkeypatch):
    from openviking.server.routers.content import ReindexRequest, reindex
    from openviking.service.reindex_executor import ReindexExecutor

    ctx = RequestContext(
        user=UserIdentifier(account_id="reindex_user_scope", user_id="bob"),
        role=Role.USER,
    )
    seen = {}

    class FakeService:
        async def reindex(self, *, uri, mode, wait, ctx):
            seen.update(uri=uri, mode=mode, wait=wait, ctx=ctx)
            return {"status": "completed", "uri": uri, "mode": mode}

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())

    own_scope = await reindex(
        body=ReindexRequest(uri="viking://~/resources", mode="vectors_only"),
        ctx=ctx,
    )
    assert own_scope.status == "ok"
    assert seen["uri"] == "viking://user/bob/resources"
    assert seen["ctx"].role == Role.USER
    assert seen["ctx"].account_id == "reindex_user_scope"

    with pytest.raises(PermissionDeniedError):
        await reindex(
            body=ReindexRequest(uri="viking://resources/shared", mode="vectors_only"),
            ctx=ctx,
        )

    with pytest.raises(PermissionDeniedError):
        await reindex(
            body=ReindexRequest(uri="viking://user/alice/resources", mode="vectors_only"),
            ctx=ctx,
        )

    peer_ctx = RequestContext(
        user=ctx.user,
        role=Role.USER,
        actor_peer_id="peer-a",
    )
    peer_scope = await reindex(
        body=ReindexRequest(
            uri="viking://user/bob/peers/peer-a/resources",
            mode="vectors_only",
        ),
        ctx=peer_ctx,
    )
    assert peer_scope.status == "ok"
    assert seen["uri"] == "viking://user/bob/peers/peer-a/resources"

    with pytest.raises(PermissionDeniedError):
        await reindex(
            body=ReindexRequest(uri="viking://user/bob/peers/peer-b/resources"),
            ctx=peer_ctx,
        )

    # An administrator's actor-peer view is also incomplete for reconciliation.
    monkeypatch.setattr(
        "openviking.server.routers.content.get_service",
        lambda: SimpleNamespace(reindex=ReindexExecutor().execute),
    )
    admin_peer_ctx = RequestContext(user=ctx.user, role=Role.ADMIN, actor_peer_id="peer-a")
    with pytest.raises(PermissionDeniedError):
        await reindex(body=ReindexRequest(uri="viking://user/bob"), ctx=admin_peer_ctx)


async def test_reindex_rejects_unsupported_uri(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://unknown/demo", "mode": "vectors_only"},
        headers=ROOT_ACCOUNT_HEADERS,
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"


@pytest.mark.parametrize(
    "uri",
    [
        "viking://session/test/demo",
        "viking://user/default/sessions/test/demo",
    ],
)
async def test_reindex_rejects_session_uri(admin_client: httpx.AsyncClient, uri: str):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": uri, "mode": "vectors_only"},
        headers=ROOT_ACCOUNT_HEADERS,
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"


async def test_reindex_root_requires_explicit_account(admin_client: httpx.AsyncClient):
    resp = await admin_client.post(
        "/api/v1/content/reindex",
        json={"uri": "viking://resources/demo", "mode": "vectors_only"},
        headers={"X-API-Key": ROOT_KEY},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error"


@pytest.mark.asyncio
async def test_reindex_resource_vectors_only_wait_true(tmp_path, monkeypatch):
    import asyncio

    import ragfs_python

    from openviking.server.routers.content import ReindexRequest, reindex
    from openviking.service.reindex_executor import ReindexExecutor
    from openviking.service.task_store import PersistentTaskStore
    from openviking.service.task_tracker import TaskStatus, TaskTracker
    from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
    from openviking.storage.queuefs.named_queue import DequeueHandlerBase
    from openviking.storage.queuefs.queue_manager import QueueManager
    from openviking.storage.queuefs.reindex_processor import ReindexProcessor
    from openviking.storage.viking_fs import VikingFS

    agfs = ragfs_python.RAGFSBindingClient()
    agfs.mount("localfs", "/local", {"local_dir": str(tmp_path)})
    agfs.mount("queuefs", "/queue", {"backend": "memory"})
    fs = VikingFS(agfs=agfs)
    tracker = TaskTracker(PersistentTaskStore(agfs))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    manager = QueueManager(agfs)
    queue = manager.get_queue("Reindex", ReindexProcessor(fs), allow_create=True)
    child_enqueued, child_started, release_child = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class EmbeddingHandler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            child_started.set()
            await release_child.wait()
            return ProcessResult.success({"vector_written": True})

    embedding = manager.get_queue("Embedding", EmbeddingHandler(), allow_create=True)
    ctx = RequestContext(user=UserIdentifier("test", "alice"), role=Role.ROOT)
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    uri = "viking://resources/demo.md"
    service = SimpleNamespace(
        viking_fs=fs,
        vikingdb_manager=SimpleNamespace(has_queue_manager=True),
        reindex=ReindexExecutor().execute,
    )
    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: service)
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: service)
    monkeypatch.setattr("openviking.server.dependencies.get_service", lambda: service)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)

    async def rebuild(_self, *, uri, mode, run):
        assert mode == "vectors_only"
        assert run.ingest_options.search_tags == ["team=search", "env=prod"]
        assert run.ingest_options.search_tag_mode == "append"
        await embedding.enqueue(
            EmbeddingMsg(message="source", context_data={"uri": uri, "account_id": ctx.account_id})
        )
        run.counters.scanned_records = run.counters.rebuilt_records = 1
        child_enqueued.set()

    monkeypatch.setattr(ReindexExecutor, "_reindex_resource", rebuild)
    await fs.write_file(uri, "source", ctx=ctx)
    request = asyncio.create_task(
        reindex(
            body=ReindexRequest(
                uri=uri,
                mode="vectors_only",
                wait=True,
                tags=["Team=Search", "env=prod"],
                tag_mode="append",
            ),
            ctx=ctx,
        )
    )
    processing = child = None
    try:
        deadline = asyncio.get_running_loop().time() + 2
        while not (messages := await queue.snapshot()):
            if request.done():
                request.result()
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.001)
        task_id = json.loads(messages[0]["data"])["task_id"]
        while (await tracker.get(task_id, **owner)).stage != "queued":
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.001)
        processing = asyncio.create_task(queue.dequeue())
        await asyncio.wait_for(child_enqueued.wait(), 2)
        child = asyncio.create_task(embedding.dequeue())
        await asyncio.wait_for(child_started.wait(), 2)
        assert not request.done()
        assert (await tracker.get(task_id, **owner)).status is TaskStatus.RUNNING
        release_child.set()
        await asyncio.wait_for(asyncio.gather(processing, child), 2)
        response = await asyncio.wait_for(request, 2)
        assert response.status == "ok"
        assert response.result["status"] == "completed"
        assert response.result["object_type"] == "resource"
        assert response.result["rebuilt_records"] == 1
        assert response.result["failed_records"] == 0
        assert "task_id" not in response.result
        assert (await tracker.get(task_id, **owner)).status is TaskStatus.COMPLETED
        assert await queue.snapshot() == await embedding.snapshot() == []
    finally:
        release_child.set()
        pending = [task for task in (request, processing, child) if task is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        agfs.close()


@pytest.mark.asyncio
async def test_reindex_rfv_skips_complete_file_without_enqueue_or_second_read(monkeypatch):
    from unittest.mock import AsyncMock

    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters
    from openviking.utils.content_hash import content_md5

    uri = "viking://resources/demo.md"
    body = b"same body"

    class FakeFS:
        stat = AsyncMock(return_value={"isDir": False})
        read_file_bytes = AsyncMock(return_value=body)

    class FakeDB:
        async def get_reindex_inventory_under_uri(
            self, target, *, ctx, output_fields, recursive=True, include_direct_children=False
        ):
            assert target == uri
            assert recursive is False
            assert include_direct_children is False
            return {
                "file-l2": {
                    "id": "file-l2",
                    "uri": uri,
                    "level": 2,
                    "md5": content_md5(body),
                }
            }

    resource_processor = SimpleNamespace(_enqueue_index_actions=AsyncMock())
    service = SimpleNamespace(
        viking_fs=FakeFS(),
        vikingdb_manager=FakeDB(),
        _resource_processor=resource_processor,
    )
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: service)
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )
    counters = _ReindexCounters()

    await ReindexExecutor()._reindex_rfv(
        uri=uri,
        mode="vectors_only",
        context_type="resource",
        recursive=False,
        run=_make_reindex_run(ctx, counters),
    )

    resource_processor._enqueue_index_actions.assert_not_awaited()
    service.viking_fs.read_file_bytes.assert_awaited_once_with(uri, ctx=ctx)
    assert counters.scanned_records == 1
    assert counters.rebuilt_records == 0


@pytest.mark.asyncio
async def test_reindex_rfv_deletes_missing_root_with_owner_context(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    root = "viking://user/bob/resources/gone"
    inventory = {
        "root-l0": {"id": "root-l0", "uri": root, "level": 0},
        "root-l1": {"id": "root-l1", "uri": root, "level": 1},
        "file-l2": {"id": "file-l2", "uri": root + "/old.md", "level": 2},
    }
    db = SimpleNamespace(strict_delete=AsyncMock(return_value=3))
    service = SimpleNamespace(viking_fs=SimpleNamespace(), vikingdb_manager=db)
    monkeypatch.setattr("openviking.service.reindex_executor.get_service", lambda: service)
    ctx = RequestContext(user=UserIdentifier("test", "admin"), role=Role.ADMIN)
    counters = _ReindexCounters()

    await ReindexExecutor()._reindex_rfv(
        uri=root,
        mode="vectors_only",
        context_type="resource",
        recursive=True,
        run=_make_reindex_run(ctx, counters),
        root_is_dir=True,
        root_exists=False,
        formal_entries=[],
        vector_inventory=inventory,
    )

    (deleted,) = db.strict_delete.await_args.args
    assert set(deleted) == set(inventory)
    assert db.strict_delete.await_args.kwargs["ctx"].user == UserIdentifier("test", "bob")
    assert counters.deleted_records == 3


@pytest.mark.asyncio
async def test_reindex_memory_semantic_request_survives_write_deduplication(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters
    from openviking.storage.queuefs.semantic_msg import SemanticMsg
    from openviking.storage.queuefs.semantic_queue import SemanticQueue
    from openviking.utils.ingest_options import IngestOptions
    from openviking_cli.exceptions import NotFoundError

    root = "viking://user/bob/memories/events"
    ctx = RequestContext(user=UserIdentifier("test", "bob"), role=Role.USER)
    queue_fs = SimpleNamespace(mkdir=AsyncMock(), write=AsyncMock(return_value="queued"))
    monkeypatch.setattr(
        "openviking.storage.queuefs.named_queue.AsyncAGFSClient", lambda _: queue_fs
    )
    queue = SemanticQueue(object(), "/queue", "Semantic")
    manager = SimpleNamespace(SEMANTIC="Semantic", get_queue=lambda *args, **kw: queue)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    await queue.enqueue(
        SemanticMsg(
            uri=root,
            context_type="memory",
            account_id="test",
            user_id="bob",
            peer_id="bob",
        )
    )
    fs = SimpleNamespace(
        stat=AsyncMock(return_value={"isDir": True}),
        exists=AsyncMock(return_value=True),
        tree=AsyncMock(return_value=[{"uri": root + "/live.md", "isDir": False}]),
        read_file=AsyncMock(side_effect=NotFoundError(root)),
    )
    db = SimpleNamespace(
        get_reindex_inventory_under_uri=AsyncMock(
            return_value={
                "gone": {"id": "gone", "uri": root + "/gone.md", "level": 2},
            }
        ),
        strict_delete=AsyncMock(return_value=1),
    )
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: SimpleNamespace(vikingdb_manager=db),
    )
    counters = _ReindexCounters()
    run = _make_reindex_run(ctx, counters)
    run.ingest_options = IngestOptions.from_search_tags(["team=search"], mode="append")

    await ReindexExecutor()._reindex_memory(uri=root, mode="semantic_and_vectors", run=run)

    # Explicit reindex survives ordinary write deduplication.
    assert queue_fs.write.await_count == 2
    queued = SemanticMsg.from_dict(json.loads(queue_fs.write.await_args.args[1]))
    assert queued.uri == root and queued.recursive
    assert queued.use_hierarchical_aggregation and not queued.skip_vectorization
    assert queued.ingest_options == run.ingest_options
    assert counters.deleted_records == 1
    db.strict_delete.assert_awaited_once_with(["gone"], ctx=ctx)


@pytest.mark.asyncio
async def test_reindex_rejects_non_recursive_namespace_before_starting_work(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor
    from openviking_cli.exceptions import InvalidArgumentError

    def unexpected_tracker():
        pytest.fail("unsupported non-recursive reindex must not start task tracking")

    monkeypatch.setattr("openviking.service.reindex_executor.get_task_tracker", unexpected_tracker)
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )
    with pytest.raises(InvalidArgumentError, match="recursive=false.*namespace"):
        await ReindexExecutor().execute(
            uri="viking://user/alice",
            mode="semantic_and_vectors",
            recursive=False,
            wait=True,
            ctx=ctx,
        )


@pytest.mark.asyncio
async def test_reindex_memory_cancellation_waits_for_inflight_reads(monkeypatch):
    import asyncio

    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    root = "viking://user/bob/memories/events"
    files = [root + "/a.md", root + "/b.md"]
    started, cleaning = asyncio.Queue(), asyncio.Queue()
    finish = asyncio.Event()
    settled = set()

    async def read(uri, **kwargs):
        started.put_nowait(uri)
        try:
            await asyncio.Future()
        finally:
            cleaning.put_nowait(uri)
            await finish.wait()
            settled.add(uri)

    fs = SimpleNamespace()
    db = SimpleNamespace(strict_delete=AsyncMock())
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: SimpleNamespace(vikingdb_manager=db),
    )
    executor = ReindexExecutor()
    executor._read_memory_body = read
    executor._reindex_memory_directory_chain = AsyncMock(return_value={})
    executor._upsert_context = AsyncMock()
    monkeypatch.setattr(executor, "_effective_file_vectorization_concurrency", lambda: 2)
    ctx = RequestContext(user=UserIdentifier("test", "bob"), role=Role.USER)
    operation = asyncio.create_task(
        executor._reindex_memory_vectors(
            uri=root,
            ctx=ctx,
            counters=_ReindexCounters(),
            root_is_dir=True,
            formal_entries=[{"uri": uri, "isDir": False} for uri in files],
            vector_inventory={"gone": {"uri": root + "/gone.md", "level": 2}},
        )
    )
    try:
        await asyncio.wait_for(started.get(), 1)
        await asyncio.wait_for(started.get(), 1)
        operation.cancel()
        await asyncio.wait_for(cleaning.get(), 1)
        await asyncio.wait_for(cleaning.get(), 1)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 1)
        assert settled == set(files)
        db.strict_delete.assert_not_awaited()
        executor._upsert_context.assert_not_awaited()
    finally:
        finish.set()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.asyncio
async def test_reindex_memory_l2_strips_memory_fields_from_abstract(monkeypatch):
    from openviking.service.reindex_executor import (
        ReindexExecutor,
        _ReindexCounters,
        _SourceRead,
    )

    class FakeVikingFS:
        async def exists(self, uri, ctx=None):
            return True

        async def stat(self, uri, ctx=None, skip_count=True):
            return {"isDir": False}

    seen = {}
    raw_body = (
        "User has a preference for watermelon, as mentioned in the conversation: "
        '\'我爱吃西瓜\'. <!-- MEMORY_FIELDS { "user": "user", "topic": "food_preference" } -->'
    )

    async def fake_read_memory_body(self, uri, *, ctx):
        return _SourceRead(exists=True, text=raw_body)

    async def fake_upsert_context(self, **kwargs):
        seen[kwargs["uri"]] = kwargs

    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(ReindexExecutor, "_read_memory_body", fake_read_memory_body)
    monkeypatch.setattr(ReindexExecutor, "_upsert_context", fake_upsert_context)

    service = ReindexExecutor()
    counters = _ReindexCounters()
    ctx = RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice"),
        role=Role.ROOT,
    )

    await service._reindex_memory_vectors(
        uri="viking://user/default/memories/preferences/food_preference.md",
        counters=counters,
        ctx=ctx,
        vector_inventory={},
    )

    assert (
        seen["viking://user/default/memories/preferences/food_preference.md"]["abstract"]
        == "User has a preference for watermelon, as mentioned in the conversation: '我爱吃西瓜'."
    )
    assert (
        seen["viking://user/default/memories/preferences/food_preference.md"]["vector_text"]
        == raw_body
    )


@pytest.mark.asyncio
async def test_reindex_memory_vectors_preserves_live_sources_and_cleans_orphans(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters
    from openviking_cli.exceptions import NotFoundError

    root = "viking://user/bob/memories/preferences"
    nested = root + "/user"
    hidden, broken, literal = nested + "/.note.md", nested + "/broken.md", root + "/real#chunk_0000"
    sources = {
        root + "/.abstract.md": "root abstract",
        root + "/.overview.md": OSError("overview unavailable"),
        nested + "/.overview.md": "nested overview",
        hidden: "likes spicy food",
        broken: OSError("body unavailable"),
        literal: "real memory",
    }
    records = {
        "hidden": (hidden, 2),
        "broken": (broken, 2),
        "literal": (literal, 2),
        "root-l0": (root, 0),
        "root-l1": (root, 1),
        "missing-sidecar": (nested, 0),
        "gone-a": (root + "/gone.md", 2),
        "gone-b": (root + "/gone.md", 2),
        "legacy-chunk": (hidden + "#chunk_0000", 2),
    }
    inventory = {
        key: {"id": key, "uri": uri, "level": level} for key, (uri, level) in records.items()
    }
    stored = dict(inventory)

    async def read(uri, **kwargs):
        if uri not in sources:
            raise NotFoundError(uri)
        if isinstance(sources[uri], Exception):
            raise sources[uri]
        return sources[uri]

    async def tree(uri, *, show_all_hidden, node_limit, level_limit, **kwargs):
        assert uri == root and node_limit is None and level_limit is None
        return [{"uri": nested, "isDir": True}] + [
            {"uri": path, "isDir": False}
            for path in sources
            if show_all_hidden or not path.rsplit("/", 1)[-1].startswith(".")
        ]

    async def delete(ids, **kwargs):
        for key in ids:
            del stored[key]
        return len(ids)

    fs = SimpleNamespace(
        exists=AsyncMock(side_effect=lambda uri, **kw: uri == root or uri in sources),
        stat=AsyncMock(return_value={"isDir": True}),
        tree=tree,
        read_file=read,
    )
    db = SimpleNamespace(
        get_reindex_inventory_under_uri=AsyncMock(return_value=inventory),
        strict_delete=AsyncMock(side_effect=delete),
        enqueue_embedding_msg=AsyncMock(return_value=True),
    )
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: SimpleNamespace(vikingdb_manager=db),
    )
    executor = ReindexExecutor()
    monkeypatch.setattr(executor, "_effective_file_vectorization_concurrency", lambda: 2)
    counters = _ReindexCounters()
    ctx = RequestContext(user=UserIdentifier("test", "admin"), role=Role.ADMIN)

    await executor._reindex_memory(
        uri=root, mode="vectors_only", run=_make_reindex_run(ctx, counters)
    )

    queued = [call.args[0].context_data for call in db.enqueue_embedding_msg.await_args_list]
    assert {(record["uri"], record["level"]) for record in queued} == {
        (root, 0),
        (nested, 1),
        (hidden, 2),
        (literal, 2),
    }
    assert all(record["owner_user_id"] == "bob" for record in queued)
    assert set(stored) == {"hidden", "broken", "literal", "root-l0", "root-l1"}
    assert counters.deleted_records == 4
    assert counters.rebuilt_records == 4
    assert counters.failed_records == 2
    assert any("overview unavailable" in warning for warning in counters.warnings)
    assert any("body unavailable" in warning for warning in counters.warnings)


@pytest.mark.asyncio
async def test_reindex_user_namespace_partitions_memory_skill_and_resource(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters
    from openviking_cli.exceptions import NotFoundError

    root = "viking://user/bob"
    directories = {
        root: [
            "resources",
            "skills",
            "memories",
            "peers",
            "notes",
            "sessions",
            "privacy",
            "settings",
        ],
        root + "/skills": ["live"],
        root + "/memories": [],
        root + "/peers": [],
    }
    children = {
        uri: [{"uri": uri + "/" + name, "isDir": True} for name in names]
        for uri, names in directories.items()
    }
    children[root].append({"uri": root + "/persona.md", "isDir": False})
    hidden = root + "/memories/.note.md"
    children[root + "/memories"].append({"uri": hidden, "isDir": False})
    records = {
        "skill": {"uri": root + "/skills/gone/SKILL.md", "level": 2},
        "peer": {"uri": root + "/peers/gone/memories/events/a.md", "level": 2},
    }

    async def stat(uri, **kwargs):
        if uri not in directories and uri != root + "/resources":
            raise NotFoundError(uri)
        return {"isDir": True}

    async def listing(uri, *, offset, node_limit, show_all_hidden, **kwargs):
        entries = [
            entry
            for entry in children[uri]
            if entry["isDir"]
            or show_all_hidden
            or not entry["uri"].rsplit("/", 1)[-1].startswith(".")
        ]
        return entries[offset : offset + node_limit]

    async def inventory(uri, *, recursive, **kwargs):
        return {
            key: dict(record, id=key)
            for key, record in records.items()
            if recursive and record["uri"].startswith(uri + "/")
        }

    fs = SimpleNamespace(stat=stat, ls=AsyncMock(side_effect=listing))
    db = SimpleNamespace(get_reindex_inventory_under_uri=AsyncMock(side_effect=inventory))
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: SimpleNamespace(vikingdb_manager=db),
    )
    executor = ReindexExecutor()
    executor._reindex_rfv = AsyncMock()
    executor._reindex_memory = AsyncMock()
    ctx = RequestContext(user=UserIdentifier("test", "bob"), role=Role.USER)

    await executor._reindex_user_namespace(
        uri=root, mode="vectors_only", run=_make_reindex_run(ctx, _ReindexCounters())
    )

    scopes = {call.kwargs["uri"]: call.kwargs for call in executor._reindex_rfv.await_args_list}
    assert set(scopes) == {
        root + suffix
        for suffix in ("/resources", "/skills/live", "/skills/gone", "/notes", "/persona.md")
    }
    assert scopes[root + "/skills/gone"]["root_exists"] is False
    assert set(scopes[root + "/skills/gone"]["vector_inventory"]) == {"skill"}
    memories = {
        call.kwargs["uri"]: call.kwargs for call in executor._reindex_memory.await_args_list
    }
    assert set(memories) == {root + "/memories", root + "/peers/gone/memories"}
    assert memories[root + "/memories"]["formal_entries"] == [{"uri": hidden, "isDir": False}]
    assert memories[root + "/peers/gone/memories"]["root_exists"] is False
    assert set(memories[root + "/peers/gone/memories"]["vector_inventory"]) == {"peer"}
    assert not any(
        part in call.args[0]
        for call in fs.ls.await_args_list
        for part in ("/sessions", "/privacy", "/settings")
    )


@pytest.mark.asyncio
async def test_reindex_global_namespace_partitions_user_and_resources(monkeypatch):
    from openviking.service.reindex_executor import ReindexExecutor, _ReindexCounters

    children = {
        "viking://agent/skills": [{"uri": "viking://agent/skills/demo", "isDir": True}],
        "viking://user": [{"uri": "viking://user/bob", "isDir": True}],
    }
    fs = SimpleNamespace(
        stat=AsyncMock(return_value={"isDir": True}),
        ls=AsyncMock(side_effect=lambda uri, **kw: children[uri]),
    )
    db = SimpleNamespace(get_reindex_inventory_under_uri=AsyncMock(return_value={}))
    monkeypatch.setattr("openviking.service.reindex_executor.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: SimpleNamespace(vikingdb_manager=db),
    )
    executor = ReindexExecutor()
    executor._reindex_user_namespace = AsyncMock()
    executor._reindex_rfv = AsyncMock()
    ctx = RequestContext(user=UserIdentifier("test", "admin"), role=Role.ADMIN)

    await executor._reindex_global_namespace(
        uri="viking://", mode="vectors_only", run=_make_reindex_run(ctx, _ReindexCounters())
    )

    assert {
        (call.kwargs["uri"], call.kwargs["context_type"])
        for call in executor._reindex_rfv.await_args_list
    } == {
        ("viking://resources", "resource"),
        ("viking://agent/skills/demo", "skill"),
    }
    assert [call.kwargs["uri"] for call in executor._reindex_user_namespace.await_args_list] == [
        "viking://user/bob"
    ]
