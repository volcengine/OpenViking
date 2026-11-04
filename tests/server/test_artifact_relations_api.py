"""Compile graph failure and target mutation through the real service/API."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.service.compile_service import CompileSessionAccepted, CompileSessionStatus
from openviking.service.task_tracker import get_task_tracker
from openviking_cli.exceptions import ConflictError


@pytest.mark.asyncio
async def test_compile_graph_failure_prevents_success_and_blocks_ordinary_edit(
    service, app, client, monkeypatch
):
    ctx = RequestContext(user=service.user, role=Role.USER, api_key="test-key")
    app.dependency_overrides[get_request_context] = lambda: ctx
    source, target = "viking://resources/input", "viking://resources/graph-failure"
    await service.viking_fs.write_file(source + "/a.md", "input", ctx=ctx)
    await service.viking_fs.mkdir(target, ctx=ctx)
    monkeypatch.setattr(service.fs, "finalize_compile_directories", AsyncMock())
    service.compile.configure_local_backend("http://localhost:1", "")
    observed_wait = []

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
    assert len(observed_wait) == 3 and set(observed_wait) <= {"pending", "running"}
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
