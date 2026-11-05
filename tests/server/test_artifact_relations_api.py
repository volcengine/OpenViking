"""Compile graph failure and target mutation through the real service/API."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.service.artifact_relations_models import (
    ArtifactRelations,
    ArtifactRelationsState,
    RelationEdge,
)
from openviking.service.compile_service import CompileSessionAccepted, CompileSessionStatus
from openviking.service.external_task_service import ExternalTaskSnapshot
from openviking.service.task_tracker import get_task_tracker
from openviking_cli.exceptions import ConflictError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_compile_graph_failure_prevents_success_and_blocks_ordinary_edit(
    service, app, client, monkeypatch
):
    ctx = RequestContext(user=service.user, role=Role.USER, api_key="test-key")
    app.dependency_overrides[get_request_context] = lambda: ctx
    source, target = "viking://resources/input", "viking://resources/graph_failure"
    await service.viking_fs.write_file(source + "/a.md", "input", ctx=ctx)
    await service.viking_fs.mkdir(target, ctx=ctx)
    monkeypatch.setattr(service.fs, "finalize_compile_directories", AsyncMock())
    service.compile.configure_local_backend("http://localhost:1", "")
    observed_wait = []
    spaced_write_status = []
    changed = AsyncMock(wraps=service.artifact_relations.changed)
    monkeypatch.setattr(service.artifact_relations, "changed", changed)

    async def build(*args):
        task = (
            await get_task_tracker().list_tasks(
                task_type="compile",
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        )[0]
        observed_wait.append(task.status)
        with pytest.raises(ConflictError):
            await service.fs.batch_write(
                root_uri=target,
                operations=[{"uri": target + "/page.md", "content": "ordinary edit"}],
                ctx=ctx,
                defer_processing=True,
            )
        raise ValueError("injected graph failure")

    monkeypatch.setattr(service.artifact_relations.builder, "build", build)

    async def generate(payload, **kwargs):
        trusted = replace(ctx, compile_write_token=kwargs["connection"]["compile_write_token"])
        await service.fs.batch_write(
            root_uri=target,
            operations=[{"uri": target + "/page.md", "content": "generated"}],
            ctx=trusted,
            defer_processing=True,
            edit_source="compile",
        )
        response = await client.post(
            "/api/v1/content/write",
            json={
                "uri": target.replace("_", " ") + "/page.md",
                "content": "ordinary edit",
            },
        )
        spaced_write_status.append(response.status_code)
        return CompileSessionAccepted(session_id="runtime-task")

    runtime = SimpleNamespace(
        create=generate,
        get=AsyncMock(return_value=CompileSessionStatus(status="completed", stage="completed")),
    )
    monkeypatch.setattr(service.compile, "_client", lambda: runtime)
    response = await client.post(
        "/api/v1/compile",
        json={
            "from": [source],
            "to": target,
            "skill": "viking://system/compile/example-skills/llm-wiki",
        },
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["result"]["task_id"]
    await service.compile._tasks.execute(
        task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
    )
    task = await get_task_tracker().wait(
        task_id, account_id=ctx.account_id, user_id=ctx.user.user_id, timeout=20
    )
    assert task.status == "failed" and "ARTIFACT_RELATIONS_FAILED" in task.error
    assert spaced_write_status == [409]
    assert len(observed_wait) == 3 and set(observed_wait) <= {"pending", "running"}
    changed.assert_awaited_once()
    record = await service.compile.lifecycle.store.get(ctx.account_id, target)
    assert record["versions_head"] is None and record["active_operation_id"] is None
    response = await client.get("/api/v1/compile/artifact-relations", params={"to": target})
    assert response.status_code != 200 and "injected graph failure" in response.text


@pytest.mark.asyncio
async def test_managed_directory_move_and_empty_directory_refresh(service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.ADMIN)
    old, new = "viking://resources/old-wiki", "viking://resources/new-wiki"
    await service.viking_fs.write_file(old + "/page.md", "[self](page.md)", ctx=ctx)
    store = service.compile.lifecycle.store
    async with store.edit(ctx.account_id, old, create=True):
        pass
    monkeypatch.setattr(service.fs, "_enqueue_transfer_rebuild", AsyncMock())
    monkeypatch.setattr(service.fs, "_refresh_move_parents", AsyncMock())
    monkeypatch.setattr("openviking.service.fs_service.vectorize_directory_meta", AsyncMock())
    await service.fs.mkdir(old + "/empty", ctx=ctx)
    await service.artifact_relations.process(ctx.account_id, old)
    assert {"uri": old + "/empty/"} in (await service.artifact_relations.query(old, ctx))["nodes"]
    await service.fs.mv(old, new, ctx)
    assert (await store.get(ctx.account_id, new))["wiki_head"]
    await service.artifact_relations.process(ctx.account_id, new)
    graph = await service.artifact_relations.query(new, ctx)
    assert {"uri": new + "/page.md"} in graph["nodes"]
    assert all(
        edge["from"].startswith(new) and edge["to"].startswith(new) for edge in graph["edges"]
    )


@pytest.mark.asyncio
async def test_skill_package_snapshot_is_registered_before_semantic_handoff(service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.ADMIN)
    target = "viking://agent/skills/graph-package"
    store = service.compile.lifecycle.store
    async with store.edit(ctx.account_id, target, create=True):
        pass
    processor = service.resources._skill_processor
    original = processor._enqueue_skill_package
    observed = []

    async def enqueue(*args, **kwargs):
        record = await store.get(ctx.account_id, target)
        assert record["wiki_head"]
        observed.append(record["wiki_head"])
        return await original(*args, **kwargs)

    monkeypatch.setattr(processor, "_enqueue_skill_package", enqueue)
    await service.resources.add_skill(
        data={"name": "graph-package", "description": "test", "content": "[self](SKILL.md)"},
        ctx=ctx,
        target_uri="viking://agent/skills",
        wait=True,
        apply_privacy=False,
    )
    await service.artifact_relations.process(ctx.account_id, target)
    graph = await service.artifact_relations.query(target, ctx)
    assert observed and {"uri": target + "/SKILL.md"} in graph["nodes"]
    assert any(edge["type"] == "references" for edge in graph["edges"])


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_started", [False, True], ids=["unavailable", "stuck"])
async def test_compile_graph_timeout_releases_target(
    service, app, client, monkeypatch, worker_started
):
    ctx = RequestContext(user=service.user, role=Role.USER, api_key="test-key")
    app.dependency_overrides[get_request_context] = lambda: ctx
    source, target = "viking://resources/input", "viking://resources/graph-timeout"
    await service.viking_fs.write_file(source + "/a.md", "input", ctx=ctx)
    await service.viking_fs.mkdir(target, ctx=ctx)
    monkeypatch.setattr(service.fs, "finalize_compile_directories", AsyncMock())
    service.compile.configure_local_backend("http://localhost:1", "")
    clock = SimpleNamespace(time=lambda: 100.0)
    monkeypatch.setattr("openviking.service.artifact_relations_store.time", clock)
    waiting, started, resume = asyncio.Event(), asyncio.Event(), asyncio.Event()
    checked_five_minutes = asyncio.Event()
    relations = service.artifact_relations
    original_ready, original_build = relations.ready, relations.builder.build

    async def ready(*args, **kwargs):
        waiting.set()
        result = await original_ready(*args, **kwargs)
        if clock.time() == 400.0:
            checked_five_minutes.set()
        return result

    async def build(*args):
        started.set()
        await resume.wait()
        return await original_build(*args)

    monkeypatch.setattr(relations, "ready", ready)
    if worker_started:
        monkeypatch.setattr(relations.builder, "build", build)
    else:
        monkeypatch.setattr(relations, "process", AsyncMock())

    async def generate(payload, **kwargs):
        trusted = replace(ctx, compile_write_token=kwargs["connection"]["compile_write_token"])
        await service.fs.batch_write(
            root_uri=target,
            operations=[{"uri": target + "/page.md", "content": "generated"}],
            ctx=trusted,
            defer_processing=True,
            edit_source="compile",
        )
        return CompileSessionAccepted(session_id="runtime-task")

    runtime = SimpleNamespace(
        create=generate,
        get=AsyncMock(return_value=CompileSessionStatus(status="completed", stage="completed")),
    )
    monkeypatch.setattr(service.compile, "_client", lambda: runtime)
    response = await client.post(
        "/api/v1/compile",
        json={
            "from": [source],
            "to": target,
            "skill": "viking://system/compile/example-skills/llm-wiki",
        },
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["result"]["task_id"]
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    execution = asyncio.create_task(service.compile._tasks.execute(task_id, **owner))
    try:
        await asyncio.wait_for(waiting.wait(), 10)
        if worker_started:
            await asyncio.wait_for(started.wait(), 10)
        clock.time = lambda: 400.0
        await asyncio.wait_for(checked_five_minutes.wait(), 10)
        assert not (await get_task_tracker().get(task_id, **owner)).is_terminal()
        clock.time = lambda: 700.0
        task = await get_task_tracker().wait(task_id, **owner, timeout=20)
        assert task.status == "failed" and "ARTIFACT_RELATIONS_TIMEOUT" in task.error
        state, graph = await relations.store.get_publication(ctx.account_id, target)
        assert state.error_code == "ARTIFACT_RELATIONS_TIMEOUT" and graph is None
        record = await service.compile.lifecycle.store.get(ctx.account_id, target)
        assert record["active_operation_id"] is None and record["active_task_id"] is None
        assert record["versions_head"] is None
        # Replay Runtime completion after the failure operation was persisted.
        replay = await service.compile.lifecycle.on_finish(
            task, ExternalTaskSnapshot(status="completed")
        )
        assert replay.status == "failed" and replay.error_code == "ARTIFACT_RELATIONS_TIMEOUT"
        await relations.authorize(ctx.account_id, [target], [target + "/page.md"], ctx)
        response = await client.get("/api/v1/compile/artifact-relations", params={"to": target})
        assert response.status_code != 200 and "timed out" in response.text
    finally:
        resume.set()
        await asyncio.wait_for(execution, 5)


@pytest.mark.asyncio
async def test_graph_batch_acl_deduplicates_queries_and_observes_permission_changes(
    service, monkeypatch
):
    from openviking.storage.acl import AclMode

    ctx = RequestContext(user=service.user, role=Role.USER)
    admin = replace(ctx, role=Role.ADMIN)
    root = "viking://resources/batch-graph"
    private = root + "/private"
    hidden = private + "/secret.md"
    await service.viking_fs.mkdir(private, ctx=admin)
    for index, uri in enumerate([root, private]):
        await service.vikingdb_manager.upsert(
            {
                "id": f"batch-acl-{index}",
                "uri": uri,
                "account_id": ctx.account_id,
                "context_type": "resource",
                "level": 0,
                "vector": [0.1] * service.vikingdb_manager.vector_dim,
            },
            ctx=admin,
        )
    await service.runtime_config_manager.patch_account(ctx.account_id, {"acl": {"enabled": True}})
    grant = [{"principal": f"user:{ctx.user.user_id}", "level": "read"}]
    await service.fs.set_acl(root, grant, ctx=admin, acl_mode=AclMode.RESTRICTED)
    await service.fs.set_acl(private, [], ctx=admin, acl_mode=AclMode.RESTRICTED)
    async with service.compile.lifecycle.store.edit(ctx.account_id, root, create=True):
        pass
    pages = [root + f"/page-{index}.md" for index in range(250)]
    graph = ArtifactRelations(
        build_id="batch",
        target_snapshot_id="snapshot",
        generated_at=1,
        nodes=[root + "/", *pages, private + "/", hidden],
        edges=[
            *[RelationEdge(from_uri=root + "/", to=page, type="contains") for page in pages],
            RelationEdge(from_uri=root + "/", to=private + "/", type="contains"),
            RelationEdge(from_uri=private + "/", to=hidden, type="contains"),
            RelationEdge(from_uri=pages[0], to=hidden, type="references"),
            RelationEdge(from_uri=hidden, to=pages[1], type="references"),
        ],
    )
    state = ArtifactRelationsState(
        status="ready",
        build_id="batch",
        target_snapshot_id="snapshot",
    )
    monkeypatch.setattr(
        service.artifact_relations.store, "get_publication", AsyncMock(return_value=(state, graph))
    )
    manager = service.viking_fs.acl_manager
    scroll = AsyncMock(wraps=manager._context_store.scroll)
    monkeypatch.setattr(manager._context_store, "scroll", scroll)
    expected = []
    for node in graph.nodes:
        try:
            await service.viking_fs._ensure_access(node.rstrip("/"), ctx)
        except PermissionDeniedError:
            continue
        expected.append({"uri": node})
    sequential_reads = scroll.await_count
    scroll.reset_mock()
    result = await service.artifact_relations.query(root, ctx)
    assert result["nodes"] == expected
    assert len(result["nodes"]) == 251
    assert len(result["edges"]) == 250
    # The real resolver groups exact and shared ancestor lookups in batches
    # of 100. Per-node resolution would issue hundreds of scroll requests.
    assert 0 < scroll.await_count < 20
    assert scroll.await_count < sequential_reads / 10
    print(
        f"ACL queries for {len(graph.nodes)} nodes: per-node={sequential_reads}, "
        f"batch={scroll.await_count}"
    )
    await service.fs.set_acl(private, grant, ctx=admin, acl_mode=AclMode.RESTRICTED)
    result = await service.artifact_relations.query(root, ctx)
    assert {"uri": hidden} in result["nodes"]
    await service.fs.set_acl(private, [], ctx=admin, acl_mode=AclMode.RESTRICTED)
    result = await service.artifact_relations.query(root, ctx)
    assert {"uri": hidden} not in result["nodes"]
    assert not any(edge["from"] == hidden or edge["to"] == hidden for edge in result["edges"])


@pytest.mark.asyncio
async def test_graph_batch_visibility_preserves_global_owner_scope(service, monkeypatch):
    account = service.user.account_id
    owner = RequestContext(user=UserIdentifier(account, "graph-owner"), role=Role.USER)
    reader = RequestContext(user=UserIdentifier(account, "global-reader"), role=Role.GLOBAL)
    root = "viking://user/graph-owner/resources/wiki"
    public, secret = root + "/public.md", root + "/secret.md"
    await service.viking_fs.write_file(
        "viking://user/graph-owner/GLOBAL_POLICY.md",
        "---\ntype: global-access-policy\nschema_version: 1\ndefault: deny\nrules:\n"
        "  - effect: allow\n    path: resources/wiki\n    recursive: true\n"
        "  - effect: deny\n    path: resources/wiki/secret.md\n    recursive: false\n---\n",
        ctx=owner,
    )
    async with service.compile.lifecycle.store.edit(account, root, create=True):
        pass
    graph = ArtifactRelations(
        build_id="global",
        target_snapshot_id="snapshot",
        generated_at=1,
        nodes=[root + "/", public, secret],
        edges=[RelationEdge(from_uri=public, to=secret, type="references")],
    )
    state = ArtifactRelationsState(
        status="ready",
        build_id="global",
        target_snapshot_id="snapshot",
    )
    monkeypatch.setattr(
        service.artifact_relations.store, "get_publication", AsyncMock(return_value=(state, graph))
    )
    access = AsyncMock(wraps=service.viking_fs._can_access_many)
    monkeypatch.setattr(service.viking_fs, "_can_access_many", access)
    result = await service.artifact_relations.query(root, reader)
    assert result["nodes"] == [{"uri": root + "/"}, {"uri": public}]
    assert not result["edges"]
    batch_ctx = access.await_args_list[-1].args[1]
    assert batch_ctx.user == reader.user and batch_ctx.role == Role.GLOBAL
    assert batch_ctx.data_access.owner_user_id == "graph-owner"


@pytest.mark.asyncio
async def test_graph_batch_visibility_keeps_actor_peer_isolation(service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.USER, actor_peer_id="alpha")
    root = f"viking://user/{service.user.user_id}"
    own = root + "/peers/alpha/memories/profile.md"
    other = root + "/peers/beta/memories/profile.md"
    async with service.compile.lifecycle.store.edit(ctx.account_id, root, create=True):
        pass
    graph = ArtifactRelations(
        build_id="peers",
        target_snapshot_id="snapshot",
        generated_at=1,
        nodes=[root + "/", own, other],
        edges=[RelationEdge(from_uri=own, to=other, type="references")],
    )
    state = ArtifactRelationsState(
        status="ready",
        build_id="peers",
        target_snapshot_id="snapshot",
    )
    monkeypatch.setattr(
        service.artifact_relations.store, "get_publication", AsyncMock(return_value=(state, graph))
    )
    result = await service.artifact_relations.query(root, ctx)
    assert {"uri": own} in result["nodes"] and {"uri": other} not in result["nodes"]
    assert not result["edges"]
