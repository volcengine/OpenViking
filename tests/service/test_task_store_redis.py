# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real Redis acceptance for the optional Task/Work backend.

Set OV_TEST_TASK_REDIS_URL to a disposable Redis. Each test owns one random
namespace; it never flushes a server or changes Redis persistence settings.
"""

import asyncio
import json
import os
import sys
from uuid import uuid4

import pytest

from openviking.service.task_domain import (
    TaskPatch,
    TaskRecord,
    TaskRetentionPolicy,
    TaskStatus,
    WorkState,
)
from openviking.service.task_store import TaskAlreadyActive
from openviking.service.task_store_redis import _COMMIT, RedisTaskWorkStore
from openviking.service.task_tracker import TaskTracker
from openviking_cli.utils.config.storage_config import StorageConfig

OWNER = {"account_id": "account", "user_id": "user"}


def test_redis_task_store_configuration():
    config = StorageConfig(
        task_store={"provider": " REDIS ", "params": {"url": "redis://localhost/0"}}
    )
    tracker = config.build_task_tracker(None, service_name="one")
    assert isinstance(tracker._store, RedisTaskWorkStore)
    assert config.task_store.provider == "redis"
    assert StorageConfig().task_store.provider == "file"


@pytest.mark.parametrize(
    "override",
    [
        {"url": "https://localhost"},
        {"namespace": ""},
        {"namespace": "{other}"},
        {"socket_timeout": 0},
        {"socket_timeout": float("inf")},
        {"socket_timeout": True},
        {"max_connections": 0},
        {"max_connections": 1.5},
        {"service_name": ""},
        {"typo": True},
        {"url": "redis://localhost/0?retry_on_timeout=True"},
        {"namespace": "with space"},
    ],
)
def test_invalid_options(override):
    with pytest.raises(ValueError):
        RedisTaskWorkStore(params={"url": "redis://localhost/0", **override})


@pytest.fixture
async def redis_stores():
    url = os.environ.get("OV_TEST_TASK_REDIS_URL")
    if not url:
        pytest.skip("Set OV_TEST_TASK_REDIS_URL for real Redis acceptance")
    params = {"url": url, "namespace": "ov-acceptance-" + uuid4().hex}
    first, second = [RedisTaskWorkStore(params=params) for _ in range(2)]
    await first.load()
    await second.load()
    try:
        yield first, second, params
    finally:
        await first._client.delete(first._key)
        await first.close()
        await second.close()


async def _child(params, mode, start=0, end=0):
    env = {**os.environ, "OV_TEST_REDIS_CHILD": json.dumps([params, mode, start, end])}
    code = """
import asyncio,json,os
from openviking.service.task_store_redis import RedisTaskWorkStore, _COMMIT
from openviking.service.task_domain import TaskRecord
from openviking.service.task_tracker import TaskTracker
async def main():
 params,mode,start,end=json.loads(os.environ['OV_TEST_REDIS_CHILD'])
 store=RedisTaskWorkStore(params=params);await store.load();tracker=TaskTracker(store)
 try:
  if mode=='admit':
   await asyncio.gather(*[tracker.create_if_no_running('compile','synthetic',task_id=f'task-{i}',account_id='account',user_id='user') for i in range(start,end)])
  elif mode=='crash':
   original=store._client.eval
   async def exit_after_commit(script,*args):
    value=await original(script,*args)
    if script==_COMMIT:os._exit(77)
    return value
   store._client.eval=exit_after_commit
   await store.create(TaskRecord(task_id='crashed',task_type='compile',account_id='account',user_id='user'),reject_if_active=True)
  else:
   async def finish(i):
    await tracker.start_work('task',str(i),account_id='account',user_id='user')
    await tracker.mark_work_done('task',str(i),context_count_delta=1,account_id='account',user_id='user')
    await tracker.mark_work_failed('task',str(i),'late duplicate',account_id='account',user_id='user')
   await asyncio.gather(*[finish(i) for i in range(start,end)])
 finally:await store.close()
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, error = await asyncio.wait_for(process.communicate(), 90)
    except BaseException:
        process.kill()
        await process.communicate()
        raise
    assert process.returncode == (77 if mode == "crash" else 0), (out + error).decode()[-3000:]


@pytest.mark.asyncio
async def test_independent_process_admission_is_atomic(redis_stores):
    first, second, params = redis_stores
    await asyncio.gather(_child(params, "admit", 0, 20), _child(params, "admit", 20, 40))
    tasks = await second.list(OWNER["account_id"], user_id=OWNER["user_id"])
    assert len(tasks) == 1
    assert (await first.get_task(tasks[0].task_id, **OWNER)).status is TaskStatus.PENDING
    assert (await first.withdraw_unaccepted(tasks[0].task_id, **OWNER)).is_applied
    assert (
        await TaskTracker(second).create_if_no_running("compile", "synthetic", **OWNER)
    ) is not None


@pytest.mark.asyncio
async def test_fanout_duplicate_completion_restart_and_retention(redis_stores):
    first, second, params = redis_stores
    tracker = TaskTracker(first)
    await tracker.create("compile", task_id="task", **OWNER)
    await tracker.start("task", **OWNER)
    for i in range(64):
        await tracker.register_work("task", str(i), "Semantic", **OWNER)
    await tracker.complete("task", {"done": True}, **OWNER)
    await asyncio.gather(_child(params, "finish", 0, 32), _child(params, "finish", 32, 64))
    result = await TaskTracker(second).wait("task", timeout=0, **OWNER)
    assert result.status is TaskStatus.COMPLETED
    assert (await tracker.get_context_count("task", **OWNER)) == 64
    assert result.result["queue_status"]["Semantic"]["processed"] == 64
    assert result.result["queue_status"]["Semantic"]["error_count"] == 0
    await first.close()
    await first.load()
    restored = await first.get_task_with_events("task", **OWNER)
    assert restored.result == result.result
    assert restored.execution_events["items"][-1]["status"] == "completed"
    expired = await first.delete_expired(
        TaskRetentionPolicy(0, 0), now=restored.updated_at + 1, limit=1
    )
    assert expired.deleted_count == 1
    assert await second.get_task("task", **OWNER) is None
    assert await second.get_work("task", "0", **OWNER) is None


@pytest.mark.asyncio
async def test_foreign_event_loop_and_stale_snapshot_cannot_overwrite(redis_stores):
    first, second, _ = redis_stores
    tracker = TaskTracker(first)
    await tracker.create("compile", task_id="task", **OWNER)
    raw, old = await first._read(("account", "user", "task"))
    await asyncio.to_thread(
        lambda: asyncio.run(tracker.patch_task("task", TaskPatch(stage="new"), **OWNER))
    )
    stale = {**old, "stage": "stale"}
    assert not await second._commit(
        {("account", "user", "task"): (raw, old)}, {("account", "user", "task"): stale}
    )
    assert (await second.get_task("task", **OWNER)).stage == "new"
    await tracker.complete("task", {}, **OWNER)
    await first.delete_tasks("task", **OWNER)
    await tracker.create("compile", task_id="task", **OWNER)
    assert not await second._commit(
        {("account", "user", "task"): (raw, old)}, {("account", "user", "task"): stale}
    )


@pytest.mark.asyncio
async def test_commit_response_loss_is_visible_without_replay(redis_stores, monkeypatch):
    from redis.exceptions import ConnectionError

    first, second, _ = redis_stores
    original = first._client.eval
    calls = 0

    async def lose_reply(script, *args):
        nonlocal calls
        result = await original(script, *args)
        if script == _COMMIT:
            calls += 1
            raise ConnectionError("synthetic reply loss after execution")
        return result

    monkeypatch.setattr(first._client, "eval", lose_reply)
    with pytest.raises(ConnectionError):
        await first.create(
            TaskRecord(task_id="task", task_type="compile", **OWNER), reject_if_active=True
        )
    assert calls == 1
    assert await second.get_task("task", **OWNER) is not None
    assert [t.task_id for t in await second.list("account", user_id="user")] == ["task"]
    with pytest.raises(TaskAlreadyActive):
        await second.create(
            TaskRecord(task_id="other", task_type="compile", **OWNER), reject_if_active=True
        )
    assert (await second.withdraw_unaccepted("task", **OWNER)).is_applied


@pytest.mark.asyncio
async def test_disconnect_before_commit_has_no_partial_state(redis_stores, monkeypatch):
    from redis.exceptions import ConnectionError

    first, second, _ = redis_stores
    original = first._client.eval

    async def fail_before(script, *args):
        if script == _COMMIT:
            raise ConnectionError("synthetic disconnect before write")
        return await original(script, *args)

    monkeypatch.setattr(first._client, "eval", fail_before)
    with pytest.raises(ConnectionError):
        await first.create(TaskRecord(task_id="task", task_type="compile", **OWNER))
    assert await second.get_task("task", **OWNER) is None
    assert await second.list("account") == []
    assert (
        await second.create(
            TaskRecord(task_id="new", task_type="compile", **OWNER), reject_if_active=True
        )
    ).created


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_retarget_guard_does_not_partially_cancel_work(redis_stores, cancelled):
    first, second, _ = redis_stores
    tracker = TaskTracker(first)
    for task in ("source", "target"):
        await tracker.create("compile", task, task_id=task, reject_if_active=True, **OWNER)
    await tracker.register_work("source", "run", "Semantic", **OWNER)
    await tracker.start_work("source", "run", **OWNER)
    before = await second.get_task_with_events("source", **OWNER)
    with pytest.raises(TaskAlreadyActive):
        await first.record_outcome(
            "source", result={}, error=None, resource_id="target", cancelled=cancelled, **OWNER
        )
    after = await second.get_task_with_events("source", **OWNER)
    assert after.updated_at == before.updated_at
    assert after.resource_id == "source"
    assert await second.get_work("source", "run", **OWNER) == await first.get_work(
        "source", "run", **OWNER
    )
    assert (await second.get_work("source", "run", **OWNER)).state is WorkState.RUNNING
    await tracker.complete("target", {}, **OWNER)
    assert (
        await first.record_outcome(
            "source", result={}, error=None, resource_id="target", cancelled=cancelled, **OWNER
        )
    ).is_applied


@pytest.mark.asyncio
async def test_scope_pagination_deletion_and_version_rejection(redis_stores):
    first, second, params = redis_stores
    other = RedisTaskWorkStore(params=params, service_name="another")
    await other.load()
    try:
        for account in ("account", "other"):
            for i in range(3):
                await first.create(
                    TaskRecord(
                        task_id=str(i),
                        task_type="compile",
                        account_id=account,
                        user_id="user",
                        created_at=float(i + 1),
                    )
                )
        assert await other.list() == []
        rows = await second.list("account", user_id="user", limit=1)
        assert [t.task_id for t in rows] == ["2"]
        rows = await second.list(
            "account", user_id="user", limit=5, before=(rows[0].created_at, rows[0].task_id)
        )
        assert [t.task_id for t in rows] == ["1", "0"]
        assert not (await first.delete_tasks(account_id="account")).accepted
        deleted = await first.delete_tasks(account_id="account", allow_active_types=("compile",))
        assert len(deleted.deleted) == 3
        assert len(await second.list("other")) == 3
        await first._client.hset(first._key, "schema", "2")
        with pytest.raises(Exception, match="schema"):
            await second.list()
        with pytest.raises(Exception, match="schema"):
            await first.load()
        assert await first._client.hlen(first._key) > 1
    finally:
        await other._client.delete(other._key)
        await other.close()


@pytest.mark.asyncio
async def test_close_drains_started_commit_and_cancelled_caller(redis_stores, monkeypatch):
    first, second, _ = redis_stores
    original = first._client.eval
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause(script, *args):
        if script == _COMMIT:
            entered.set()
            await release.wait()
        return await original(script, *args)

    monkeypatch.setattr(first._client, "eval", pause)
    write = asyncio.create_task(
        first.create(TaskRecord(task_id="task", task_type="compile", **OWNER))
    )
    await asyncio.wait_for(entered.wait(), 5)
    write.cancel()
    closing = asyncio.create_task(first.close())
    try:
        for _ in range(3):
            await asyncio.sleep(0)
        assert not closing.done()
        assert not write.done()
    finally:
        release.set()
        await asyncio.gather(write, closing, return_exceptions=True)
    assert write.cancelled()
    assert await second.get_task("task", **OWNER) is not None
    assert [t.task_id for t in await second.list("account")] == ["task"]
    with pytest.raises(RuntimeError, match="not loaded"):
        await first.list()
    await first.load()
    assert await first.get_task("task", **OWNER) is not None


@pytest.mark.asyncio
async def test_failure_cannot_reset_unversioned_or_malformed_state(redis_stores):
    first, second, _ = redis_stores
    await first.create(TaskRecord(task_id="task", task_type="compile", **OWNER))
    field = first._field(("account", "user", "task"))
    await first._client.hset(first._key, field, "{bad-json")
    with pytest.raises(ValueError):
        await second.list_durable("account")
    with pytest.raises(ValueError):
        await second.get_task("task", **OWNER)
    await first._client.hdel(first._key, "schema")
    with pytest.raises(Exception, match="Unversioned"):
        await first.load()
    assert await first._client.hget(first._key, field) == "{bad-json"


@pytest.mark.asyncio
async def test_process_death_after_commit_preserves_discoverability(redis_stores):
    first, second, params = redis_stores
    await _child(params, "crash")
    assert await second.get_task("crashed", **OWNER) is not None
    assert [t.task_id for t in await first.list("account")] == ["crashed"]
    assert (await first.withdraw_unaccepted("crashed", **OWNER)).is_applied
    assert (
        await second.create(
            TaskRecord(task_id="new", task_type="compile", **OWNER), reject_if_active=True
        )
    ).created


@pytest.mark.asyncio
async def test_store_to_public_task_routes_preserves_owner_scope(redis_stores, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from openviking.server.auth import get_request_context
    from openviking.server.identity import RequestContext, Role
    from openviking.server.routers import tasks
    from openviking_cli.session.user_id import UserIdentifier

    first, second, _ = redis_stores
    writer, reader = TaskTracker(first), TaskTracker(second)
    await writer.create("compile", task_id="task", **OWNER)
    await writer.register_work("task", "work", "Semantic", **OWNER)
    monkeypatch.setattr(tasks, "get_task_tracker", lambda: reader)
    app = FastAPI()
    app.include_router(tasks.router)
    ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
    app.dependency_overrides[get_request_context] = lambda: ctx
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/tasks/task?include_events=true")
        assert response.status_code == 200
        assert response.json()["result"]["status"] == "pending"
        ctx = RequestContext(user=UserIdentifier("other", "user"), role=Role.USER)
        response = await client.get("/api/v1/tasks")
        assert response.status_code == 200 and response.json()["result"] == []
        ctx = RequestContext(user=UserIdentifier("account", "user"), role=Role.USER)
        response = await client.post("/api/v1/tasks/task/cancel")
        assert response.status_code == 200
        assert response.json()["result"]["status"] == "cancelled"
    assert (await first.get_work("task", "work", **OWNER)).state is WorkState.CANCELLED
