"""Persistent configuration, full execution, and shared scheduling contracts."""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.service.compile_scheduler import CompileScheduler
from openviking.service.compile_service import (
    CompileConfigUpdate,
    CompileCreateRequest,
    CompileRequest,
    CompileSessionAccepted,
    CompileSessionStatus,
)
from openviking.service.compile_targets import new_record, parse_schedule
from openviking.service.task_tracker import get_task_tracker
from openviking.storage.errors import ResourceBusyError
from openviking.storage.queuefs import get_queue_manager
from openviking.storage.queuefs.compile_target_tag_processor import CompileTargetTagProcessor
from openviking.storage.queuefs.process_result import ProcessOutcome
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    UnavailableError,
)
from openviking_cli.session.user_id import UserIdentifier

SOURCE = "viking://resources/source"
TARGET = "viking://resources/wiki"
SKILL = "viking://system/compile/example-skills/llm-wiki"
INPUTS = {"from": [SOURCE], "to": TARGET, "skill": SKILL}
SAVED_INPUTS = {**INPUTS, "compile_name": "research-summary"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"compile_name": "../state"},
        {"compile_name": "-demo"},
        {"compile_name": "research-summary", "to": TARGET},
        {"compile_name": "research-summary", "args": []},
        {"compile_id": "research-summary"},
        {**INPUTS, "compile_name": None},
        {**INPUTS, "operation_id": "cmp_example"},
        {**INPUTS, "args": {"mode": "incremental"}},
        {**INPUTS, "args": {"changes": {}}},
        {**INPUTS, "args": {"last_compile_time": 1}},
        {**INPUTS, "args": {"auto_update": False}},
        {**INPUTS, "args": {"update_schedule": None}},
        {**INPUTS, "args": {"owner_user_id": "alice"}},
        {**INPUTS, "reason": "old alias"},
        {**INPUTS, "owner_user_id": "someone-else"},
        {**INPUTS, "user_id": "someone-else"},
    ],
)
def test_execution_request_rejects_ambiguous_or_legacy_controls(payload):
    with pytest.raises(ValidationError):
        CompileRequest.model_validate(payload)


def test_named_execution_guides_mixed_requests_before_validating_inputs():
    with pytest.raises(ValidationError, match="send compile_name and optional args.owner_user_id"):
        CompileRequest.model_validate({"compile_name": "jj", "from": None})


@pytest.mark.parametrize("owner", [None, "", "../alice", 1])
def test_named_execution_rejects_invalid_owner(owner):
    with pytest.raises(ValidationError, match="args.owner_user_id"):
        CompileRequest.model_validate({"compile_name": "wiki", "args": {"owner_user_id": owner}})


@pytest.mark.parametrize("owner", [{}, {"owner_user_id": "alice"}])
def test_named_execution_ignores_non_owner_args(owner):
    request = CompileRequest.model_validate(
        {"compile_name": "wiki", "args": {**owner, "model_name": "ignored", "mode": "full"}}
    )
    assert request.args == owner


@pytest.mark.parametrize(
    "args",
    [
        {"auto_update": False},
        {"update_schedule": None},
        {"auto_update": True, "update_schedule": {"frequency": "daily", "hour": 10}},
    ],
)
async def test_execution_reports_misplaced_scheduling_options(client, args):
    response = await client.post("/api/v1/compile", json={**INPUTS, "args": args})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_ARGUMENT"
    assert error["details"]["validation_errors"][0]["loc"] == ["body"]
    message = error["message"]
    assert message.startswith("ov compile (POST /api/v1/compile)")
    assert ", ".join(sorted(args)) in message
    assert "ov compile create --compile-name <name>" in message
    assert "POST /api/v1/compile/create" in message
    assert "POST /api/v1/compile/config/update" in message
    assert "https://docs.openviking.ai/en/api/23-agent-runtime" in message


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


async def test_creation_requires_name_before_side_effects(service, app, client, monkeypatch):
    ctx, runtime = await prepare(service, app, monkeypatch)
    for body in (
        INPUTS,
        {**INPUTS, "compile_name": ""},
        {**INPUTS, "compile_name": "../state"},
    ):
        response = await client.post("/api/v1/compile/create", json=body)
        assert response.status_code == 400, response.text
        assert "compile_name" in response.text
    service.fs.ensure_compile_target.assert_not_awaited()
    assert not runtime.calls
    assert not [
        record async for record in service.compile.lifecycle.store.iter_records(ctx.account_id)
    ]


async def test_named_configuration_conflicts_and_lookup_permissions(
    service, app, client, monkeypatch
):
    ctx, _ = await prepare(service, app, monkeypatch)
    created = await client.post("/api/v1/compile/create", json=SAVED_INPUTS)
    assert created.status_code == 200, created.text
    name = SAVED_INPUTS["compile_name"]
    store = service.compile.lifecycle.store
    original = await store.get(ctx.account_id, ctx.user.user_id, name)
    assert original["execution_context"]["executor"]["user_id"] == ctx.user.user_id
    response = await client.post(
        "/api/v1/compile/create", json={**SAVED_INPUTS, "instruction": "overwrite"}
    )
    assert response.status_code == 409, response.text
    assert await store.get(ctx.account_id, ctx.user.user_id, name) == original
    service.fs.ensure_compile_target.assert_awaited_once()
    response = await client.get("/api/v1/compile/config", params={"compile_name": name})
    assert response.status_code == 200, response.text
    assert response.json()["result"]["compile_name"] == name
    response = await client.post("/api/v1/compile", json={"compile_name": "missing"})
    assert response.status_code == 404, response.text
    foreign = RequestContext(
        user=UserIdentifier("another-account", ctx.user.user_id), role=Role.USER
    )
    app.dependency_overrides[get_request_context] = lambda: foreign
    response = await client.get("/api/v1/compile/config", params={"compile_name": name})
    assert response.status_code == 404, response.text
    app.dependency_overrides[get_request_context] = lambda: ctx
    monkeypatch.setattr(
        service.viking_fs, "_ensure_access", AsyncMock(side_effect=PermissionDeniedError("denied"))
    )
    response = await client.post("/api/v1/compile", json={"compile_name": name})
    assert response.status_code == 403, response.text


async def test_same_name_configuration_and_runs_are_user_scoped(service, app, client, monkeypatch):
    ctx, _ = await prepare(service, app, monkeypatch)
    other = RequestContext(user=UserIdentifier(ctx.account_id, "another-owner"), role=Role.USER)
    name = SAVED_INPUTS["compile_name"]
    task_ids = []
    app.dependency_overrides[get_request_context] = lambda: actor
    for actor in (ctx, other):
        missing = await client.get("/api/v1/compile/config", params={"compile_name": name})
        assert missing.status_code == 404, missing.text
        created = await client.post(
            "/api/v1/compile/create", json={**SAVED_INPUTS, "instruction": actor.user.user_id}
        )
        assert created.status_code == 200, created.text
        configs = (await client.get("/api/v1/compile/configs")).json()["result"]["configs"]
        assert [(c["compile_name"], c["instruction"]) for c in configs] == [
            (name, actor.user.user_id)
        ]
        assert configs[0]["executor_user_id"] == actor.user.user_id
        accepted = await client.post(
            "/api/v1/compile",
            json={"compile_name": name},
            headers={"Idempotency-Key": "same-user-scoped-key"},
        )
        assert accepted.status_code == 202, accepted.text
        assert all({"deprecation", "link"}.isdisjoint(r.headers) for r in (created, accepted))
        task_ids.append(accepted.json()["result"]["task_id"])
        runs = (await client.get("/api/v1/compile/runs", params={"compile_name": name})).json()[
            "result"
        ]["runs"]
        assert [r["task_id"] for r in runs] == [task_ids[-1]]
        assert runs[0]["params"]["instruction"] == actor.user.user_id
    assert task_ids[0] != task_ids[1]
    foreign_run = await client.get(
        f"/api/v1/compile/runs/{task_ids[0]}", params={"compile_name": name}
    )
    assert foreign_run.status_code == 404, foreign_run.text
    changed = await client.post(
        "/api/v1/compile/config/update",
        json={**SAVED_INPUTS, "instruction": "updated", "auto_update": False},
    )
    assert changed.status_code == 200, changed.text
    assert (await service.compile.get_config(name, ctx))["instruction"] == ctx.user.user_id
    assert (await service.compile.get_config(name, other))["instruction"] == "updated"


async def test_delegated_execution_owner_actor_and_retries(service, app, client, monkeypatch):
    ctx, runtime = await prepare(service, app, monkeypatch)
    actor = RequestContext(
        user=UserIdentifier(ctx.account_id, "executor"), role=Role.USER, api_key="executor-key"
    )
    app.dependency_overrides[get_request_context] = lambda: actor
    store = service.compile.lifecycle.store
    name = SAVED_INPUTS["compile_name"]
    for owner in ("alice", "bob", "executor"):
        created = await client.post(
            "/api/v1/compile/create",
            json={
                **SAVED_INPUTS,
                "instruction": owner,
                "args": {"owner_user_id": owner, "model_name": "saved"},
            },
        )
        assert created.status_code == 200, created.text
    payload = {"compile_name": name, "args": {"owner_user_id": "alice"}}
    headers = {"Idempotency-Key": "compile-delegated-owner"}
    response = await client.post(
        "/api/v1/compile",
        json={**payload, "args": {**payload["args"], "model_name": "ignored", "auto_update": True}},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["result"]["task_id"]
    await service.compile._tasks.execute(task_id, account_id=ctx.account_id, user_id="executor")
    run = await store.get_run(ctx.account_id, "alice", name, task_id)
    assert run["status"] == "completed", run["error"]
    assert run["actor"] == "executor" and run["params"]["instruction"] == "alice"
    assert runtime.calls[0]["args"]["model_name"] == "saved"
    assert "owner_user_id" not in runtime.calls[0]["args"]
    repeated = await client.post("/api/v1/compile", json=payload, headers=headers)
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["result"]["task_id"] == task_id
    conflict = await client.post(
        "/api/v1/compile",
        json={"compile_name": name, "args": {"owner_user_id": "bob"}},
        headers=headers,
    )
    assert conflict.status_code == 409, conflict.text

    own_ids = set()
    for args in ({}, {"owner_user_id": "executor"}):
        response = await client.post(
            "/api/v1/compile",
            json={"compile_name": name, "args": args},
            headers={"Idempotency-Key": "compile-default-owner"},
        )
        assert response.status_code == 202, response.text
        own_ids.add(response.json()["result"]["task_id"])
    assert len(own_ids) == 1 and task_id not in own_ids
    foreign = RequestContext(user=UserIdentifier("another-account", "executor"), role=Role.USER)
    app.dependency_overrides[get_request_context] = lambda: foreign
    assert (await client.post("/api/v1/compile", json=payload)).status_code == 404


async def test_case_distinct_names_paginate(service, app, client, monkeypatch):
    await prepare(service, app, monkeypatch)
    names = ["Research-summary", "research-summary"]
    for name in names:
        created = await client.post(
            "/api/v1/compile/create", json={**INPUTS, "compile_name": name, "instruction": name}
        )
        assert created.status_code == 200, created.text
    first = (await client.get("/api/v1/compile/configs", params={"limit": 1})).json()["result"]
    assert [config["compile_name"] for config in first["configs"]] == names[:1]
    assert first["next_cursor"] == names[0]
    second = (
        await client.get(
            "/api/v1/compile/configs", params={"limit": 1, "cursor": first["next_cursor"]}
        )
    ).json()["result"]
    assert [config["compile_name"] for config in second["configs"]] == names[1:]
    assert second["next_cursor"] is None


async def test_configuration_list_page_order(service, app, monkeypatch):
    ctx, _ = await prepare(service, app, monkeypatch)
    store = service.compile.lifecycle.store
    for name, timestamp in (("alpha", None), ("beta", 100), ("gamma", 300)):
        record = new_record(INPUTS) | {"last_run_time": timestamp}
        await store.create(ctx.account_id, ctx.user.user_id, record, compile_name=name)
    first = await service.compile.list_configs(ctx, limit=2, cursor=None)
    second = await service.compile.list_configs(ctx, limit=2, cursor=first["next_cursor"])
    assert [config["compile_name"] for config in first["configs"]] == ["beta", "alpha"]
    assert [config["compile_name"] for config in second["configs"]] == ["gamma"]


@pytest.mark.parametrize("role", [Role.USER, Role.GLOBAL, Role.ROOT])
async def test_account_configuration_listing_requires_admin(service, app, client, role):
    ctx = RequestContext(user=service.user, role=role)
    app.dependency_overrides[get_request_context] = lambda: ctx
    response = await client.get("/api/v1/compile/configs", params={"scope": "account"})
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("scope,role", [("user", Role.USER), ("account", Role.ADMIN)])
async def test_configuration_list_metadata_and_cross_user_paging(
    service, app, client, monkeypatch, scope, role
):
    ctx, _ = await prepare(service, app, monkeypatch)
    store = service.compile.lifecycle.store
    for owner in ("alice", "bob"):
        await store.create(ctx.account_id, owner, new_record(INPUTS), compile_name="team_compile")
    await store.create("foreign-account", "alice", new_record(INPUTS), compile_name="team_compile")
    async with store.edit(ctx.account_id, "alice", "team_compile") as record:
        record["updated_by"] = "editor"
        record["conflict_num"] = 7
        record["execution_context"]["executor"] = {"user_id": "global-executor"}
    actor = RequestContext(user=UserIdentifier(ctx.account_id, "alice"), role=role)
    app.dependency_overrides[get_request_context] = lambda: actor
    params = {"scope": scope, "compile_name": "team_compile", "limit": 1}
    response = await client.get("/api/v1/compile/configs", params=params)
    assert response.status_code == 200, response.text
    page = response.json()["result"]
    assert len(page["configs"]) == 1
    first = page["configs"][0]
    assert first["conflict_num"] == 7
    assert {key: first[key] for key in ("owner_user_id", "executor_user_id", "updated_by")} == {
        "owner_user_id": "alice",
        "executor_user_id": "global-executor",
        "updated_by": "editor",
    }
    assert "execution_context" not in first
    if scope == "user":
        assert page["next_cursor"] is None
    else:
        assert page["next_cursor"] is not None
        response = await client.get(
            "/api/v1/compile/configs", params={**params, "cursor": page["next_cursor"]}
        )
        assert response.status_code == 200, response.text
        page = response.json()["result"]
        assert len(page["configs"]) == 1
        second = page["configs"][0]
        assert second["owner_user_id"] == "bob" and second["updated_by"] == "bob"
        assert second["executor_user_id"] is None
        assert page["next_cursor"] is None


@pytest.mark.parametrize("endpoint", ["/api/v1/compile/create", "/api/v1/compile"])
async def test_compile_admission_rejects_invalid_inputs_before_publication(
    service, app, client, monkeypatch, endpoint
):
    ctx, runtime = await prepare(service, app, monkeypatch)
    inputs = SAVED_INPUTS if endpoint.endswith("/create") else INPUTS
    for target in (SOURCE, f"{SOURCE}/child", "viking://~/memories/experiences/project/"):
        response = await client.post(endpoint, json={**inputs, "to": target})
        assert response.status_code == 400, response.text
    service.fs.ensure_compile_target.assert_not_awaited()
    service.fs.ensure_compile_target.side_effect = UnavailableError("tags", "unavailable")
    response = await client.post(endpoint, json=inputs)
    assert response.status_code == 503, response.text
    monkeypatch.setattr(service.fs, "stat", AsyncMock(side_effect=NotFoundError(SOURCE, "file")))
    response = await client.post(endpoint, json=inputs)
    assert response.status_code == 404, response.text
    assert "The source directory is not found." in response.json()["error"]["message"]
    assert response.json()["error"]["details"]["resource"] == SOURCE
    assert not runtime.calls
    assert not [r async for r in service.compile.lifecycle.store.iter_records(ctx.account_id)]
    assert not await get_task_tracker().list_tasks(
        task_type="compile", account_id=ctx.account_id, user_id=ctx.user.user_id, limit=None
    )


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN, Role.GLOBAL])
async def test_configuration_runs_full_isolation_and_failure_recovery(
    service, app, client, monkeypatch, role
):
    ensure_target = service.fs.ensure_compile_target
    ctx, runtime = await prepare(service, app, monkeypatch)
    ctx.role, ctx.group_ids = Role(role), ("reviewers",)
    commit = AsyncMock(wraps=service.viking_fs.commit)
    monkeypatch.setattr(service.viking_fs, "commit", commit)
    monkeypatch.setattr(service.fs, "ensure_compile_target", AsyncMock(wraps=ensure_target))
    service.fs.finalize_compile_directories.return_value = False
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    store = service.compile.lifecycle.store
    tracker = get_task_tracker()

    created = await client.post("/api/v1/compile/create", json=SAVED_INPUTS)
    assert created.status_code == 200, created.text
    assert created.json()["result"] == {"compile_name": "research-summary"}
    compile_name = created.json()["result"]["compile_name"]
    state = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
    assert state["task_first_create_time"] is state["last_run_time"] is None
    assert await store.list_runs(ctx.account_id, ctx.user.user_id, compile_name) == []
    assert runtime.calls == []
    service.fs.ensure_compile_target.assert_awaited_once_with(TARGET, ctx)
    assert state["conflict_num"] is None

    nested = await client.post("/api/v1/compile", json={**INPUTS, "to": TARGET + "/child"})
    assert nested.status_code == 409, nested.text
    assert "Choose another directory" in nested.json()["error"]["message"]
    assert runtime.calls == []

    async def execute(payload, key):
        response = await client.post(
            "/api/v1/compile", json=payload, headers={"Idempotency-Key": key}
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["result"]["task_id"]
        await service.compile._tasks.execute(task_id, **owner)
        for call in commit.await_args_list:
            executor = call.kwargs["ctx"]
            assert (executor.role, executor.group_ids, executor.bypass_acl) == (
                role,
                ctx.group_ids,
                False,
            )
        return await tracker.get(task_id, **owner)

    first = await execute({"compile_name": compile_name}, "compile-first-execution")
    assert first.status == "completed", first.error
    state = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
    assert state["task_first_create_time"] == state["last_run_time"] == first.created_at
    run = await store.get_run(ctx.account_id, ctx.user.user_id, compile_name, first.task_id)
    assert run["meta"]["mode"] == "full"
    assert run["actor"] == ctx.user.user_id
    assert state["execution_context"]["owner_user_id"] == ctx.user.user_id
    assert state["conflict_num"] == 0
    assert state["progress"]["source_commit"] == run["meta"]["start_snapshot"]
    assert state["progress"]["model_name"] == ""
    assert runtime.calls[-1]["args"]["changes"]["execution"]["mode"] == "full"
    assert "compile" not in runtime.calls[-1]
    assert "compile_name" not in runtime.calls[-1]

    repeated = await execute({"compile_name": compile_name}, "compile-first-execution")
    assert repeated.task_id == first.task_id
    assert (await store.get(ctx.account_id, ctx.user.user_id, compile_name))[
        "last_run_time"
    ] == first.created_at
    assert len(runtime.calls) == 1

    added_source = SOURCE + "-added"
    await service.viking_fs.write_file(f"{added_source}/source.md", "source", ctx=ctx)
    update = CompileConfigUpdate(**SAVED_INPUTS, auto_update=False)
    for sources in ([SOURCE, added_source], [SOURCE]):
        update.from_ = sources
        await service.compile.update_config(update, ctx)

    noop = await execute({"compile_name": compile_name}, "compile-noop-execution")
    assert noop.status == "completed", noop.error
    assert noop.task_id != first.task_id
    assert len(runtime.calls) == 1
    state = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
    assert state["task_first_create_time"] == first.created_at
    assert state["last_run_time"] == noop.created_at
    assert state["last_triggered_slot"] is None

    await service.viking_fs.write_file(f"{SOURCE}/source.md", "updated", ctx=ctx)
    incremental = await execute({"compile_name": compile_name}, "compile-updated-source")
    assert incremental.status == "completed", incremental.error
    assert runtime.calls[-1]["args"]["changes"]["execution"]["mode"] == "incremental"
    update.args = {"model_name": "model-a"}
    await service.compile.update_config(update, ctx)
    modeled = await execute({"compile_name": compile_name}, "compile-model-change")
    assert modeled.status == "completed", modeled.error
    assert runtime.calls[-1]["args"]["changes"]["execution"]["mode"] == "full"

    state_before_full = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
    assert state_before_full["progress"]["model_name"] == "model-a"
    runs_before_full = await store.list_runs(ctx.account_id, ctx.user.user_id, compile_name)
    full = await execute(INPUTS, "compile-full-execution")
    assert full.status == "completed", full.error
    assert runtime.calls[-1]["args"]["changes"]["head"] is None
    assert runtime.calls[-1]["args"]["changes"]["execution"]["mode"] == "full"
    assert await store.get(ctx.account_id, ctx.user.user_id, compile_name) == state_before_full
    assert await store.list_runs(ctx.account_id, ctx.user.user_id, compile_name) == runs_before_full

    preserved = await service.fs.read(f"{TARGET}/page.md", ctx)
    await service.viking_fs.write_file(f"{SOURCE}/source.md", "another update", ctx=ctx)
    runtime.status = "failed"
    failed = await execute({"compile_name": compile_name}, "compile-failed-execution")
    assert failed.status == "failed"
    after_failure = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
    assert after_failure["progress"] == state_before_full["progress"]
    assert after_failure["last_run_time"] == failed.created_at
    assert await service.fs.read(f"{TARGET}/page.md", ctx) == preserved
    assert not await service.viking_fs.exists(f"{TARGET}/partial.md", ctx=ctx)
    failed_run = await store.get_run(ctx.account_id, ctx.user.user_id, compile_name, failed.task_id)
    assert failed_run["meta"]["finish_snapshot"] is not None

    history = await client.get(
        "/api/v1/compile/runs", params={"compile_name": compile_name, "limit": 2}
    )
    assert history.status_code == 200, history.text
    assert history.json()["result"]["runs"][0]["task_id"] == failed.task_id
    assert history.json()["result"]["next_cursor"] is not None

    # Output history distinguishes the frozen run from later edits to this file and other files.
    uri = f"{TARGET}/page.md"
    await service.viking_fs.write_file(uri, "manual update", ctx=ctx)
    changed = await service.fs.commit(message="edit output", paths=[uri], ctx=ctx)
    unrelated = f"{TARGET}/other.md"
    await service.viking_fs.write_file(unrelated, "other file", ctx=ctx)
    await service.fs.commit(message="edit other file", paths=[unrelated], ctx=ctx)
    response = await client.get(
        f"/api/v1/compile/runs/{first.task_id}/outputs", params={"compile_name": compile_name}
    )
    assert response.status_code == 200, response.text
    (output,) = response.json()["result"]["outputs"]
    assert output["uri"] == uri
    assert output["snapshot_id"] == run["meta"]["finish_snapshot"]
    assert output["latest_snapshot_id"] == changed["commit_oid"]
    assert await service.fs.show(output["snapshot_id"], path=uri, ctx=ctx) == b"generated 1"
    assert (
        await service.fs.show(output["latest_snapshot_id"], path=uri, ctx=ctx) == b"manual update"
    )


@pytest.mark.parametrize("rule", [{"frequency": "daily", "hour": 10}, {"frequency": "weekly"}])
def test_omitted_schedule_fields_spread_across_plans(rule):
    slots = [parse_schedule(rule, ("account", "owner", str(i))) for i in range(100)]
    assert len({slot["minute"] for slot in slots}) > 1
    assert {slot["hour"] for slot in slots} == ({10} if "hour" in rule else set(range(4)))
    if rule["frequency"] == "weekly":
        assert {slot["weekday"] for slot in slots} == set(range(1, 8))


async def test_scheduled_execution_shares_entry_times_and_failed_slots(service, app, monkeypatch):
    ctx, _ = await prepare(service, app, monkeypatch)
    store = service.compile.lifecycle.store
    scheduler = CompileScheduler(service.compile, store)
    scheduler._expiry = SimpleNamespace(report=AsyncMock(), unregister=AsyncMock())
    scheduler.save_credential = AsyncMock(return_value={"ciphertext": "test"})
    executor = RequestContext(user=UserIdentifier(ctx.account_id, "executor"), role=Role.USER)
    scheduler._executor = AsyncMock(
        return_value=(executor, {"account_id": ctx.account_id, "user_id": executor.user.user_id})
    )
    service.compile.configure_scheduler(scheduler)
    rule = {"frequency": "daily", "hour": 10}
    result = await service.compile.create_compile_task(
        CompileCreateRequest.model_validate({**SAVED_INPUTS, "args": {"update_schedule": rule}}),
        ctx=ctx,
    )
    compile_name = result["compile_name"]
    before = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
    assert before["execution_context"]["executor"]["user_id"] == ctx.user.user_id
    due = datetime.fromisoformat(before["next_triggered_slot"])
    assert (
        await scheduler.trigger_if_due(ctx.account_id, ctx.user.user_id, compile_name, due)
        == "submitted"
    )
    after = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
    assert after["task_first_create_time"] == after["last_run_time"] is not None
    assert after["last_triggered_slot"] == due.isoformat()
    assert after["next_triggered_slot"] == (due + timedelta(days=1)).isoformat()
    runs = await store.list_runs(ctx.account_id, ctx.user.user_id, compile_name)
    assert len(runs) == 1 and runs[0]["meta"]["trigger"] == "initial"
    assert runs[0]["actor"] == executor.user.user_id
    assert await store.get(ctx.account_id, executor.user.user_id, compile_name) is None
    assert (
        await scheduler.trigger_if_due(ctx.account_id, ctx.user.user_id, compile_name, due)
        == "not_due"
    )
    plan = {**before["update_schedule"], "hour": 9}
    other_owner = "another-owner"
    await service.compile.create_compile_task(
        CompileCreateRequest.model_validate(
            {
                **SAVED_INPUTS,
                "args": {"owner_user_id": other_owner, "update_schedule": plan},
            }
        ),
        ctx=ctx,
    )
    assert scheduler._expiry.report.await_args.args[0].identity.user_id == other_owner
    assert (await store.get(ctx.account_id, other_owner, compile_name))["updated_by"] == other_owner
    assert (
        await scheduler.trigger_if_due(ctx.account_id, other_owner, compile_name, due)
        == "submitted"
    )
    other_runs = await store.list_runs(ctx.account_id, other_owner, compile_name)
    assert len(other_runs) == 1 and other_runs[0]["task_id"] != runs[0]["task_id"]
    assert other_runs[0]["actor"] == executor.user.user_id
    with monkeypatch.context() as patch:
        patch.setattr(
            service.compile,
            "create",
            AsyncMock(side_effect=UnavailableError("compile", "queue unavailable")),
        )
        with pytest.raises(UnavailableError):
            await scheduler.trigger_if_due(
                ctx.account_id, ctx.user.user_id, compile_name, due + timedelta(days=1)
            )
    assert await store.get(ctx.account_id, ctx.user.user_id, compile_name) == after
    update = CompileConfigUpdate.model_validate(
        {**SAVED_INPUTS, "auto_update": False, "args": {"owner_user_id": other_owner}}
    )
    saved = await service.compile.update_config(update, ctx)
    assert saved["update_schedule"] == plan
    disabled = await store.get(ctx.account_id, other_owner, compile_name)
    assert disabled["execution_context"]["executor"]["user_id"] == ctx.user.user_id
    assert disabled["execution_context"]["credential"] is disabled["next_triggered_slot"] is None


async def test_configuration_update_preserves_times_and_retry_payload(service, app, monkeypatch):
    ctx, _ = await prepare(service, app, monkeypatch)
    store = service.compile.lifecycle.store
    compile_name = (
        await service.compile.create_compile_task(
            CompileCreateRequest.model_validate(SAVED_INPUTS), ctx=ctx
        )
    )["compile_name"]
    request = CompileRequest(compile_name=compile_name)
    task = await service.compile.create(
        request, ctx=ctx, connection={}, idempotency_key="compile-frozen-config"
    )
    async with store.edit(ctx.account_id, ctx.user.user_id, compile_name) as record:
        record["progress"] = {"source_commit": "old-success"}
        record["conflict_num"] = 7
    source = SOURCE + "-updated"
    await service.viking_fs.write_file(f"{source}/source.md", "source", ctx=ctx)
    editor = RequestContext(
        user=UserIdentifier(ctx.account_id, "editor"), role=Role.USER, api_key="editor-key"
    )
    await service.compile.update_config(
        CompileConfigUpdate.model_validate(
            {
                "compile_name": compile_name,
                "from": [source],
                "skill": "viking://system/compile/example-skills/daily-report",
                "instruction": "changed",
                "auto_update": False,
                "updated_by": "bob",
                "args": {"owner_user_id": ctx.user.user_id},
            }
        ),
        editor,
    )
    updated = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
    assert updated["updated_by"] == "bob"
    assert updated["execution_context"]["executor"]["user_id"] == editor.user.user_id
    assert updated["task_first_create_time"] == updated["last_run_time"] == task.created_at
    assert updated["progress"] == {"source_commit": "old-success"}
    assert updated["conflict_num"] == 7
    assert updated["to"] == TARGET
    assert updated["from"] == [source]
    assert updated["skill"] == "viking://system/compile/example-skills/daily-report"
    changed = CompileConfigUpdate.model_validate(
        {**INPUTS, "compile_name": compile_name, "auto_update": False}
    )
    changed.to = "viking://resources/new-target"
    with pytest.raises(InvalidArgumentError, match="to must match"):
        await service.compile.update_config(changed, ctx)
    assert await store.get(ctx.account_id, ctx.user.user_id, compile_name) == updated
    recovered = await service.compile.create(
        request, ctx=ctx, connection={}, idempotency_key="compile-frozen-config"
    )
    assert recovered.task_id == task.task_id
    run = await store.get_run(ctx.account_id, ctx.user.user_id, compile_name, task.task_id)
    assert run["params"]["instruction"] is None


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
    compile_name = (
        await service.compile.create_compile_task(
            CompileCreateRequest.model_validate({**SAVED_INPUTS, "args": private_args}), ctx=ctx
        )
    )["compile_name"]
    store = service.compile.lifecycle.store
    state = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
    public = await service.compile.get_config(compile_name, ctx)
    assert "private-secret" not in json.dumps(state)
    assert "private-secret" not in json.dumps(public)
    async with store.edit(ctx.account_id, ctx.user.user_id, compile_name) as record:
        record["progress"] = {"source_commit": "successful-baseline"}

    for args in (public["args"], private_args):
        await service.compile.update_config(
            CompileConfigUpdate.model_validate(
                {
                    **INPUTS,
                    "compile_name": compile_name,
                    "args": args,
                    "auto_update": False,
                }
            ),
            ctx,
        )
        updated = await store.get(ctx.account_id, ctx.user.user_id, compile_name)
        assert updated["progress"] == {"source_commit": "successful-baseline"}
        assert (
            updated["execution_context"]["private_args"]
            == state["execution_context"]["private_args"]
        )

    task = await service.compile.create(
        CompileRequest(compile_name=compile_name), connection={}, ctx=ctx
    )
    auth = await service.compile._tasks._task_auth(task.task_id, ctx.account_id, ctx.user.user_id)
    assert (
        service.compile._merge_args(task.meta["request"], auth["external_request_private"])
        == private_args
    )
    run = await store.get_run(ctx.account_id, ctx.user.user_id, compile_name, task.task_id)
    assert "private-secret" not in json.dumps(run)
    if "provider" in private_args:
        with pytest.raises(InvalidArgumentError, match="resubmitting"):
            await service.compile.update_config(
                CompileConfigUpdate.model_validate(
                    {
                        **INPUTS,
                        "compile_name": compile_name,
                        "auto_update": False,
                        "args": {"provider": {"model": "different"}},
                    }
                ),
                ctx,
            )


async def test_run_queries_check_frozen_target_permissions(service, app, monkeypatch):
    ctx, _ = await prepare(service, app, monkeypatch)
    compile_name = (
        await service.compile.create_compile_task(
            CompileCreateRequest.model_validate(SAVED_INPUTS), ctx=ctx
        )
    )["compile_name"]
    task = await service.compile.create(
        CompileRequest(compile_name=compile_name), connection={}, ctx=ctx
    )
    async with service.compile.lifecycle.store.edit(
        ctx.account_id, ctx.user.user_id, compile_name
    ) as record:
        record["to"] = "viking://resources/another-target"

    async def authorize(uri, *args, **kwargs):
        if uri == TARGET:
            raise PermissionDeniedError("Historical output permission revoked")

    monkeypatch.setattr(service.viking_fs, "_ensure_access", authorize)
    with pytest.raises(PermissionDeniedError):
        await service.compile.get_run(compile_name, task.task_id, ctx)
    assert await service.compile.list_runs(compile_name, ctx, limit=20, cursor=None) == {
        "runs": [],
        "next_cursor": None,
    }


@pytest.mark.parametrize(
    "target,nested", [(TARGET, TARGET + "/child"), (TARGET + "/child", TARGET)]
)
async def test_pending_compile_target_survives_tag_retry(service, monkeypatch, target, nested):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    ensure = service.fs.ensure_compile_target
    queue = get_queue_manager().get_queue("CompileTargetTag")
    deliveries = []

    async def capture(enqueue_ctx):
        deliveries.append(dict(enqueue_ctx.payload))
        enqueue_ctx.committed = True
        return str(len(deliveries))

    monkeypatch.setattr(queue, "_enqueue", capture)
    monkeypatch.setattr(
        "openviking.storage.queuefs.compile_target_tag_processor.sleep", AsyncMock()
    )
    monkeypatch.setattr(
        service.fs, "finalize_compile_directories", AsyncMock(side_effect=[True, False, True])
    )
    try:
        await asyncio.gather(ensure(target, ctx), ensure(target + "/", ctx))
        first, second = deliveries
        assert first["target_lock"]["holder_id"] != second["target_lock"]["holder_id"]
        assert (await queue.process_dequeued(first)).outcome is ProcessOutcome.SUCCESS
        assert (await queue.process_dequeued(second)).outcome is ProcessOutcome.REQUEUED

        with pytest.raises(ResourceBusyError, match="Choose another directory"):
            await asyncio.wait_for(ensure(nested, ctx), timeout=1.75)
        retry = deliveries[2]
        assert retry["_task_work_id"] == second["_task_work_id"]
        assert (await queue.process_dequeued(retry)).outcome is ProcessOutcome.SUCCESS
        await ensure(nested, ctx)
    finally:
        worker = CompileTargetTagProcessor(service.fs)
        for delivery in deliveries:
            await worker.on_cancelled(delivery)


async def test_failed_tag_enqueue_releases_compile_target(service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    queue = get_queue_manager().get_queue("CompileTargetTag")
    monkeypatch.setattr(queue, "_enqueue", AsyncMock(side_effect=OSError("queue unavailable")))
    with pytest.raises(OSError, match="queue unavailable"):
        await service.fs.ensure_compile_target(TARGET, ctx)
    requests, _ = service.fs._compile_target_lock_scope(TARGET, ctx)
    assert not await service.viking_fs._async_agfs.pathlock_is_locked(requests[0]["path"])
