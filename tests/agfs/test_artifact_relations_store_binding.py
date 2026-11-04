"""Exercise real localfs replacement and coordination between native clients."""

import asyncio
import json

import pytest

from openviking.pyagfs import AsyncAGFSClient, get_binding_client
from openviking.service.artifact_relations_models import (
    ArtifactRelations,
    RelationSource,
    RelationSources,
)
from openviking.service.artifact_relations_store import ArtifactRelationsStore
from openviking.service.compile_targets import CompileStateStore


@pytest.fixture(params=[False, True], ids=["plaintext", "encrypted"])
def native_clients(tmp_path, request):
    binding, _ = get_binding_client()
    root = tmp_path / "local"
    root.mkdir()
    config = {"service_name": "artifact-store-test"}
    if request.param:
        config["encryption"] = {"root_key": bytes(range(32)), "provider_type": 0}
    clients = [
        binding(config=config),
        binding(config=config),
    ]
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
    account, to = "acme", "viking://resources/wiki"
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
    account, to = "acme", "viking://resources/wiki"
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
