"""Exercise durable worker, write registration and ACL with real Git and storage."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs import AsyncAGFSClient, get_binding_client
from openviking.server.identity import RequestContext, Role
from openviking.service.artifact_relations_service import ArtifactRelationsService
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
