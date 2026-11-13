"""Studio task pagination and submission retry contracts."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
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
        ("viking://system/compile/global/llm-wiki", True),
        ("viking://system/compile/global/llm-wiki/SKILL.md", True),
        ("viking://system/compile/global", False),
        ("viking://system/compile/global/llm-wiki/references", False),
        ("viking://system/compile/global-other/llm-wiki", False),
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
        with pytest.raises(InvalidArgumentError, match="The input skill is not"):
            service._normalize_request_uris(request, ctx)


@pytest.mark.parametrize(
    "target",
    [
        "viking://system",
        "viking://system/compile",
        "viking://system/compile/example-skills",
        "viking://system/compile/global",
        "viking://system/resources/wiki",
    ],
)
def test_compile_rejects_system_target(target):
    from openviking.service.compile_service import CompileService

    CompileService._validate_target("viking://user/alice/skills")
    CompileService._validate_target("viking://agent/skills")

    with pytest.raises(InvalidArgumentError, match="system namespace"):
        CompileService._validate_target(target)


@pytest.mark.parametrize(
    "source",
    [
        "viking://system/compile/example-skills/llm-wiki",
        "viking://system/compile/global/llm-wiki",
        "viking://system/memory/memory_templates/custom/qa/input",
    ],
)
def test_compile_rejects_system_source(source):
    from openviking.service.compile_service import CompileRequest, CompileService
    from openviking_cli.utils.config.open_viking_config import CompileApiConfig

    service = CompileService(CompileApiConfig(), ExternalTaskService(), SimpleNamespace())
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ADMIN)
    request = CompileRequest.model_validate(
        {
            "from": [source],
            "to": "viking://resources/wiki",
            "skill": "viking://system/compile/example-skills/llm-wiki",
        }
    )

    with pytest.raises(InvalidArgumentError, match="Compile source.*system namespace"):
        service._normalize_request_uris(request, ctx)


async def test_compile_api_rejects_system_target_without_creating_task(service, app, client):
    from openviking.server.auth import get_request_context
    from openviking.service.task_tracker import get_task_tracker

    ctx = RequestContext(user=service.user, role=Role.USER, api_key="test-compile-key")
    app.dependency_overrides[get_request_context] = lambda: ctx
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    tracker = get_task_tracker()
    before = {
        task.task_id for task in await tracker.list_tasks(task_type="compile", limit=None, **owner)
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
        task.task_id for task in await tracker.list_tasks(task_type="compile", limit=None, **owner)
    }
    assert after == before


@pytest.mark.parametrize(
    "source",
    [
        "viking://system/compile/example-skills/llm-wiki",
        "viking://system/memory/memory_templates/custom/qa/input",
    ],
)
async def test_compile_api_rejects_system_source_without_creating_task(
    source, service, app, client
):
    from openviking.server.auth import get_request_context
    from openviking.service.task_tracker import get_task_tracker

    ctx = RequestContext(user=service.user, role=Role.ADMIN, api_key="test-compile-key")
    app.dependency_overrides[get_request_context] = lambda: ctx
    owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
    tracker = get_task_tracker()
    before = {
        task.task_id for task in await tracker.list_tasks(task_type="compile", limit=None, **owner)
    }

    response = await client.post(
        "/api/v1/compile",
        json={
            "from": [source],
            "to": "viking://resources/wiki",
            "skill": "viking://system/compile/example-skills/llm-wiki",
        },
    )

    assert response.status_code == 400, response.text
    assert "Compile source must not be in the system namespace" in response.text
    after = {
        task.task_id for task in await tracker.list_tasks(task_type="compile", limit=None, **owner)
    }
    assert after == before


@pytest.mark.parametrize("mode", ["memory", "recovery"])
async def test_compile_bypasses_admission_for_memory_and_submission_recovery(mode):
    from openviking.service.compile_service import CompileRequest, CompileService
    from openviking_cli.utils.config.open_viking_config import CompileApiConfig

    result = object()
    tasks = SimpleNamespace(recover_submission=AsyncMock(return_value=result), create=AsyncMock())
    fs = SimpleNamespace(ensure_compile_target=AsyncMock())
    service = CompileService(CompileApiConfig(), tasks, fs)
    service._memory_runner = SimpleNamespace(create=AsyncMock(return_value=result))
    request = CompileRequest(to="viking://~/memories/experiences", skill="memory")
    if mode == "recovery":
        request.skill = "viking://system/compile/example-skills/llm-wiki"
        request.from_ = ["viking://resources/source"]
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    assert await service.create(request, connection={}, ctx=ctx, idempotency_key="retry") is result
    tasks.create.assert_not_awaited()
    fs.ensure_compile_target.assert_not_awaited()


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


@pytest.mark.parametrize(
    ("stage", "auth", "waiting_config"),
    [
        ("polling", {"external_task_id": "submitted"}, {}),
        ("queued", {"compile_execution": {"phase": "planning"}}, {"compile_name": "wiki-other"}),
    ],
)
async def test_concurrent_submission_retry_and_restart(monkeypatch, stage, auth, waiting_config):
    import ragfs_python

    import openviking.service.external_task_service as module
    from openviking.service.compile_service import CompileService
    from openviking.service.external_task_service import ExternalTaskSnapshot
    from openviking.storage.queuefs.queue_manager import QueueManager
    from openviking_cli.utils.config.open_viking_config import CompileApiConfig

    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    monkeypatch.setattr(module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    transport = ragfs_python.RAGFSBindingClient()
    transport.mount("queuefs", "/queue", {"backend": "memory"})
    manager = QueueManager(transport)
    manager.get_queue(manager.EXTERNAL_TASK, allow_create=True)
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)

    service = ExternalTaskService()
    provider = CompileService(
        CompileApiConfig(base_url="http://runtime"), service, SimpleNamespace()
    )
    provider.submit = AsyncMock(return_value="external-task")
    provider.get = AsyncMock(
        return_value=ExternalTaskSnapshot(status="completed", result={"done": True})
    )
    provider.on_start = AsyncMock(return_value=None)
    provider.on_finish = AsyncMock(side_effect=lambda task, snapshot: snapshot)
    service.register(provider)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    params = {
        "resource_id": None,
        "payload": {
            "from": ["viking://resources/a"],
            "to": "viking://resources/b",
            "compile": {"compile_name": "wiki"},
        },
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
        waiting_payload = {**params["payload"], "compile": waiting_config}
        waiting = await service.create(
            "compile", **{**params, "ctx": other, "payload": waiting_payload}
        )
        assert waiting.task_id != again.task_id

        owner = {"account_id": ctx.account_id, "user_id": ctx.user.user_id}
        await tracker.start(again.task_id, stage=stage, **owner)
        await tracker.patch_task(again.task_id, TaskPatch(auth=auth), **owner)
        await tracker.start(
            waiting.task_id, stage="queued", account_id=other.account_id, user_id="bob"
        )
        tracker = TaskTracker(PersistentTaskStore(agfs))
        service = ExternalTaskService()
        service.register(provider)
        await manager.prepare_task_tracking(tracker)
        recovered = await manager.get_queued_tasks(tracker, manager.EXTERNAL_TASK)
        assert {task.task_id for task in recovered} == {again.task_id, waiting.task_id}
        await service.restore_tasks(recovered)

        assert not await service.execute(waiting.task_id, other.account_id, other.user.user_id)
        provider.on_start.side_effect = ["busy", None, None]
        assert not await service.execute(again.task_id, **owner)
        assert not await service.execute(waiting.task_id, other.account_id, other.user.user_id)
        provider.submit.assert_not_awaited()
        assert await service.execute(again.task_id, ctx.account_id, ctx.user.user_id)
        assert await service.execute(waiting.task_id, other.account_id, other.user.user_id)
        assert (await tracker.get(waiting.task_id, account_id="acme", user_id="bob")).result == {
            "done": True
        }
    finally:
        transport.close()


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
