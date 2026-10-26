"""Studio task pagination and submission retry contracts."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.compile_targets import CompileLifecycle, new_operation
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_domain import TaskPatch
from openviking.service.task_pagination import decode_cursor, encode_cursor, page_scope
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking_cli.exceptions import ConflictError, InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.parametrize(
    "skill,valid",
    [
        ("viking://system/compile/example-skills/llm-wiki/SKILL.md", True),
        ("viking://system/compile/example-skills", False),
        ("viking://system/compile/example-skills/llm-wiki/references", False),
        ("viking://system/memory/memory_templates/builtin", False),
    ],
)
def test_compile_builtin_skill_uri(skill, valid):
    """Compile accepts system Skill roots and rejects other system directories."""
    from openviking.service.compile_service import CompileRequest, CompileService
    from openviking_cli.utils.config.open_viking_config import CompileApiConfig

    service = CompileService(CompileApiConfig(), ExternalTaskService(), SimpleNamespace())
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    request = CompileRequest.model_validate(
        {"from": ["viking://resources/source"], "to": "viking://resources/wiki", "skill": skill}
    )
    if valid:
        assert service._normalize_request_uris(request, ctx).skill == skill.removesuffix(
            "/SKILL.md"
        )
    else:
        with pytest.raises(InvalidArgumentError, match="skill must resolve"):
            service._normalize_request_uris(request, ctx)


@pytest.mark.parametrize(
    "target",
    [
        "viking://system",
        "viking://system/compile",
        "viking://system/compile/example-skills",
        "viking://system/resources/wiki",
    ],
)
def test_compile_builtin_skill_namespace_is_source_only(target):
    from openviking.service.compile_service import CompileService

    CompileService._validate_target("viking://user/alice/skills")
    CompileService._validate_target("viking://agent/skills")

    with pytest.raises(InvalidArgumentError, match="system namespace"):
        CompileService._validate_target(target)


@pytest.mark.asyncio
async def test_compile_api_rejects_system_target_without_creating_task(service, app, client):
    from openviking.server.auth import get_request_context
    from openviking.service.task_tracker import get_task_tracker

    ctx = RequestContext(user=service.user, role=Role.USER, api_key="test-compile-key")
    app.dependency_overrides[get_request_context] = lambda: ctx
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    tracker = get_task_tracker()
    before = {
        task.task_id
        for task in await tracker.list_tasks(task_type="compile", limit=None, **owner)
    }

    response = await client.post(
        "/api/v1/compile",
        json={
            "from": ["viking://resources/source"],
            "to": "viking://system/compile-output",
            "skill": "viking://system/compile/example-skills/llm-wiki",
        },
    )

    assert response.status_code == 400, response.text
    assert "system namespace" in response.text
    after = {
        task.task_id
        for task in await tracker.list_tasks(task_type="compile", limit=None, **owner)
    }
    assert after == before


@pytest.mark.asyncio
async def test_page_reads_past_200_and_isolates_owner():
    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    owner = {"account_id": "acme", "user_id": "alice"}
    for index in range(205):
        await tracker.create(
            "compile",
            task_id=f"cmp_{index:03}",
            meta={"request": {"to": "viking://resources/wiki"}},
            **owner,
        )
    await tracker.create("compile", task_id="foreign", account_id="acme", user_id="bob")
    # Empty cache: pagination must read durable records without repopulating all tasks.
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    filters = {
        "task_type": "compile",
        "status": None,
        "resource_id": None,
        "include_internal": False,
        "q": "wiki",
    }
    seen = []
    before = None
    while True:
        page = await tracker.list_tasks(**owner, limit=30, before=before, **filters)
        if not page:
            break
        seen.extend(task.task_id for task in page)
        before = (page[-1].created_at, page[-1].task_id)
    assert len(seen) == len(set(seen)) == 205
    assert seen[0] == "cmp_204"
    assert "foreign" not in seen


def test_cursor_is_bound_to_identity_and_filters():
    scope = page_scope(account="a", user="u", status="running")
    cursor = encode_cursor({"created_at": 123.4, "task_id": "cmp_a"}, scope)
    assert decode_cursor(cursor, scope) == (123.4, "cmp_a")
    with pytest.raises(InvalidArgumentError):
        decode_cursor(cursor, page_scope(account="a", user="other", status="running"))
    with pytest.raises(InvalidArgumentError):
        decode_cursor("broken", scope)


@pytest.mark.asyncio
async def test_concurrent_submission_retry_and_restart(monkeypatch):
    import ragfs_python

    import openviking.service.external_task_service as module
    from openviking.service.external_task_service import ExternalTaskSnapshot
    from openviking.storage.queuefs.queue_manager import QueueManager

    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    monkeypatch.setattr(module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    transport = ragfs_python.RAGFSBindingClient()
    transport.mount("queuefs", "/queue", {"backend": "memory"})
    manager = QueueManager(transport)
    manager.get_queue(manager.EXTERNAL_TASK, allow_create=True)
    monkeypatch.setattr(module, "get_queue_manager", lambda: manager)
    provider = SimpleNamespace(
        task_type="compile",
        task_id_prefix="cmp_",
        serialization_key=lambda payload: payload["to"],
        poll_interval_seconds=0.001,
        poll_max_attempts=1,
        submit=AsyncMock(return_value="external-task"),
        get=AsyncMock(return_value=ExternalTaskSnapshot(status="completed", result={"done": True})),
    )
    service = ExternalTaskService()
    service.register(provider)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    params = {
        "resource_id": None,
        "payload": {"from": ["viking://resources/a"], "to": "viking://resources/b"},
        "connection": {},
        "ctx": ctx,
        "idempotency_key": "test-submission-123",
    }
    try:
        results = await asyncio.gather(*(service.create("compile", **params) for _ in range(5)))
        assert len({t.task_id for t in results}) == 1
        tracker = TaskTracker(PersistentTaskStore(agfs))
        again = await service.create("compile", **params)
        assert again.task_id == results[0].task_id
        with pytest.raises(ConflictError):
            await service.create("compile", **{**params, "payload": {"to": "different"}})
        other = RequestContext(user=UserIdentifier("acme", "bob"), role=Role.USER)
        waiting = await service.create("compile", **{**params, "ctx": other})
        assert waiting.task_id != again.task_id

        owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
        await tracker.start(again.task_id, stage="polling", **owner)
        await tracker.patch_task(
            again.task_id,
            TaskPatch(auth={"external_task_id": "submitted"}),
            **owner,
        )
        tracker = TaskTracker(PersistentTaskStore(agfs))
        service = ExternalTaskService()
        service.register(provider)
        await manager.prepare_task_tracking(tracker)
        recovered = await manager.get_queued_tasks(tracker, manager.EXTERNAL_TASK)
        assert {task.task_id for task in recovered} == {again.task_id, waiting.task_id}
        await service.restore_tasks(recovered)

        assert not await service.execute(waiting.task_id, other.account_id, other.user.user_id)
        provider.submit.assert_not_awaited()
        assert await service.execute(again.task_id, ctx.account_id, ctx.user.user_id)
        assert await service.execute(waiting.task_id, other.account_id, other.user.user_id)
        assert (await tracker.get(waiting.task_id, account_id="acme", user_id="bob")).result == {
            "done": True
        }
    finally:
        transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("builtin_skill", [False, True])
async def test_http_contract_preserves_retries_and_compile_target_lifecycle(
    service, app, client, monkeypatch, tmp_path, builtin_skill
):
    """One target keeps its history, ordinary edits and live permissions across runs."""
    import base64
    from datetime import datetime, timedelta

    import openviking.service.external_task_service as external
    from openviking.server.auth import get_request_context
    from openviking.server.identity import ResolvedIdentity
    from openviking.service.compile_scheduler import (
        TZ,
        CompileScheduler,
        build_credential_encryptor,
    )
    from openviking.service.compile_service import CompileSessionAccepted, CompileSessionStatus
    from openviking.service.task_tracker import get_task_tracker
    from openviking.storage.acl import AclMode
    from openviking_cli.exceptions import NotFoundError, PermissionDeniedError

    ctx = RequestContext(
        user=service.user, role=Role.USER, group_ids=("engineering",), api_key="test-compile-key"
    )
    admin = RequestContext(user=service.user, role=Role.ADMIN)
    app.dependency_overrides[get_request_context] = lambda: ctx
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    source, target = "viking://resources/source", "viking://resources/wiki"
    skill = (
        "viking://system/compile/example-skills/llm-wiki"
        if builtin_skill
        else f"viking://user/{ctx.user.user_id}/skills/wiki"
    )
    page, picture = f"{target}/page.md", f"{target}/diagram.png"
    png = b"\x89PNG\r\n\x1a\noriginal"
    for uri, content in {
        f"{source}/a.md": "source",
        f"{target}/old.md": "keep renamed",
        f"{target}/discard.md": "remove",
        "viking://resources/inbox/note.md": "keep imported",
        "viking://resources/inbox/copy.md": "keep copied",
        picture: png,
    }.items():
        await service.viking_fs.write_file(uri, content, ctx=admin)
    if not builtin_skill:
        await service.viking_fs.write_file(f"{skill}/SKILL.md", "Build a wiki", ctx=admin)
    await service.runtime_config_manager.patch_account(ctx.account_id, {"acl": {"enabled": True}})
    await service.vikingdb_manager.upsert(
        {
            "id": "compile-source",
            "uri": source,
            "account_id": ctx.account_id,
            "context_type": "resource",
            "level": 0,
            "vector": [0.1] * service.vikingdb_manager.vector_dim,
        },
        ctx=admin,
    )
    await service.fs.set_acl(
        source,
        [{"principal": "group:engineering", "level": "read"}],
        ctx=admin,
        acl_mode=AclMode.RESTRICTED,
    )

    # Drive the real task provider explicitly; generation and derived refresh are external work.
    monkeypatch.setattr(external, "get_queue_manager", lambda: SimpleNamespace(enqueue=AsyncMock()))
    monkeypatch.setattr(service, "reindex", AsyncMock(return_value={"status": "accepted"}))
    monkeypatch.setattr(service.fs, "_enqueue_copy_refresh", AsyncMock(return_value="queued"))
    monkeypatch.setattr(service.fs, "_enqueue_delete_refresh", AsyncMock(return_value="skipped"))
    monkeypatch.setattr("openviking.service.fs_service.vectorize_directory_meta", AsyncMock())
    monkeypatch.setattr(service.viking_fs, "_schedule_restore_reindex_for_paths", AsyncMock())
    service.compile.configure_local_backend("http://localhost:1", "")
    calls = []
    runtime_status = "completed"

    async def generate(payload, **kwargs):
        calls.append(payload["payload"])
        operations = [{"uri": page, "content": "generated"}]
        if runtime_status == "failed":
            operations.append({"uri": f"{target}/partial.md", "content": "unfinished"})
        await service.fs.batch_write(
            root_uri=target,
            operations=operations,
            ctx=ctx,
            defer_processing=True,
            edit_source="compile",
        )
        if runtime_status == "failed":
            await service.fs.mv("viking://resources/inbox/note.md", f"{target}/imported.md", ctx)
            await service.fs.cp(
                "viking://resources/inbox/copy.md", f"{target}/copied.md", recursive=False, ctx=ctx
            )
            await service.fs.mv(f"{target}/old.md", f"{target}/renamed.md", ctx)
            await service.fs.rm(f"{target}/discard.md", ctx, wait=True)
            await service.fs.batch_write(
                root_uri=target,
                operations=[{"uri": page, "content": "ordinary edit"}],
                ctx=ctx,
                defer_processing=True,
            )
        return CompileSessionAccepted(session_id="runtime-task")

    runtime = SimpleNamespace(
        create=generate,
        get=AsyncMock(
            side_effect=lambda *a, **kw: CompileSessionStatus(
                status=runtime_status,
                stage=runtime_status,
                error="generation failed" if runtime_status == "failed" else None,
            )
        ),
    )
    monkeypatch.setattr(service.compile, "_client", lambda: runtime)
    tracker = get_task_tracker()

    async def submit(payload, key):
        response = await client.post(
            "/api/v1/compile",
            json=payload,
            headers={"Idempotency-Key": key, "X-Request-ID": "original-request-id"},
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["result"]["task_id"]
        await service.compile._tasks.execute(task_id, **owner)
        return task_id

    payload = {
        "from": [source],
        "to": target,
        "skill": skill,
        "args": {"api_key": "private-runtime-key"},
    }
    accepted = await client.post(
        "/api/v1/compile",
        json=payload,
        headers={"Idempotency-Key": "compile-initial-key", "X-Request-ID": "original-request-id"},
    )
    assert accepted.status_code == 202, accepted.text
    first = accepted.json()["result"]["task_id"]
    retry = await client.post(
        "/api/v1/compile",
        json=payload,
        headers={"Idempotency-Key": "compile-initial-key", "X-Request-ID": "retry-request-id"},
    )
    assert retry.json()["result"]["task_id"] == first
    assert "private-runtime-key" not in retry.text and "submission_hash" not in retry.text
    auth = await tracker.get_task_auth(first, **owner)
    assert auth["openviking_connection"]["request_id"] == "original-request-id"
    await service.compile._tasks.execute(first, **owner)
    assert (await tracker.get(first, **owner)).status == "completed"
    assert await service.fs.read(page, ctx) == "generated"

    # Interrupted finalization reuses durable checkpoints without publishing another version.
    store = service.compile.lifecycle.store
    initial_state = await store.get_by_to(ctx.account_id, target)
    assert initial_state["auto_update"] is False and initial_state["update_schedule"] is None
    target_id = initial_state["id"]
    with monkeypatch.context() as patch:
        summary = (await store.list_summaries(ctx.account_id))[0]
        summaries = [{**summary, "id": f"target_{i}"} for i in (1, 0)]
        patch.setattr(store, "list_summaries", AsyncMock(return_value=summaries))
        p = (await client.get("/api/v1/compile/configs?limit=1")).json()["result"]
        assert [item["target_id"] for item in p["targets"]] == ["target_0"]
        assert p["next_cursor"] == "target_0"
        p = (await client.get("/api/v1/compile/configs?limit=1&cursor=target_0")).json()["result"]
        assert [item["target_id"] for item in p["targets"]] == ["target_1"]
        assert p["next_cursor"] is None
    operation = await store.get_operation(ctx.account_id, target_id, first)
    checkpoint = dict(operation["meta"])
    assert checkpoint["snapshot_committed"] and checkpoint["target_head"]
    assert (checkpoint["skill_snapshot"]["snapshot_id"] is None) == builtin_skill
    assert checkpoint["reindex_task_id"]
    versions_before_resume = await store.list_version_indexes(ctx.account_id, target_id)
    operation.update(status="committing", finished_at=None)
    await store.put_operation(ctx.account_id, target_id, operation)
    resumed_lifecycle = CompileLifecycle(store, service.viking_fs)
    task = await tracker.get(first, **owner)
    reindex_requests = service.reindex.await_count
    with monkeypatch.context() as patch:
        patch.setattr(resumed_lifecycle, "_commit", AsyncMock())
        patch.setattr(resumed_lifecycle.versions, "record_compile", AsyncMock())
        resumed = await resumed_lifecycle.on_start(task)
        assert resumed.status == "completed"
        await resumed_lifecycle.on_finish(task, resumed)
        resumed_lifecycle._commit.assert_not_awaited()
        resumed_lifecycle.versions.record_compile.assert_not_awaited()
    operation = await store.get_operation(ctx.account_id, target_id, first)
    assert operation["status"] == "completed" and operation["finished_at"] is not None
    assert operation["meta"] == checkpoint
    assert service.reindex.await_count == reindex_requests + 1
    assert service.reindex.call_args.kwargs["task_id"] == checkpoint["reindex_task_id"]
    assert await store.list_version_indexes(ctx.account_id, target_id) == versions_before_resume

    recovery = await client.get("/api/v1/compile/submissions/compile-initial-key")
    assert recovery.json()["result"]["task_id"] == first
    assert isinstance((await client.get("/api/v1/tasks")).json()["result"], list)
    paged = await client.get(
        "/api/v1/tasks", params={"pagination": "cursor", "task_type": "compile"}
    )
    assert paged.json()["result"]["items"][0]["task_id"] == first
    conflict = await client.post(
        "/api/v1/compile",
        json={**payload, "instruction": "changed"},
        headers={"Idempotency-Key": "compile-initial-key"},
    )
    assert conflict.status_code == 409

    queued = await client.post(
        "/api/v1/compile", json=payload, headers={"Idempotency-Key": "compile-cancel-key"}
    )
    cancelled_id = queued.json()["result"]["task_id"]
    stopped = await client.post(f"/api/v1/tasks/{cancelled_id}/cancel")
    assert stopped.status_code == 200 and stopped.json()["result"]["status"] == "cancelled"
    cancelled_retry = await client.post(
        "/api/v1/compile", json=payload, headers={"Idempotency-Key": "compile-cancel-key"}
    )
    assert cancelled_retry.json()["result"]["task_id"] == cancelled_id

    unchanged = await submit(payload, "compile-unchanged-key")
    assert (await tracker.get(unchanged, **owner)).status == "completed" and len(calls) == 1
    payload = {**payload, "instruction": "Change the title"}
    await submit(payload, "compile-instruction-key")
    assert len(calls) == 2 and calls[-1]["args"]["changes"]["execution"]["mode"] == "full"
    assert calls[-1]["args"]["changes"]["full_dirs"] == [source]
    assert calls[-1]["args"]["changes"]["diff_dirs"] == []
    await service.viking_fs.write_file(f"{source}/a.md", "new source", ctx=admin)
    await submit(payload, "compile-source-update-key")
    changes = calls[-1]["args"]["changes"]
    assert changes["execution"]["mode"] == "incremental"
    assert changes["diff_dirs"] == [source] and changes["full_dirs"] == []
    assert "scopes" not in changes

    progress = (await store.get_by_to(ctx.account_id, target))["progress"]
    extra = "viking://resources/extra-source"
    await service.viking_fs.write_file(f"{extra}/a.md", "extra", ctx=admin)
    runtime_status = "failed"
    failed = await submit(
        {**payload, "from": [source, extra], "instruction": "Regenerate"}, "compile-failure-key"
    )
    assert (await tracker.get(failed, **owner)).status == "failed"
    assert (await store.get_by_to(ctx.account_id, target))["progress"] == progress
    assert await service.fs.read(page, ctx) == "ordinary edit"
    assert await service.fs.read(f"{target}/imported.md", ctx) == "keep imported"
    assert await service.fs.read(f"{target}/copied.md", ctx) == "keep copied"
    assert await service.fs.read(f"{target}/renamed.md", ctx) == "keep renamed"
    for uri in (f"{target}/old.md", f"{target}/discard.md", f"{target}/partial.md"):
        assert not await service.viking_fs.exists(uri, ctx=ctx)
    history = (await client.get("/api/v1/compile/operations", params={"to": target})).json()[
        "result"
    ]
    public_fields = {
        "operation_id",
        "operation_type",
        "actor",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "error",
        "params",
        "result",
        "conflicts_num",
        "meta",
    }
    assert all(set(op) == public_fields for op in history["operations"])
    failed_operation = next(
        op for op in history["operations"] if op["meta"].get("task_id") == failed
    )
    assert failed_operation["status"] == "failed"
    assert failed_operation["params"]["from"] == [source, extra]
    assert failed_operation["params"]["to"] == target
    assert failed_operation["params"]["skill"] == skill
    assert set(failed_operation["meta"]) == {
        "task_id",
        "trigger",
        "execution",
        "skill_snapshot",
        "base",
        "head",
        "changes",
        "reindex_task_id",
    }
    assert set(failed_operation["meta"]["changes"]) == {"added", "modified", "deleted"}
    target_id = (await store.get_by_to(ctx.account_id, target))["id"]
    stored_operation = await store.get_operation(ctx.account_id, target_id, failed)
    assert set(stored_operation) == public_fields
    assert set(stored_operation["meta"]) - set(failed_operation["meta"]) == {
        "target_deleted_at",
        "target_base",
        "target_head",
        "snapshot_committed",
        "content_changed",
        "changed_roots",
        "diff_dirs",
        "full_dirs",
        "rollback_head",
    }
    ordinary_operation = next(
        op for op in history["operations"] if op["operation_type"] == "agent_revise"
    )
    assert ordinary_operation["meta"] == {}
    assert ordinary_operation["params"] == {"batch": True, "uris": [page]}
    assert ordinary_operation["result"] == {"created": [], "updated": [page], "unchanged": []}
    assert ordinary_operation["conflicts_num"] == 0
    cancelled_operation = next(
        op for op in history["operations"] if op["operation_id"] == cancelled_id
    )
    assert {"execution", "skill_snapshot"}.isdisjoint(cancelled_operation["meta"])

    # Binary version restore is guarded by the version the caller last observed.
    await service.fs.batch_write(
        root_uri=target,
        operations=[
            {"uri": picture, "content_base64": base64.b64encode(png + b" edited").decode()}
        ],
        ctx=ctx,
        defer_processing=True,
    )
    state = await store.get_by_to(ctx.account_id, target)
    target_id = state["id"]
    indexes = await store.list_version_indexes(ctx.account_id, target_id)
    assert tracker.compile_history_cleanup == store.delete_expired
    assert await store.delete_expired(now=time.time() + 604801, limit=256) > 0
    assert await store.get(ctx.account_id, target_id) == state
    assert await store.list_version_indexes(ctx.account_id, target_id) == indexes
    assert await tracker.get(first, **owner) is not None
    versions = (await client.get("/api/v1/snapshot/versions", params={"uri": picture})).json()[
        "result"
    ]
    restore = {
        "path": picture,
        "restore_version": versions["versions"][-1]["version_id"],
        "current_version": versions["current_version"],
    }
    restored = await client.post("/api/v1/snapshot/restore", json=restore)
    assert restored.status_code == 200, restored.text
    assert restored.json()["result"]["meta"]["restored_from"] == restore["restore_version"]
    await service.resources.wait_processed()
    assert await service.fs.read_file_bytes(picture, ctx) == png
    assert (await client.post("/api/v1/snapshot/restore", json=restore)).status_code == 409

    # Scheduled submissions reuse the target but resolve current group permissions on each trigger.
    scheduler = CompileScheduler(
        service.compile, service.compile.lifecycle.store, service.viking_fs
    )
    scheduler.install_expiry(
        SimpleNamespace(register_handler=Mock(), report=AsyncMock(), unregister=AsyncMock())
    )
    scheduler.credential_encryptor = build_credential_encryptor(service._config.encryption)
    scheduler.credential_encryptor.provider.key_file = tmp_path / "compile-key"
    groups = AsyncMock(return_value=ctx.group_ids)
    scheduler.set_identity_resolver(
        AsyncMock(return_value=ResolvedIdentity(role=Role.USER, **owner)), groups
    )
    service.compile.lifecycle.save_credential = scheduler.save_credential
    service.compile.lifecycle.reschedule = scheduler.reschedule
    settings = {
        "from": [source],
        "to": target,
        "skill": skill,
        "auto_update": True,
        "update_schedule": {"frequency": "daily"},
    }
    configured = await client.post("/api/v1/compile/config/update", json=settings)
    assert configured.status_code == 200, configured.text
    assert configured.json()["result"]["update_schedule"] == {
        "frequency": "daily",
        "hour": 0,
        "minute": 0,
    }
    target_id = configured.json()["result"]["target_id"]
    now = datetime.now(TZ) + timedelta(days=1)
    runtime_status = "completed"
    assert await scheduler.trigger_if_due(ctx.account_id, target_id, now) == "submitted"
    operations = await service.compile.list_operations(target, ctx, limit=20, cursor=None)
    scheduled = next(
        op["meta"]["task_id"]
        for op in operations["operations"]
        if op["meta"].get("trigger") == "scheduled"
    )
    await service.compile._tasks.execute(scheduled, **owner)
    assert (await tracker.get(scheduled, **owner)).status == "completed"
    assert await scheduler.trigger_if_due(ctx.account_id, target_id, now) == "not_due"

    # Disabling clears a supplied rule; null and omitted rules are valid only while disabled.
    for rule in ({"update_schedule": {"frequency": "daily"}}, {"update_schedule": None}, {}):
        disabled = await client.post(
            "/api/v1/compile/config/update",
            json={key: value for key, value in settings.items() if key != "update_schedule"}
            | {"auto_update": False}
            | rule,
        )
        assert disabled.status_code == 200, disabled.text
        config = disabled.json()["result"]
        assert config["auto_update"] is False
        assert config["update_schedule"] is None and config["next_run_at"] is None
    for rule in ({}, {"update_schedule": None}):
        rejected = await client.post(
            "/api/v1/compile/config/update",
            json={key: value for key, value in settings.items() if key != "update_schedule"} | rule,
        )
        assert rejected.status_code == 400, rejected.text
        rejected = await client.post(
            "/api/v1/compile", json={**payload, "args": {"auto_update": True, **rule}}
        )
        assert rejected.status_code == 400, rejected.text
    await submit(
        {**payload, "args": {"auto_update": False, "update_schedule": None}},
        "compile-disable-schedule-key",
    )
    assert (await store.get(ctx.account_id, target_id))["update_schedule"] is None
    configured = await client.post("/api/v1/compile/config/update", json=settings)
    assert configured.status_code == 200, configured.text
    groups.return_value = ()
    with pytest.raises(PermissionDeniedError):
        await scheduler.trigger_if_due(ctx.account_id, target_id, now + timedelta(days=1))

    # Submission recovery remains available after inputs disappear or the backend is disabled.
    with monkeypatch.context() as patch:
        patch.setattr(service.fs, "stat", AsyncMock(side_effect=NotFoundError(source)))
        patch.setattr(service.compile, "_local_endpoint", None)
        recovered = await client.post(
            "/api/v1/compile",
            json={**payload, "instruction": None},
            headers={"Idempotency-Key": "compile-initial-key"},
        )
        assert recovered.status_code == 202, recovered.text
        assert recovered.json()["result"]["task_id"] == first
    ctx = RequestContext(user=UserIdentifier(owner["account_id"], "bob"), role=Role.USER)
    assert (await client.get("/api/v1/tasks", params={"pagination": "cursor"})).json()["result"][
        "items"
    ] == []


@pytest.mark.asyncio
async def test_disabled_schedule_clears_legacy_state_and_lifecycle_changes(service, monkeypatch):
    """Disabled rules stay null across legacy reads, delayed triggers and target reuse."""
    from datetime import datetime

    import openviking.service.compile_scheduler as scheduling

    store = service.compile.lifecycle.store
    account = service.user.account_id
    ctx = RequestContext(user=service.user, role=Role.USER)
    target = "viking://resources/scheduled"
    target_id = await store.register(account, target)
    path = store._record_path(account, target_id)
    schedule = {"frequency": "daily", "hour": 0, "minute": 0}
    legacy = await store.get(account, target_id)
    legacy["update_schedule"] = schedule
    await store.write_json(path, legacy)
    await store._sync_summary(account, legacy)
    assert (await store.get(account, target_id))["update_schedule"] is None
    assert (await store.list_summaries(account))[0]["update_schedule"] is None
    assert (await store.read_json(path))["update_schedule"] == schedule
    async with store.edit(account, target_id):
        pass
    assert (await store.read_json(path))["update_schedule"] is None
    summaries = await store.read_json(store._targets_path(account))
    assert summaries["targets"][0]["update_schedule"] is None

    scheduler = scheduling.CompileScheduler(service.compile, store, service.viking_fs)
    now = datetime(2026, 10, 22, 12, tzinfo=scheduling.TZ)
    with monkeypatch.context() as patch:
        slot = Mock(wraps=scheduling.current_slot)
        patch.setattr(scheduling, "current_slot", slot)
        await scheduler.scan_once(now)
        assert await scheduler.trigger_if_due(account, target_id, now) == "not_due"
        slot.assert_not_called()

    async with store.edit(account, target_id) as record:
        record.update(
            initialized=True,
            auto_update=True,
            update_schedule=schedule,
            auto_update_enabled_at="2026-10-20T00:00:00+08:00",
        )
    assert await scheduler.trigger_if_due(account, target_id, now) == "disabled"
    invalid = await store.get(account, target_id)
    assert invalid["auto_update"] is False and invalid["update_schedule"] is None
    assert (await store.list_summaries(account))[0]["update_schedule"] is None

    async with store.edit(account, target_id) as record:
        record.update(auto_update=True, update_schedule=schedule)
    await service.compile.lifecycle.versions.after_remove(target, ctx)
    deleted = await store.get(account, target_id)
    assert deleted["status"] == "deleted" and deleted["update_schedule"] is None
    assert await store.register(account, target) == target_id
    reused = await store.get(account, target_id)
    assert reused["status"] == "active" and reused["auto_update"] is False
    assert reused["update_schedule"] is None


@pytest.mark.asyncio
async def test_cursor_prunes_cold_expired_records_without_loading_cache():
    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    owner = {"account_id": "acme", "user_id": "alice"}
    for task_id, status, days in [
        ("expired-complete", TaskStatus.COMPLETED, 2),
        ("expired-cancelled", TaskStatus.CANCELLED, 2),
        ("expired-failed", TaskStatus.FAILED, 8),
        ("recent-failed", TaskStatus.FAILED, 2),
        ("active", TaskStatus.RUNNING, 8),
    ]:
        task = await tracker.create("compile", task_id=task_id, **owner)
        path = f"/local/acme/_system/tasks/alice/{task.task_id}.json"
        payload = json.loads(agfs.files[path])
        payload["status"] = status.value
        payload["updated_at"] = time.time() - days * 86400
        agfs.files[path] = json.dumps(payload).encode()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    tracker.compile_history_cleanup = AsyncMock(side_effect=OSError("history cleanup"))
    await tracker.delete_expired()
    tracker.compile_history_cleanup.assert_awaited_once()
    page = await asyncio.wait_for(
        tracker.list_tasks(
            **owner,
            limit=30,
            task_type="compile",
            status=None,
            resource_id=None,
            include_internal=False,
            q=None,
        ),
        timeout=3,
    )
    assert {task.task_id for task in page} == {"recent-failed", "active"}
    assert {task.task_id for task in await store.list("acme", user_id="alice")} == {
        "recent-failed",
        "active",
    }


@pytest.mark.asyncio
async def test_cursor_cleanup_rechecks_task_updated_after_scan():
    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    owner = {"account_id": "acme", "user_id": "alice"}
    task = await tracker.create("compile", task_id="updated-after-scan", **owner)
    path = f"/local/acme/_system/tasks/alice/{task.task_id}.json"
    payload = json.loads(agfs.files[path])
    payload["status"] = TaskStatus.COMPLETED.value
    payload["updated_at"] = time.time() - 2 * 86400
    agfs.files[path] = json.dumps(payload).encode()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    record_lock = ("acme", "alice", task.task_id)
    async with store._record_locks.acquire(record_lock):
        cleanup = asyncio.create_task(tracker.delete_expired())
        await asyncio.sleep(0)
        payload["updated_at"] = time.time()
        agfs.files[path] = json.dumps(payload).encode()
    await asyncio.wait_for(cleanup, timeout=3)
    page = await tracker.list_tasks(
        **owner,
        limit=30,
        task_type="compile",
        status=None,
        resource_id=None,
        include_internal=False,
        q=None,
    )
    assert [record.task_id for record in page] == [task.task_id]
    assert await store.get_task(task.task_id, **owner) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_dir", ["operation_outputs", "operations"])
async def test_compile_history_retention_and_retry(service, monkeypatch, failed_dir):
    store = service.compile.lifecycle.store
    account = service.user.account_id
    target = await store.register(account, "viking://resources/history")
    now = time.time()
    for operation_id, status, finished_at in (
        ("expired", "completed", now - 604801),
        ("boundary", "failed", now - 604800),
        ("missing", "cancelled", None),
        ("running", "running", now - 604801),
    ):
        operation = new_operation(operation_id, "restore", {}, "user", status=status)
        operation["finished_at"] = finished_at
        await store.merge_outputs(account, target, operation_id, [])
        await store.put_operation(account, target, operation)
    async with store.edit(account, target) as record:
        record["active_task_id"] = "running-task"
    assert await store.delete_expired(now=now, limit=256) == 0
    async with store.edit(account, target) as record:
        record["active_task_id"] = None
    remove = store.remove

    async def fail_remove(path, **kwargs):
        if path.endswith(f"/{failed_dir}/expired.json"):
            raise OSError("history deletion failed")
        await remove(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "remove", fail_remove)
        assert await store.delete_expired(now=now, limit=256) == 0
    assert await store.get_operation(account, target, "expired") is not None
    for _ in range(4):
        await store.delete_expired(now=now, limit=2)
    assert await store.get_operation(account, target, "expired") is None
    assert await store.get_outputs(account, target, "expired") is None
    for operation_id in ("boundary", "missing", "running"):
        assert await store.get_operation(account, target, operation_id) is not None
        assert await store.get_outputs(account, target, operation_id) is not None
