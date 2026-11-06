"""Persistent configuration, full execution, and shared scheduling contracts."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.service.compile_scheduler import CompileScheduler, is_due, next_slot
from openviking.service.compile_service import (
    CompileConfigUpdate,
    CompileCreateRequest,
    CompileRequest,
    CompileSessionAccepted,
    CompileSessionStatus,
)
from openviking.service.compile_targets import STATE_FIELDS
from openviking.service.task_tracker import get_task_tracker
from openviking_cli.exceptions import InvalidArgumentError, PermissionDeniedError, UnavailableError
from openviking_cli.session.user_id import UserIdentifier

SOURCE = "viking://resources/source"
TARGET = "viking://resources/wiki"
SKILL = "viking://system/compile/example-skills/llm-wiki"
INPUTS = {"from": [SOURCE], "to": TARGET, "skill": SKILL}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"compile_id": "../state"},
        {"compile_id": "cpt_" + "a" * 32, "to": TARGET},
        {"compile_id": "cpt_" + "a" * 32, "args": None},
        {**INPUTS, "operation_id": "cmp_example"},
        {**INPUTS, "args": {"mode": "incremental"}},
        {**INPUTS, "args": {"changes": {}}},
        {**INPUTS, "args": {"last_compile_time": 1}},
        {**INPUTS, "args": {"auto_update": False}},
        {**INPUTS, "args": {"update_schedule": None}},
        {**INPUTS, "reason": "old alias"},
        {**INPUTS, "owner_user_id": "someone-else"},
        {**INPUTS, "user_id": "someone-else"},
    ],
)
def test_execution_request_rejects_ambiguous_or_legacy_controls(payload):
    with pytest.raises(ValidationError):
        CompileRequest.model_validate(payload)


async def prepare(service, app, monkeypatch):
    """Drive real task storage/snapshots while replacing generation and derived indexing."""
    ctx = RequestContext(user=service.user, role=Role.USER, api_key="compile-key")
    app.dependency_overrides[get_request_context] = lambda: ctx
    admin = RequestContext(user=service.user, role=Role.ADMIN)
    await service.viking_fs.write_file(f"{SOURCE}/source.md", "source", ctx=admin)
    await service.viking_fs.write_file(f"{TARGET}/page.md", "original", ctx=admin)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager", lambda: SimpleNamespace(enqueue=AsyncMock())
    )
    monkeypatch.setattr(service.fs, "ensure_compile_target", AsyncMock())
    monkeypatch.setattr(service.fs, "finalize_compile_directories", AsyncMock())
    monkeypatch.setattr(
        service.viking_fs, "_schedule_restore_reindex_for_paths", AsyncMock(return_value=None)
    )
    service.compile.configure_local_backend("http://localhost:1", "")
    runtime = SimpleNamespace(status="completed", calls=[])

    async def generate(body, **kwargs):
        runtime.calls.append(body["payload"])
        await service.viking_fs.write_file(
            f"{TARGET}/page.md", f"generated {len(runtime.calls)}", ctx=admin
        )
        if runtime.status == "failed":
            await service.viking_fs.write_file(f"{TARGET}/partial.md", "unfinished", ctx=admin)
        return CompileSessionAccepted(session_id="runtime-task")

    runtime.create = generate
    runtime.archive = AsyncMock()
    runtime.get = AsyncMock(
        side_effect=lambda *a, **kw: CompileSessionStatus(
            status=runtime.status,
            stage=runtime.status,
            error="generation failed" if runtime.status == "failed" else None,
        )
    )
    monkeypatch.setattr(service.compile, "_client", lambda: runtime)
    return ctx, runtime


@pytest.mark.parametrize("endpoint", ["/api/v1/compile/create", "/api/v1/compile"])
async def test_compile_admission_rejects_invalid_targets_before_publication(
    service, app, client, monkeypatch, endpoint
):
    ctx, runtime = await prepare(service, app, monkeypatch)
    for target in (SOURCE, f"{SOURCE}/child", "viking://~/memories/experiences/project/"):
        response = await client.post(endpoint, json={**INPUTS, "to": target})
        assert response.status_code == 400, response.text
    service.fs.ensure_compile_target.assert_not_awaited()
    service.fs.ensure_compile_target.side_effect = UnavailableError("tags", "unavailable")
    response = await client.post(endpoint, json=INPUTS)
    assert response.status_code == 503, response.text
    assert not runtime.calls
    assert not [r async for r in service.compile.lifecycle.store.iter_records(ctx.account_id)]
    assert not await get_task_tracker().list_tasks(
        task_type="compile", account_id=ctx.account_id, user_id=ctx.user.user_id, limit=None
    )


async def test_configuration_runs_full_isolation_and_failure_recovery(
    service, app, client, monkeypatch
):
    ctx, runtime = await prepare(service, app, monkeypatch)
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    store = service.compile.lifecycle.store
    tracker = get_task_tracker()

    created = await client.post("/api/v1/compile/create", json=INPUTS)
    assert created.status_code == 200, created.text
    compile_id = created.json()["result"]["compile_id"]
    state = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
    assert set(state) == STATE_FIELDS
    assert state["task_first_create_time"] is state["last_run_time"] is None
    assert await store.list_runs(ctx.account_id, ctx.user.user_id, compile_id) == []
    assert runtime.calls == []
    service.fs.ensure_compile_target.assert_awaited_once_with(TARGET, ctx)
    assert state["conflict_num"] is None
    actor = RequestContext(user=UserIdentifier(ctx.account_id, "executor"), role=Role.USER)
    app.dependency_overrides[get_request_context] = lambda: actor
    owner["user_id"] = actor.user.user_id

    async def execute(payload, key):
        response = await client.post(
            "/api/v1/compile", json=payload, headers={"Idempotency-Key": key}
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["result"]["task_id"]
        await service.compile._tasks.execute(task_id, **owner)
        return await tracker.get(task_id, **owner)

    first = await execute({"compile_id": compile_id}, "compile-first-execution")
    assert first.status == "completed", first.error
    state = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
    assert state["task_first_create_time"] == state["last_run_time"] == first.created_at
    run = await store.get_run(ctx.account_id, ctx.user.user_id, compile_id, first.task_id)
    assert run["task_id"] == first.task_id and "operation_id" not in run
    assert set(run["meta"]) == {
        "trigger",
        "mode",
        "base_snapshot",
        "start_snapshot",
        "finish_snapshot",
        "skill_snapshot_id",
    }
    assert run["meta"]["mode"] == "full"
    assert run["actor"] == "executor"
    assert state["execution_context"]["owner_user_id"] == ctx.user.user_id
    assert state["conflict_num"] == 0
    assert await store.get(ctx.account_id, "executor", compile_id) is None
    assert state["progress"]["source_commit"] == run["meta"]["start_snapshot"]
    assert runtime.calls[-1]["args"]["changes"]["execution"]["mode"] == "full"
    assert "compile" not in runtime.calls[-1]

    repeated = await execute({"compile_id": compile_id}, "compile-first-execution")
    assert repeated.task_id == first.task_id
    assert (await store.get(ctx.account_id, ctx.user.user_id, compile_id))[
        "last_run_time"
    ] == first.created_at
    assert len(runtime.calls) == 1

    noop = await execute({"compile_id": compile_id}, "compile-noop-execution")
    assert noop.status == "completed", noop.error
    assert len(runtime.calls) == 1
    state = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
    assert state["task_first_create_time"] == first.created_at
    assert state["last_run_time"] == noop.created_at
    assert state["last_triggered_slot"] is None

    await service.viking_fs.write_file(f"{SOURCE}/source.md", "updated", ctx=ctx)
    incremental = await execute({"compile_id": compile_id}, "compile-updated-source")
    assert incremental.status == "completed", incremental.error
    assert runtime.calls[-1]["args"]["changes"]["execution"]["mode"] == "incremental"
    state_before_full = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
    runs_before_full = await store.list_runs(ctx.account_id, ctx.user.user_id, compile_id)
    commit = service.viking_fs.commit
    paths = []

    async def capture_commit(*args, **kwargs):
        paths.append(kwargs.get("paths"))
        return await commit(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(service.viking_fs, "commit", capture_commit)
        patch.setattr(
            store, "get", AsyncMock(side_effect=AssertionError("full read compile state"))
        )
        full = await execute(INPUTS, "compile-full-execution")
    assert full.status == "completed", full.error
    assert all(SOURCE not in (entry or []) for entry in paths)
    assert runtime.calls[-1]["args"]["changes"]["head"] is None
    assert runtime.calls[-1]["args"]["changes"]["execution"]["mode"] == "full"
    assert await store.get(ctx.account_id, ctx.user.user_id, compile_id) == state_before_full
    assert await store.list_runs(ctx.account_id, ctx.user.user_id, compile_id) == runs_before_full

    preserved = await service.fs.read(f"{TARGET}/page.md", ctx)
    await service.viking_fs.write_file(f"{SOURCE}/source.md", "another update", ctx=ctx)
    runtime.status = "failed"
    failed = await execute({"compile_id": compile_id}, "compile-failed-execution")
    assert failed.status == "failed"
    after_failure = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
    assert after_failure["progress"] == state_before_full["progress"]
    assert after_failure["last_run_time"] == failed.created_at
    assert await service.fs.read(f"{TARGET}/page.md", ctx) == preserved
    assert not await service.viking_fs.exists(f"{TARGET}/partial.md", ctx=ctx)
    failed_run = await store.get_run(ctx.account_id, ctx.user.user_id, compile_id, failed.task_id)
    assert failed_run["meta"]["finish_snapshot"] is not None

    second_config = (await client.post("/api/v1/compile/create", json=INPUTS)).json()["result"][
        "compile_id"
    ]
    configs = (await client.get("/api/v1/compile/configs")).json()["result"]["configs"]
    assert {item["compile_id"] for item in configs} == {compile_id, second_config}
    history = await client.get(
        "/api/v1/compile/runs", params={"compile_id": compile_id, "limit": 2}
    )
    assert history.status_code == 200, history.text
    assert history.json()["result"]["runs"][0]["task_id"] == failed.task_id
    assert history.json()["result"]["next_cursor"] is not None


async def test_scheduled_execution_shares_entry_times_and_failed_slots(service, app, monkeypatch):
    ctx, _ = await prepare(service, app, monkeypatch)
    store = service.compile.lifecycle.store
    scheduler = CompileScheduler(service.compile, store)
    scheduler._expiry = SimpleNamespace(report=AsyncMock(), unregister=AsyncMock())
    scheduler.save_credential = AsyncMock(
        return_value=(
            {"account_id": ctx.account_id, "user_id": ctx.user.user_id},
            {"ciphertext": "test"},
        )
    )
    scheduler._executor = AsyncMock(
        return_value=(ctx, {"account_id": ctx.account_id, "user_id": ctx.user.user_id})
    )
    service.compile.configure_scheduler(scheduler)
    result = await service.compile.create_compile_task(
        CompileCreateRequest.model_validate(
            {
                **INPUTS,
                "args": {"update_schedule": {"frequency": "daily", "hour": 10, "minute": 0}},
            }
        ),
        ctx=ctx,
    )
    compile_id = result["compile_id"]
    before = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
    due = datetime.fromisoformat(before["next_triggered_slot"])
    assert not is_due(before, due - timedelta(seconds=1))
    assert is_due(before, due)
    assert (
        await scheduler.trigger_if_due(ctx.account_id, ctx.user.user_id, compile_id, due)
        == "submitted"
    )
    after = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
    assert after["task_first_create_time"] == after["last_run_time"] is not None
    assert after["last_triggered_slot"] == due.isoformat()
    assert after["next_triggered_slot"] == next_slot(after["update_schedule"], due).isoformat()
    runs = await store.list_runs(ctx.account_id, ctx.user.user_id, compile_id)
    assert len(runs) == 1 and runs[0]["meta"]["trigger"] == "scheduled"
    assert (
        await scheduler.trigger_if_due(ctx.account_id, ctx.user.user_id, compile_id, due)
        == "not_due"
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            service.compile,
            "create",
            AsyncMock(side_effect=UnavailableError("compile", "queue unavailable")),
        )
        with pytest.raises(UnavailableError):
            await scheduler.trigger_if_due(
                ctx.account_id, ctx.user.user_id, compile_id, due + timedelta(days=1)
            )
    assert await store.get(ctx.account_id, ctx.user.user_id, compile_id) == after


async def test_configuration_update_preserves_times_and_retry_payload(service, app, monkeypatch):
    ctx, _ = await prepare(service, app, monkeypatch)
    store = service.compile.lifecycle.store
    compile_id = (
        await service.compile.create_compile_task(
            CompileCreateRequest.model_validate(INPUTS), ctx=ctx
        )
    )["compile_id"]
    request = CompileRequest(compile_id=compile_id)
    task = await service.compile.create(
        request, ctx=ctx, connection={}, idempotency_key="compile-frozen-config"
    )
    async with store.edit(ctx.account_id, ctx.user.user_id, compile_id) as record:
        record["progress"] = {"source_commit": "old-success"}
        record["conflict_num"] = 7
    await service.compile.update_config(
        CompileConfigUpdate.model_validate(
            {
                **INPUTS,
                "compile_id": compile_id,
                "instruction": "changed",
                "auto_update": False,
            }
        ),
        ctx,
    )
    updated = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
    assert updated["task_first_create_time"] == updated["last_run_time"] == task.created_at
    assert updated["progress"] == {}
    assert updated["conflict_num"] == 7
    changed = CompileConfigUpdate.model_validate(
        {**INPUTS, "compile_id": compile_id, "auto_update": False}
    )
    changed.to = "viking://resources/new-target"
    await service.compile.update_config(changed, ctx)
    assert (await store.get(ctx.account_id, ctx.user.user_id, compile_id))["conflict_num"] is None
    recovered = await service.compile.create(
        request, ctx=ctx, connection={}, idempotency_key="compile-frozen-config"
    )
    assert recovered.task_id == task.task_id
    run = await store.get_run(ctx.account_id, ctx.user.user_id, compile_id, task.task_id)
    assert run["params"]["instruction"] is None
    assert "operation_id" not in json.dumps(run)


@pytest.mark.parametrize(
    "private_args",
    [
        {"api_key": "private-secret", "model": "example"},
        {"provider": {"api_key": "private-secret", "model": "example"}},
        {"providers": [{"user_key": "private-secret", "name": "example"}]},
    ],
)
async def test_configuration_private_args_round_trip_and_update(
    service, app, monkeypatch, private_args
):
    ctx, _ = await prepare(service, app, monkeypatch)
    compile_id = (
        await service.compile.create_compile_task(
            CompileCreateRequest.model_validate({**INPUTS, "args": private_args}), ctx=ctx
        )
    )["compile_id"]
    store = service.compile.lifecycle.store
    state = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
    public = await service.compile.get_config(compile_id, ctx)
    assert "private-secret" not in json.dumps(state)
    assert "private-secret" not in json.dumps(public)
    async with store.edit(ctx.account_id, ctx.user.user_id, compile_id) as record:
        record["progress"] = {"source_commit": "successful-baseline"}

    for args in (public["args"], private_args):
        await service.compile.update_config(
            CompileConfigUpdate.model_validate(
                {
                    **INPUTS,
                    "compile_id": compile_id,
                    "args": args,
                    "auto_update": False,
                }
            ),
            ctx,
        )
        updated = await store.get(ctx.account_id, ctx.user.user_id, compile_id)
        assert updated["progress"] == {"source_commit": "successful-baseline"}
        assert (
            updated["execution_context"]["private_args"]
            == state["execution_context"]["private_args"]
        )

    task = await service.compile.create(
        CompileRequest(compile_id=compile_id), connection={}, ctx=ctx
    )
    auth = await service.compile._tasks._task_auth(task.task_id, ctx.account_id, ctx.user.user_id)
    assert (
        service.compile._merge_args(task.meta["request"], auth["external_request_private"])
        == private_args
    )
    run = await store.get_run(ctx.account_id, ctx.user.user_id, compile_id, task.task_id)
    assert "private-secret" not in json.dumps(run)
    if "provider" in private_args:
        with pytest.raises(InvalidArgumentError, match="resubmitting"):
            await service.compile.update_config(
                CompileConfigUpdate.model_validate(
                    {
                        **INPUTS,
                        "compile_id": compile_id,
                        "auto_update": False,
                        "args": {"provider": {"model": "different"}},
                    }
                ),
                ctx,
            )


async def test_run_queries_check_frozen_target_permissions(service, app, monkeypatch):
    ctx, _ = await prepare(service, app, monkeypatch)
    compile_id = (
        await service.compile.create_compile_task(
            CompileCreateRequest.model_validate(INPUTS), ctx=ctx
        )
    )["compile_id"]
    task = await service.compile.create(
        CompileRequest(compile_id=compile_id), connection={}, ctx=ctx
    )
    async with service.compile.lifecycle.store.edit(
        ctx.account_id, ctx.user.user_id, compile_id
    ) as record:
        record["to"] = "viking://resources/another-target"

    async def authorize(uri, *args, **kwargs):
        if uri == TARGET:
            raise PermissionDeniedError("Historical output permission revoked")

    monkeypatch.setattr(service.viking_fs, "_ensure_access", authorize)
    with pytest.raises(PermissionDeniedError):
        await service.compile.get_run(compile_id, task.task_id, ctx)
    assert await service.compile.list_runs(compile_id, ctx, limit=20, cursor=None) == {
        "runs": [],
        "next_cursor": None,
    }
