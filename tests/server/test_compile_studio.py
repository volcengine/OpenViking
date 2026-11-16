"""Studio task pagination and submission retry contracts."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_domain import TaskPatch, WorkState
from openviking.service.task_pagination import decode_cursor, encode_cursor, page_scope
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker
from openviking_cli.exceptions import ConflictError, InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wait_at",
    [
        "admission",
        "finalization",
        "recovered_finalization",
        "start_finalization",
        "finalization_error",
    ],
)
async def test_external_busy_preserves_delivery_and_provider_wait(monkeypatch, tmp_path, wait_at):
    import ragfs_python

    import openviking.service.external_task_service as module
    from openviking.service.external_task_service import ExternalTaskSnapshot
    from openviking.service.task_work_index import extract_task_metadata
    from openviking.storage.queuefs.external_task_processor import ExternalTaskProcessor
    from openviking.storage.queuefs.queue_manager import QueueManager

    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    transport = ragfs_python.RAGFSBindingClient()
    transport.mount(
        "queuefs", "/queue", {"backend": "sqlite", "db_path": str(tmp_path / "queue.sqlite")}
    )
    manager = QueueManager(transport)
    service = ExternalTaskService()
    completed = ExternalTaskSnapshot(status="completed", result={"ok": True})
    provider = SimpleNamespace(
        task_type="compile",
        task_id_prefix="cmp_",
        serialization_key=lambda payload: payload["to"],
        poll_interval_seconds=0.001,
        poll_max_attempts=1,
        submit=AsyncMock(return_value="remote"),
        get=AsyncMock(return_value=completed),
        on_start=AsyncMock(
            side_effect=["busy"] * 3 + [None] if wait_at == "admission" else None,
            return_value=completed if wait_at == "start_finalization" else None,
        ),
        on_finish=AsyncMock(
            side_effect=(
                [ConnectionError("local finalization unavailable")] * 3
                if wait_at == "finalization_error"
                else [ExternalTaskSnapshot(status="running", stage="committing")] * 3
            )
            + [completed]
            if wait_at != "admission"
            else lambda task, snapshot: snapshot
        ),
        on_cancel=AsyncMock(),
        submission_meta=lambda _: {},
    )
    service.register(provider)
    queue = manager.get_queue(
        manager.EXTERNAL_TASK, dequeue_handler=ExternalTaskProcessor(service), allow_create=True
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role(Role.USER))
    owner = {"account_id": "acme", "user_id": "alice"}
    try:
        task = await service.create("compile", resource_id=None, payload={"to": "target"}, ctx=ctx)
        [original] = await queue.snapshot()
        metadata = extract_task_metadata(original)
        assert metadata is not None

        async def wait(delay):
            assert (
                await tracker.get_work_state(task.task_id, metadata.work_id, **owner)
                is WorkState.RUNNING
            )
            assert (await queue.get_status()).in_progress == 1
            assert original in await queue.snapshot()

        sleep = AsyncMock(side_effect=wait)
        monkeypatch.setattr(
            module, "asyncio", SimpleNamespace(sleep=sleep, CancelledError=asyncio.CancelledError)
        )
        provider.poll_interval_seconds = 30.0
        if wait_at == "recovered_finalization":
            await tracker.start(task.task_id, **owner)
            await tracker.patch_task(
                task.task_id,
                TaskPatch(
                    auth={
                        "external_terminal_snapshot": {
                            "status": "completed",
                            "result": {"ok": True},
                        }
                    }
                ),
                **owner,
            )
        waiting = await service.create(
            "compile", resource_id=None, payload={"to": "target"}, ctx=ctx
        )
        requeues = (
            3
            if wait_at == "admission"
            else int(wait_at in {"start_finalization", "finalization_error"})
        )
        for _ in range(requeues):
            await queue.dequeue()
            current = await tracker.get(task.task_id, **owner)
            assert current is not None
            assert current.status not in {TaskStatus.FAILED, TaskStatus.COMPLETED}
            assert (await queue.get_status()).pending == 2
            assert original in await queue.snapshot()
            assert await tracker.has_work(task.task_id, **owner)
            assert (
                await tracker.get_work_state(task.task_id, metadata.work_id, **owner)
                is WorkState.PENDING
            )
        await queue.dequeue()
        current = await tracker.get(task.task_id, **owner)
        assert current is not None and current.status is TaskStatus.COMPLETED
        next_message = await queue.dequeue_raw()
        assert next_message is not None and next_message["id"] != original["id"]
        waiting_task = await tracker.get(waiting.task_id, **owner)
        assert waiting_task is not None and waiting_task.status is TaskStatus.PENDING
        if wait_at in {"recovered_finalization", "start_finalization"}:
            provider.submit.assert_not_awaited()
            provider.get.assert_not_awaited()
        else:
            provider.submit.assert_awaited_once()
            provider.get.assert_awaited_once()
        expected_delays = {
            "admission": [],
            "finalization": [30.0] * 3,
            "recovered_finalization": [30.0] * 3,
            "start_finalization": [30.0] * 3,
            "finalization_error": [0.2, 0.4, 30.0],
        }
        assert [call.args[0] for call in sleep.await_args_list] == expected_delays[wait_at]
        provider.on_cancel.assert_not_awaited()
    finally:
        transport.close()


@pytest.mark.asyncio
async def test_external_cancel_during_finalization_keeps_work_running_until_cleanup(
    monkeypatch, tmp_path
):
    import ragfs_python

    import openviking.service.external_task_service as module
    from openviking.service.external_task_service import (
        ExternalTaskCancellation,
        ExternalTaskSnapshot,
    )
    from openviking.service.task_work_index import extract_task_metadata
    from openviking.storage.queuefs.external_task_processor import ExternalTaskProcessor
    from openviking.storage.queuefs.queue_manager import QueueManager

    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    transport = ragfs_python.RAGFSBindingClient()
    transport.mount(
        "queuefs", "/queue", {"backend": "sqlite", "db_path": str(tmp_path / "queue.sqlite")}
    )
    manager = QueueManager(transport)
    service = ExternalTaskService()
    finalization_waiting = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_allowed = asyncio.Event()

    async def wait(delay):
        assert delay == 30.0
        finalization_waiting.set()
        await asyncio.Event().wait()

    async def finish(task, snapshot):
        if snapshot.status == "cancelled":
            cleanup_started.set()
            await cleanup_allowed.wait()
            return snapshot
        return ExternalTaskSnapshot(status="running", stage="committing")

    monkeypatch.setattr(
        module, "asyncio", SimpleNamespace(sleep=wait, CancelledError=asyncio.CancelledError)
    )
    provider = SimpleNamespace(
        task_type="compile",
        task_id_prefix="cmp_",
        serialization_key=lambda payload: payload["to"],
        poll_interval_seconds=30.0,
        poll_max_attempts=1,
        submit=AsyncMock(return_value="remote"),
        get=AsyncMock(return_value=ExternalTaskSnapshot(status="completed", result={"ok": True})),
        on_start=AsyncMock(return_value=None),
        on_finish=AsyncMock(side_effect=finish),
        on_cancel=AsyncMock(
            return_value=ExternalTaskCancellation(
                external_task_id="remote", snapshot=ExternalTaskSnapshot(status="cancelled")
            )
        ),
        submission_meta=lambda _: {},
    )
    service.register(provider)
    queue = manager.get_queue(
        manager.EXTERNAL_TASK, dequeue_handler=ExternalTaskProcessor(service), allow_create=True
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    owner = {"account_id": "acme", "user_id": "alice"}
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    worker = None
    try:
        task = await service.create("compile", resource_id=None, payload={"to": "target"}, ctx=ctx)
        [original] = await queue.snapshot()
        metadata = extract_task_metadata(original)
        assert metadata is not None
        waiting = await service.create(
            "compile", resource_id=None, payload={"to": "target"}, ctx=ctx
        )
        worker = asyncio.create_task(queue.dequeue())
        await asyncio.wait_for(finalization_waiting.wait(), timeout=2)
        assert not worker.done()
        assert (await tracker.get(task.task_id, **owner)).status is TaskStatus.RUNNING
        await tracker.cancel(task.task_id, **owner)
        await asyncio.wait_for(cleanup_started.wait(), timeout=2)
        assert (await tracker.get(task.task_id, **owner)).status is TaskStatus.CANCELLING
        assert (
            await tracker.get_work_state(task.task_id, metadata.work_id, **owner)
            is WorkState.RUNNING
        )
        assert await tracker.has_work(task.task_id, **owner)
        assert original in await queue.snapshot()
        assert await queue.dequeue_raw() is None
        assert (await tracker.get(waiting.task_id, **owner)).status is TaskStatus.PENDING
        cleanup_allowed.set()
        await asyncio.wait_for(worker, timeout=2)
        assert (await tracker.get(task.task_id, **owner)).status is TaskStatus.CANCELLED
        assert (
            await tracker.get_work_state(task.task_id, metadata.work_id, **owner)
            is WorkState.CANCELLED
        )
        assert not await tracker.has_work(task.task_id, **owner)
        successor = await queue.dequeue_raw()
        assert successor is not None and successor["id"] != original["id"]
        provider.submit.assert_awaited_once()
        provider.get.assert_awaited_once()
        provider.on_cancel.assert_awaited_once()
    finally:
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        transport.close()


@pytest.mark.asyncio
async def test_external_interrupted_cancellation_cleanup_preserves_delivery(monkeypatch, tmp_path):
    import ragfs_python

    from openviking.service.external_task_service import (
        ExternalTaskCancellation,
        ExternalTaskSnapshot,
    )
    from openviking.storage.queuefs.external_task_processor import ExternalTaskProcessor
    from openviking.storage.queuefs.queue_manager import QueueManager

    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    transport = ragfs_python.RAGFSBindingClient()
    transport.mount(
        "queuefs", "/queue", {"backend": "sqlite", "db_path": str(tmp_path / "queue.sqlite")}
    )
    manager = QueueManager(transport)
    service = ExternalTaskService()
    executing = asyncio.Event()
    cleanup_polling = asyncio.Event()
    polls = 0

    async def poll(*args, **kwargs):
        nonlocal polls
        polls += 1
        (executing if polls == 1 else cleanup_polling).set()
        await asyncio.Event().wait()

    provider = SimpleNamespace(
        task_type="compile",
        task_id_prefix="cmp_",
        serialization_key=lambda payload: payload["to"],
        poll_interval_seconds=0.001,
        poll_max_attempts=1,
        submit=AsyncMock(),
        get=poll,
        on_start=AsyncMock(return_value=None),
        on_finish=AsyncMock(side_effect=lambda task, snapshot: snapshot),
        on_cancel=AsyncMock(
            return_value=ExternalTaskCancellation(
                external_task_id="remote", snapshot=ExternalTaskSnapshot(status="running")
            )
        ),
        submission_meta=lambda _: {},
    )
    service.register(provider)
    queue = manager.get_queue(
        manager.EXTERNAL_TASK, dequeue_handler=ExternalTaskProcessor(service), allow_create=True
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    owner = {"account_id": "acme", "user_id": "alice"}
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role(Role.USER))
    try:
        task = await service.create("compile", resource_id=None, payload={"to": "target"}, ctx=ctx)
        await tracker.patch_task(
            task.task_id, TaskPatch(auth={"external_task_id": "remote"}), **owner
        )
        [original] = await queue.snapshot()
        worker = asyncio.create_task(queue.dequeue())
        await asyncio.wait_for(executing.wait(), timeout=2)
        await tracker.cancel(task.task_id, **owner)
        await asyncio.wait_for(cleanup_polling.wait(), timeout=2)
        worker.cancel()
        await asyncio.wait_for(worker, timeout=2)
        assert await queue.snapshot() == [original]
        assert (await queue.get_status()).in_progress == 1
        assert await tracker.has_work(task.task_id, **owner)
        provider.on_finish.assert_not_awaited()
    finally:
        transport.close()


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
    from openviking.storage.queuefs.external_task_processor import ExternalTaskProcessor
    from openviking.storage.queuefs.queue_manager import QueueManager
    from openviking_cli.utils.config.open_viking_config import CompileApiConfig

    agfs = _FakeAgfs()
    store = PersistentTaskStore(agfs)
    tracker = TaskTracker(store)
    # Recovery must not depend on a global tracker left by another test.
    monkeypatch.setattr("openviking.service.task_tracker._instance", None)
    monkeypatch.setattr(module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr("openviking.service.task_tracker.get_task_tracker", lambda: tracker)
    monkeypatch.setattr(
        "openviking.storage.queuefs.external_task_processor.get_task_tracker", lambda: tracker
    )
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
        queue = manager.get_queue(manager.EXTERNAL_TASK)
        queue.set_dequeue_handler(ExternalTaskProcessor(service))
        message = await queue.dequeue_raw()
        assert json.loads(message["data"])["task_id"] == again.task_id
        # Other users cannot dequeue the same target while its original ID is unacked.
        assert await queue.dequeue_raw() is None
        provider.submit.assert_not_awaited()
        await queue.process_dequeued(message)
        await queue.ack(message["id"], message)
        for _ in range(len(await queue.snapshot())):
            await queue.dequeue()
        assert (await tracker.get(waiting.task_id, account_id="acme", user_id="bob")).result == {
            "done": True
        }
    finally:
        transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("save_failures", [2, 3])
async def test_external_outcome_save_does_not_repeat_provider(monkeypatch, tmp_path, save_failures):
    import ragfs_python

    from openviking.service.external_task_service import ExternalTaskSnapshot
    from openviking.storage.queuefs.external_task_processor import ExternalTaskProcessor
    from openviking.storage.queuefs.queue_manager import QueueManager

    agfs = _FakeAgfs()
    tracker = TaskTracker(PersistentTaskStore(agfs))
    monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
    transport = ragfs_python.RAGFSBindingClient()
    queue_config = {"backend": "sqlite", "db_path": str(tmp_path / "queue.sqlite")}
    transport.mount("queuefs", "/queue", queue_config)
    manager = QueueManager(transport)
    service = ExternalTaskService()
    provider = SimpleNamespace(
        task_type="compile",
        task_id_prefix="cmp_",
        serialization_key=lambda payload: payload["to"],
        poll_interval_seconds=0.001,
        poll_max_attempts=1,
        submit=AsyncMock(return_value="remote"),
        get=AsyncMock(return_value=ExternalTaskSnapshot(status="completed", result={"ok": True})),
        on_start=AsyncMock(return_value=None),
        on_finish=AsyncMock(side_effect=lambda task, snapshot: snapshot),
        on_cancel=AsyncMock(),
        submission_meta=lambda _: {},
    )
    service.register(provider)
    queue = manager.get_queue(
        manager.EXTERNAL_TASK, dequeue_handler=ExternalTaskProcessor(service), allow_create=True
    )
    monkeypatch.setattr("openviking.storage.queuefs.get_queue_manager", lambda: manager)
    complete = tracker.complete
    saves = 0

    async def save(*args, **kwargs):
        nonlocal saves
        saves += 1
        if saves <= save_failures:
            raise OSError("store unavailable")
        return await complete(*args, **kwargs)

    monkeypatch.setattr(tracker, "complete", save)
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    try:
        first = await service.create("compile", resource_id=None, payload={"to": "target"}, ctx=ctx)
        [original] = await queue.snapshot()
        await queue.dequeue()
        provider.submit.assert_awaited_once()
        provider.get.assert_awaited_once()
        provider.on_finish.assert_awaited_once()
        provider.on_cancel.assert_not_awaited()
        if save_failures == 3:
            assert (await queue.get_status()).in_progress == 1
            assert await queue.snapshot() == [original]
            assert await tracker.has_work(first.task_id, account_id="acme", user_id="alice")
            waiting = await service.create(
                "compile", resource_id=None, payload={"to": "target"}, ctx=ctx
            )
            assert await queue.dequeue_raw() is None

            # Restart preserves the original delivery and retries its saved outcome.
            tracker = TaskTracker(PersistentTaskStore(agfs))
            monkeypatch.setattr("openviking.service.task_tracker._instance", tracker)
            service = ExternalTaskService()
            service.register(provider)
            queue.set_dequeue_handler(ExternalTaskProcessor(service))
            transport.unmount("/queue")
            transport.mount("queuefs", "/queue", queue_config)
            await manager.prepare_task_tracking(tracker)
            recovered = await queue.dequeue_raw()
            assert recovered["id"] == original["id"]
            await queue.process_dequeued(recovered)
            await queue.ack(recovered["id"], recovered)
            provider.submit.assert_awaited_once()
            provider.get.assert_awaited_once()
            await queue.dequeue()
            assert (
                await tracker.get(waiting.task_id, account_id="acme", user_id="alice")
            ).status is TaskStatus.COMPLETED
        assert (
            await tracker.get(first.task_id, account_id="acme", user_id="alice")
        ).status is TaskStatus.COMPLETED
        assert (await queue.get_status()).is_complete
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
