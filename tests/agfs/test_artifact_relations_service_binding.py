"""Exercise durable worker, write registration and ACL with real Git and storage."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs import AsyncAGFSClient, get_binding_client
from openviking.server.identity import RequestContext, Role
from openviking.service.artifact_relations_models import (
    ArtifactRelations,
    RelationSource,
    RelationSources,
)
from openviking.service.artifact_relations_service import ArtifactRelationsService
from openviking.service.artifact_relations_store import ArtifactRelationsStore
from openviking.service.compile_targets import CompileStateStore
from openviking_cli.exceptions import ConflictError, FailedPreconditionError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier

ROOT = "viking://resources/wiki"


class NativeFS:
    def __init__(self, client):
        self._async_agfs = client
        self.denied = set()
        self.body_reads = []

    def _uri_to_tree_path(self, uri, **kwargs):
        return uri.removeprefix("viking://")

    def _tree_path_to_uri(self, path):
        return "viking://" + path

    async def _ensure_access(self, uri, ctx):
        if uri in self.denied:
            raise PermissionDeniedError("Denied")

    def authorize_read(self, ctx, targets):
        return ctx

    async def _can_access_many(self, uris, ctx):
        return {uri: uri not in self.denied for uri in uris}

    async def commit(self, *, paths, ctx, message, **kwargs):
        return await self._async_agfs.run(
            "git_commit",
            account=ctx.account_id,
            branch="main",
            paths=[self._uri_to_tree_path(uri) for uri in paths],
            message=message,
            author_name="test",
            author_email="test@example.com",
            **kwargs,
        )

    async def diff_paths(self, *, dirs, base, head, ctx):
        changes = await self._async_agfs.run(
            "git_diff_paths",
            account=ctx.account_id,
            base=base,
            head=head,
            paths=[self._uri_to_tree_path(uri) for uri in dirs],
        )
        return [
            {"uri": self._tree_path_to_uri(path), "change_type": change} for path, change in changes
        ]

    async def write(self, uri, data, ctx):
        path = f"/local/{ctx.account_id}/{self._uri_to_tree_path(uri)}"
        await self._async_agfs.ensure_parent_dirs(path)
        await self._async_agfs.write(path, data)


@pytest.fixture
async def environment(tmp_path):
    local = tmp_path / "local"
    git = tmp_path / "git"
    local.mkdir()
    git.mkdir()
    config = tmp_path / "ragfs.toml"
    config.write_text(f'[git]\nenabled=true\nbackend="local"\n[git.local]\nbase_dir="{git}"\n')
    binding, _ = get_binding_client()
    client = binding(git_config_path=str(config))
    client.mount("localfs", "/local", {"local_dir": str(local)})
    client.mount("queuefs", "/queue", {"backend": "memory"})
    agfs = AsyncAGFSClient(client)
    from openviking.storage.queuefs.queue_manager import QueueManager

    manager = QueueManager(client)
    store = CompileStateStore(agfs)
    async with store.edit("acme", ROOT, create=True):
        pass
    fs = NativeFS(agfs)
    service = ArtifactRelationsService(store, fs, manager)
    manager.get_queue("ArtifactRelations", dequeue_handler=service, allow_create=True)
    ctx = RequestContext(UserIdentifier("acme", "alice"), Role.USER)
    return service, fs, ctx


async def save(service, fs, ctx, files):
    async with service.mutation(list(files), ctx):
        for uri, body in files.items():
            await fs.write(uri, body, ctx)


@pytest.mark.asyncio
async def test_worker_fixed_snapshot_followup_and_dynamic_acl(environment):
    service, fs, ctx = environment
    await save(service, fs, ctx, {ROOT + "/a.md": b"[future](b.md)"})
    queue = service._manager.get_queue("ArtifactRelations")
    await queue.dequeue()
    first = await service.query(ROOT, ctx)
    assert not any(edge["type"] == "references" for edge in first["edges"])
    original = service.builder.build
    started, resume = asyncio.Event(), asyncio.Event()

    async def paused(*args):
        started.set()
        await resume.wait()
        return await original(*args)

    service.builder.build = paused
    await save(service, fs, ctx, {ROOT + "/b.md": b"target"})
    running = asyncio.create_task(queue.dequeue())
    await started.wait()
    assert await service.query(ROOT, ctx) == first
    await save(service, fs, ctx, {ROOT + "/b.md": b"changed", ROOT + "/c.md": b"new"})
    resume.set()
    await running
    service.builder.build = original
    await queue.dequeue()
    graph = await service.query(ROOT, ctx)
    assert any(
        edge["type"] == "references" and edge["to"] == ROOT + "/b.md" for edge in graph["edges"]
    )
    assert {"uri": ROOT + "/c.md"} in graph["nodes"]
    fs.denied.add(ROOT + "/b.md")
    filtered = await service.query(ROOT, ctx)
    assert {"uri": ROOT + "/b.md"} not in filtered["nodes"]
    assert not any(edge["to"] == ROOT + "/b.md" for edge in filtered["edges"])


@pytest.mark.asyncio
async def test_compile_authorization_failure_and_revival(environment):
    service, fs, ctx = environment
    async with service._targets.edit(ctx.account_id, ROOT) as target:
        target.update(active_operation_id="op", active_task_id="task")
    await service._targets.put_operation(
        ctx.account_id,
        ROOT,
        {
            "operation_id": "op",
            "actor": "alice",
            "finished_at": None,
            "meta": {"write_token": "secret"},
        },
    )
    for caller in (
        ctx,
        replace(ctx, compile_write_token="wrong"),
        replace(ctx, user=UserIdentifier("acme", "other"), compile_write_token="secret"),
    ):
        with pytest.raises(ConflictError):
            async with service.mutation([ROOT + "/a.md"], caller):
                pytest.fail("Unauthorized write was admitted")
    trusted = replace(ctx, compile_write_token="secret")
    await save(service, fs, trusted, {ROOT + "/a.md": b"a"})
    head = (await fs.commit(paths=[ROOT], ctx=trusted, message="compile"))["commit_oid"]
    await service.changed("acme", ROOT, head, operation_id="op")
    service.builder.build = AsyncMock(side_effect=ValueError("invalid graph"))
    for _ in range(3):
        await service.process("acme", ROOT)
    with pytest.raises(FailedPreconditionError, match="invalid graph"):
        await service.ready("acme", ROOT, head, "op")
    await service.changed("acme", ROOT, head, operation_id="op")
    assert (await service.store.get_publication("acme", ROOT))[0].status == "failed"
    async with service._targets.edit("acme", ROOT) as target:
        target.update(status="deleted", active_operation_id=None, active_task_id=None)
    async with service._targets.edit("acme", ROOT, create=True):
        pass
    assert await service.store.get_publication("acme", ROOT) == (None, None)


@pytest.mark.asyncio
async def test_pending_notifications_deduplicate_but_active_retains_followup(environment):
    service, _, _ = environment
    queue = service._manager.get_queue("ArtifactRelations")
    await asyncio.gather(*(service._enqueue("acme", ROOT) for _ in range(20)))
    assert await queue.size() == 1
    delivery = await queue.dequeue_raw()
    await asyncio.gather(*(service._enqueue("acme", ROOT) for _ in range(20)))
    assert await queue.size() == 1
    await queue.ack(delivery["id"], delivery)
    assert await queue.size() == 1


@pytest.mark.asyncio
async def test_unmanaged_write_does_not_create_graph_snapshot(environment):
    service, fs, ctx = environment
    uri = "viking://resources/unmanaged/page.md"
    fs.commit = AsyncMock(side_effect=AssertionError("Unmanaged write must not snapshot"))
    async with service.mutation([uri], ctx):
        await fs.write(uri, b"ordinary resource", ctx)
        await service.register_writes([uri], None, ctx)
    fs.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_write_discovers_nested_targets_without_summary_scan(environment):
    service, fs, ctx = environment
    nested = ROOT + "/nested"
    async with service._targets.edit(ctx.account_id, nested, create=True):
        pass
    service._targets.list_summaries = AsyncMock(
        side_effect=AssertionError("Ordinary writes must not enumerate Compile targets")
    )
    service._enqueue = AsyncMock()

    await save(service, fs, ctx, {nested + "/page.md": b"page"})

    assert service._enqueue.await_count == 2
    assert {call.args[1] for call in service._enqueue.await_args_list} == {ROOT, nested}


@pytest.mark.asyncio
async def test_repeated_change_registers_and_enqueues_once(environment):
    service, fs, ctx = environment
    await fs.write(ROOT + "/page.md", b"page", ctx)
    head = (await fs.commit(paths=[ROOT], ctx=ctx, message="snapshot"))["commit_oid"]
    service._enqueue = AsyncMock()

    await service.changed(ctx.account_id, ROOT, head)
    state_path = (
        service.store._directory(ctx.account_id, ROOT)
        + "/"
        + (await service._targets.get(ctx.account_id, ROOT))["generation"]
        + "/artifact_relations_state.json"
    )
    first_state = await fs._async_agfs.read(state_path)
    await service.changed(ctx.account_id, ROOT, head)

    assert service._enqueue.await_count == 1
    assert await fs._async_agfs.read(state_path) == first_state


@pytest.mark.asyncio
async def test_start_runs_recovery_once(environment):
    service, _, _ = environment
    service.recover = AsyncMock()

    await service.start()
    await service._recovery_task
    await service.stop()

    service.recover.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_only_reenqueues_existing_queued_state(environment):
    service, fs, ctx = environment
    await fs.write(ROOT + "/page.md", b"page", ctx)
    head = (await fs.commit(paths=[ROOT], ctx=ctx, message="snapshot"))["commit_oid"]
    await service.changed(ctx.account_id, ROOT, head)
    state_path = (
        service.store._directory(ctx.account_id, ROOT)
        + "/"
        + (await service._targets.get(ctx.account_id, ROOT))["generation"]
        + "/artifact_relations_state.json"
    )
    first_state = await fs._async_agfs.read(state_path)
    service._enqueue = AsyncMock()

    await service.recover()

    service._enqueue.assert_awaited_once_with(ctx.account_id, ROOT)
    assert await fs._async_agfs.read(state_path) == first_state


@pytest.mark.asyncio
@pytest.mark.parametrize("completed_compile", [False, True], ids=["ordinary", "completed-compile"])
async def test_idle_recovery_reads_status_without_loading_graph(environment, completed_compile):
    service, fs, ctx = environment
    await save(service, fs, ctx, {ROOT + "/a.md": b"page"})
    await service._manager.get_queue("ArtifactRelations").dequeue()
    if completed_compile:
        head = (await service._targets.get(ctx.account_id, ROOT))["wiki_head"]
        async with service._targets.edit(ctx.account_id, ROOT) as target:
            target.update(active_operation_id="completed", active_task_id="task")
        await service.store.request(ctx.account_id, ROOT, head, compile_operation_id="completed")
        async with service._targets.edit(ctx.account_id, ROOT) as target:
            target.update(active_operation_id=None, active_task_id=None)
    agfs = fs._async_agfs
    agfs.read = AsyncMock(wraps=agfs.read)
    service._enqueue = AsyncMock()
    await service.recover()
    await service.recover()
    paths = [call.args[0] for call in agfs.read.await_args_list]
    assert sum(path.endswith("/artifact_relations_state.json") for path in paths) == 2
    assert not any(
        path.endswith(("/artifact_relations.json", "/relation_sources.json")) for path in paths
    )
    service._enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_query_uses_one_batch_and_filters_both_edge_ends(environment):
    service, fs, ctx = environment
    await save(service, fs, ctx, {ROOT + "/a.md": b"[b](b.md)", ROOT + "/b.md": b"b"})
    await service._manager.get_queue("ArtifactRelations").dequeue()
    fs._can_access_many = AsyncMock(wraps=fs._can_access_many)
    fs._ensure_access = AsyncMock(wraps=fs._ensure_access)
    fs.denied.add(ROOT + "/b.md")
    graph = await service.query(ROOT, ctx)
    fs._can_access_many.assert_awaited_once()
    fs._ensure_access.assert_awaited_once_with(ROOT, ctx)
    assert {"uri": ROOT + "/b.md"} not in graph["nodes"]
    assert not any(
        edge["from"] == ROOT + "/b.md" or edge["to"] == ROOT + "/b.md" for edge in graph["edges"]
    )
    fs.denied.clear()
    graph = await service.query(ROOT, ctx)
    assert {"uri": ROOT + "/b.md"} in graph["nodes"]


@pytest.fixture(params=[False, True], ids=["plaintext", "encrypted"])
def native_clients(tmp_path, request):
    binding, _ = get_binding_client()
    root = tmp_path / "local"
    root.mkdir()
    config = {"service_name": "artifact-store-test"}
    if request.param:
        config["encryption"] = {"root_key": bytes(range(32)), "provider_type": 0}
    clients = [binding(config=config), binding(config=config)]
    for client in clients:
        client.mount("localfs", "/local", {"local_dir": str(root)})
    return [AsyncAGFSClient(client) for client in clients], root


@pytest.mark.asyncio
async def test_native_conditional_write_fences_stale_bytes(native_clients):
    clients, _ = native_clients
    path = "/local/acme/cas/state.json"
    await clients[0].ensure_parent_dirs(path)
    assert await clients[0].conditional_write(path, None, b"initial")
    assert not await clients[1].conditional_write(path, None, b"duplicate")
    assert await clients[1].conditional_write(path, b"initial", b"latest")
    assert not await clients[0].conditional_write(path, b"initial", b"stale")
    assert await clients[0].read(path) == b"latest"


@pytest.mark.asyncio
async def test_native_replace_released_lease_is_rejected(native_clients):
    clients, _ = native_clients
    client = clients[0]
    directory = "/local/acme/replace"
    lease = await client.pathlock_acquire_tree(directory)
    ctx = {"account_id": "acme", "lease_ref": lease["lease_ref"]}
    src, dst = directory + "/temp", directory + "/published"
    await client.ensure_parent_dirs(src, fs_ctx=ctx)
    await client.write(src, b"new", fs_ctx=ctx)
    await client.write(dst, b"old", fs_ctx=ctx)
    await client.pathlock_release(lease, fs_ctx=ctx)
    with pytest.raises(Exception, match="lease|Lease"):
        await client.replace(src, dst, fs_ctx=ctx)
    assert await clients[1].read(dst) == b"old"
    await client.replace(src, dst)
    assert await clients[1].read(dst) == b"new"


@pytest.mark.asyncio
async def test_native_two_clients_merge_and_publish(native_clients):
    clients, root = native_clients
    account, to = "acme", ROOT
    compile_stores = [CompileStateStore(client) for client in clients]
    async with compile_stores[0].edit(account, to, create=True) as record:
        record.update(active_operation_id="op", active_task_id="task")
    stores = [ArtifactRelationsStore(store) for store in compile_stores]
    await asyncio.gather(
        *[
            store.merge_sources(
                account,
                to,
                {to + f"/{i}.md": RelationSource(blob_oid=f"oid{i}", links=[])},
                parser_version="v1",
                compile_operation_id="op",
            )
            for i, store in enumerate(stores)
        ]
    )
    _, sources, _ = await stores[1].get_baseline(account, to)
    assert len(sources.pages) == 2 and sources.build_id is None
    await stores[0].request(account, to, "snapshot", compile_operation_id="op")
    claimed = await stores[1].claim(account, to)
    sources.build_id = claimed.build_id
    graph = ArtifactRelations(
        build_id=claimed.build_id,
        target_snapshot_id="snapshot",
        generated_at=1,
        nodes=[to + "/", *sorted(sources.pages)],
        edges=[],
    )
    await stores[0].publish(account, to, claimed, sources, graph)
    state, read_graph = await stores[1].get_publication(account, to)
    assert state.status == "ready" and read_graph == graph
    target = await compile_stores[0].get(account, to)
    directory = root / (
        stores[0]._directory(account, to).removeprefix("/local/") + "/" + target["generation"]
    )
    business_files = sorted({path.name for path in directory.rglob("*.json")})
    assert business_files == [
        "artifact_relations.json",
        "artifact_relations_state.json",
        "relation_sources.json",
    ]
    assert not list(directory.rglob(".replace-*.tmp"))


@pytest.mark.asyncio
async def test_native_repeated_replacement_keeps_complete_graph(native_clients):
    clients, _ = native_clients
    account, to = "acme", ROOT
    compile_store = CompileStateStore(clients[0])
    async with compile_store.edit(account, to, create=True):
        pass
    stores = [ArtifactRelationsStore(CompileStateStore(client)) for client in clients]
    for i in range(3):
        store = stores[i % 2]
        await store.request(account, to, f"W{i}")
        claimed = await store.claim(account, to)
        graph = ArtifactRelations(
            build_id=claimed.build_id,
            target_snapshot_id=f"W{i}",
            generated_at=i,
            nodes=[to + "/"],
            edges=[],
        )
        await store.publish(
            account,
            to,
            claimed,
            RelationSources(parser_version="v1", build_id=claimed.build_id),
            graph,
        )
        _, read_graph = await stores[(i + 1) % 2].get_publication(account, to)
        assert json.loads(read_graph.model_dump_json()) == json.loads(graph.model_dump_json())
