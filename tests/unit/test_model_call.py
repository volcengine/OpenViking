# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import asyncio
import time
from types import SimpleNamespace

import pytest

from openviking.metrics.datasources.model_retry import ModelRetryEventDataSource
from openviking.service.task_work_index import bind_task_context
from openviking.storage.queuefs.embedding_msg import EmbeddingMsg
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.telemetry.context import bind_telemetry_stage, get_current_telemetry_stage
from openviking.utils.model_call import (
    ModelCallError,
    RetryContext,
    current_model_workload,
    current_retry_context,
    delegate_model_call,
    is_model_call_error,
    model_stage,
    model_workload,
    run_model_async,
    run_model_sync,
)
from openviking.utils.model_retry import is_retryable_api_error, retry_async


@pytest.fixture
def events(monkeypatch):
    events = []
    monkeypatch.setattr(
        ModelRetryEventDataSource, "_emit", lambda name, payload: events.append((name, payload))
    )
    monkeypatch.setattr("openviking.utils.model_call.random.uniform", lambda *_: 0)
    return events


@pytest.mark.asyncio
async def test_retry_context_is_shared_by_failover_attempts_and_restored(events):
    observed = []
    deadline = time.time() + 60
    primary_adapter = object()
    backup_adapter = object()

    async def request(error=None):
        observed.append((current_retry_context(), current_retry_context().attempts))
        if error is not None:
            raise error
        return "ok"

    async def primary():
        with delegate_model_call(primary_adapter):
            return await run_model_async(
                lambda: request(RuntimeError("401 Unauthorized")),
                model_type="vlm",
                adapter=primary_adapter,
            )

    async def backup():
        with delegate_model_call(backup_adapter):
            return await run_model_async(request, model_type="vlm", adapter=backup_adapter)

    with model_workload(
        "add_resource",
        stage="parse",
        deadline_at=deadline,
        root_task_id="task-123",
    ):
        assert await run_model_async(primary, alternatives=[backup], model_type="vlm") == "ok"

    context = observed[0][0]
    assert isinstance(context, RetryContext)
    assert observed[1][0] is context
    assert [attempts for _context, attempts in observed] == [1, 2]
    assert (
        context.operation,
        context.workload,
        context.stage,
        context.deadline_at,
        context.root_task_id,
        context.max_attempts,
        context.attempts,
        context.route,
        context.disabled_routes,
    ) == ("add_resource", "offline", "parse", deadline, "task-123", 4, 2, 1, {0})
    assert current_retry_context() is None


def test_nested_model_call_gets_an_independent_retry_context(events):
    observed = []

    def nested():
        observed.append(("nested", current_retry_context()))
        return "nested-ok"

    def outer():
        observed.append(("outer-before", current_retry_context()))
        assert run_model_sync(nested, model_type="embedding") == "nested-ok"
        observed.append(("outer-after", current_retry_context()))
        return "outer-ok"

    with model_workload("add_resource"):
        assert run_model_sync(outer, model_type="vlm") == "outer-ok"

    outer_before = observed[0][1]
    nested_context = observed[1][1]
    outer_after = observed[2][1]
    assert isinstance(outer_before, RetryContext)
    assert isinstance(nested_context, RetryContext)
    assert outer_before is outer_after
    assert nested_context is not outer_before
    assert outer_before.attempts == nested_context.attempts == 1
    assert current_retry_context() is None


def test_retry_context_inherits_bound_root_task(events):
    observed = []

    with bind_task_context("task-from-queue", "account", "user"):
        assert (
            run_model_sync(
                lambda: observed.append(current_retry_context()) or "ok",
                model_type="embedding",
            )
            == "ok"
        )

    assert observed[0].root_task_id == "task-from-queue"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["file_summary", "directory_overview"])
async def test_model_stage_overrides_legacy_stage_without_changing_policy_or_delegation(
    events, stage
):
    adapter = object()
    deadline = time.time() + 60
    scopes = []

    async def request():
        scopes.append(current_model_workload())
        assert get_current_telemetry_stage() == "semantic_execute"
        raise TimeoutError()

    async def backend():
        with delegate_model_call(adapter):
            return await run_model_async(request, model_type="vlm", adapter=adapter)

    with model_workload("add_resource", stage="parse", deadline_at=deadline):
        with model_stage(stage), bind_telemetry_stage("semantic_execute"):
            with pytest.raises(TimeoutError):
                await run_model_async(backend, model_type="vlm")
        assert current_model_workload().stage == "parse"
    assert len(scopes) == 4
    assert all(
        (scope.stage, scope.operation, scope.workload, scope.deadline_at)
        == (stage, "add_resource", "offline", deadline)
        for scope in scopes
    )
    assert all(payload["stage"] == stage for _, payload in events)
    assert len([event for event, _ in events if event == "model_retry.logical_call"]) == 1


@pytest.mark.parametrize(
    "stage", ["archive_summary", "memory_extract", "skill_extract", "working_memory"]
)
def test_session_stages_keep_existing_telemetry_fallback(events, stage):
    with model_workload("session_commit"), bind_telemetry_stage(stage):
        assert run_model_sync(lambda: "ok", model_type="vlm") == "ok"
        assert current_model_workload().stage == get_current_telemetry_stage() == stage
    assert all(payload["stage"] == stage for _, payload in events)


@pytest.mark.asyncio
async def test_model_stage_is_task_local_and_restores_after_cancellation():
    async def inspect(stage):
        with model_stage(stage):
            await asyncio.sleep(0)
            assert current_model_workload().stage == stage
            try:
                with model_stage("working_memory"):
                    raise asyncio.CancelledError()
            except asyncio.CancelledError:
                pass
            return current_model_workload().stage

    with bind_telemetry_stage("semantic_execute"):
        assert await asyncio.gather(inspect("file_summary"), inspect("directory_overview")) == [
            "file_summary",
            "directory_overview",
        ]
        assert current_model_workload().stage == "semantic_execute"


@pytest.mark.asyncio
@pytest.mark.parametrize("offline,expected", [(False, 1), (True, 4)])
async def test_nested_owners_and_workflow_share_one_budget(events, offline, expected):
    sent = 0
    adapter = object()

    async def request():
        nonlocal sent
        sent += 1
        raise TimeoutError()

    async def backend():
        return await run_model_async(request, model_type="vlm", max_retries=9, adapter=adapter)

    async def selected_backend():
        with delegate_model_call(adapter):
            return await backend()

    async def wrapper():
        return await run_model_async(
            selected_backend, alternatives=[selected_backend], model_type="vlm"
        )

    with model_workload("session_commit", workload="offline" if offline else "online"):
        with pytest.raises(TimeoutError):
            await retry_async(wrapper, max_retries=3, base_delay=0)
    assert sent == expected
    assert len([e for e in events if e[0] == "model_retry.logical_call"]) == 1
    assert len([e for e in events if e[0] == "model_retry.attempt"]) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("child_task", [False, True])
async def test_independent_nested_model_call_keeps_its_budget(events, child_task):
    sent = 0

    async def request():
        nonlocal sent
        sent += 1
        raise TimeoutError()

    async def outer_request():
        nested = run_model_async(request, model_type="embedding")
        with pytest.raises(TimeoutError):
            await (asyncio.create_task(nested) if child_task else nested)
        return "ok"

    with model_workload("add_resource"):
        assert await run_model_async(outer_request, model_type="vlm") == "ok"
    assert sent == 4
    assert len([e for e in events if e[0] == "model_retry.logical_call"]) == 2
    assert len([e for e in events if e[0] == "model_retry.attempt"]) == 5


@pytest.mark.asyncio
async def test_delegation_is_adapter_specific_and_does_not_transfer_to_child_tasks(events):
    selected = object()
    independent = object()
    sent = 0

    async def request():
        nonlocal sent
        sent += 1
        raise TimeoutError()

    with model_workload("add_resource"), delegate_model_call(selected):
        with pytest.raises(TimeoutError):
            await run_model_async(request, model_type="vlm", adapter=independent)
        with pytest.raises(TimeoutError):
            await asyncio.create_task(run_model_async(request, model_type="vlm", adapter=selected))
    assert sent == 8
    assert len([e for e in events if e[0] == "model_retry.logical_call"]) == 2


def test_delegation_is_consumed_once_so_nested_calls_to_same_adapter_are_independent(events):
    adapter = object()
    sent = 0

    def request():
        nonlocal sent
        sent += 1
        raise TimeoutError()

    def backend():
        with pytest.raises(TimeoutError):
            run_model_sync(request, model_type="vlm", adapter=adapter)
        return "ok"

    def wrapper():
        with delegate_model_call(adapter):
            return run_model_sync(backend, model_type="vlm", adapter=adapter)

    with model_workload("add_resource"):
        assert run_model_sync(wrapper, model_type="vlm") == "ok"
    assert sent == 4
    assert len([e for e in events if e[0] == "model_retry.logical_call"]) == 2


@pytest.mark.parametrize("error_type", ["read_timeout", "connect_error", "requests_timeout"])
def test_transport_errors_with_empty_messages_are_retried(events, error_type):
    import httpx
    import requests

    cls = {
        "read_timeout": httpx.ReadTimeout,
        "connect_error": httpx.ConnectError,
        "requests_timeout": requests.exceptions.Timeout,
    }[error_type]
    sent = 0

    def request():
        nonlocal sent
        sent += 1
        raise cls("")

    with model_workload("add_resource"), pytest.raises(cls):
        run_model_sync(request, model_type="embedding")
    assert sent == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "401 Unauthorized",
        "403 Forbidden",
        "400 InvalidParameter",
        "content safety",
        "AccountQuotaExceeded",
        "unknown provider failure",
    ],
)
async def test_permanent_errors_never_retry_same_credential(events, message):
    sent = 0

    async def request():
        nonlocal sent
        sent += 1
        raise RuntimeError(message)

    with model_workload("add_resource"):
        with pytest.raises(RuntimeError) as exc:
            await run_model_async(request, model_type="embedding")
    assert sent == 1
    wrapped = RuntimeError("adapter wrapper")
    wrapped.__cause__ = exc.value
    assert is_model_call_error(wrapped)
    assert not is_retryable_api_error(wrapped)


@pytest.mark.asyncio
async def test_auth_route_is_removed_and_transient_route_uses_remaining_budget(events):
    sent = [0, 0]

    async def auth():
        sent[0] += 1
        raise RuntimeError("401 Unauthorized")

    async def transient():
        sent[1] += 1
        raise RuntimeError("429 TooManyRequests")

    with model_workload("add_resource"):
        with pytest.raises(RuntimeError) as exc:
            await run_model_async(auth, alternatives=[transient], model_type="embedding")
    assert sent == [1, 3]
    assert exc.value.model_call_error.reason == "max_attempts"


@pytest.mark.asyncio
async def test_deadline_and_retry_after_do_not_start_an_extra_request(events):
    sent = 0

    async def request():
        nonlocal sent
        sent += 1
        error = RuntimeError("429 TooManyRequests")
        error.response = SimpleNamespace(headers={"retry-after": "30"})
        raise error

    with model_workload("add_resource", deadline_at=time.time() + 5):
        with pytest.raises(RuntimeError) as exc:
            await run_model_async(request, model_type="embedding")
        assert exc.value.model_call_error.reason == "deadline"
    assert sent == 1
    with model_workload("add_resource", deadline_at=time.time() - 1):
        with pytest.raises(ModelCallError, match="deadline"):
            await run_model_async(request, model_type="embedding")
    assert sent == 1


@pytest.mark.parametrize("elapsed", [0.01, 0.02, 0.06])
def test_sync_request_cannot_publish_a_result_after_deadline(events, monkeypatch, elapsed):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr("openviking.utils.model_call.time", SimpleNamespace(time=lambda: clock.now))
    sent = 0

    def request():
        nonlocal sent
        sent += 1
        clock.now += elapsed
        return "ok"

    with model_workload("add_resource", deadline_at=100.02):
        if elapsed < 0.02:
            assert run_model_sync(request, model_type="embedding") == "ok"
        else:
            with pytest.raises(ModelCallError, match="deadline") as caught:
                run_model_sync(request, model_type="embedding")
            assert caught.value.attempts == 1
    assert sent == 1
    expected = "ok" if elapsed < 0.02 else "error"
    assert [p["result"] for e, p in events if e == "model_retry.logical_call"] == [expected]
    assert [p["result"] for e, p in events if e == "model_retry.attempt"] == [expected]


@pytest.mark.asyncio
async def test_async_request_cannot_publish_a_result_after_suppressed_cancellation(events):
    async def request():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            return "late-success"

    with model_workload("add_resource", deadline_at=time.time() + 0.01):
        with pytest.raises(ModelCallError, match="deadline") as caught:
            await run_model_async(request, model_type="embedding")

    assert caught.value.attempts == 1
    assert [p["result"] for e, p in events if e == "model_retry.logical_call"] == ["error"]
    assert [p["result"] for e, p in events if e == "model_retry.attempt"] == ["error"]


@pytest.mark.asyncio
async def test_async_request_cannot_swallow_caller_cancellation(events):
    started = asyncio.Event()

    async def request():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            return "should-not-publish"

    task = asyncio.create_task(run_model_async(request, model_type="embedding"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [p["result"] for e, p in events if e == "model_retry.logical_call"] == ["cancelled"]
    assert [p["result"] for e, p in events if e == "model_retry.attempt"] == ["cancelled"]


@pytest.mark.asyncio
async def test_deadline_does_not_wait_for_cancelled_callback_cleanup(events):
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()

    async def request():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cleanup_started.set()
            await finish_cleanup.wait()
            return "late-success"

    with model_workload("add_resource", deadline_at=time.time() + 0.01):
        task = asyncio.create_task(run_model_async(request, model_type="embedding"))
        await cleanup_started.wait()
        with pytest.raises(ModelCallError, match="deadline"):
            await asyncio.wait_for(task, timeout=0.1)
        finish_cleanup.set()
        await asyncio.sleep(0)

    assert [p["result"] for e, p in events if e == "model_retry.logical_call"] == ["error"]
    assert [p["result"] for e, p in events if e == "model_retry.attempt"] == ["error"]


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_wait_for_callback_cleanup(events):
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()

    async def request():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cleanup_started.set()
            await finish_cleanup.wait()
            return "late-success"

    task = asyncio.create_task(run_model_async(request, model_type="embedding"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.1)
    await cleanup_started.wait()
    finish_cleanup.set()
    await asyncio.sleep(0)

    assert [p["result"] for e, p in events if e == "model_retry.logical_call"] == ["cancelled"]
    assert [p["result"] for e, p in events if e == "model_retry.attempt"] == ["cancelled"]


@pytest.mark.asyncio
async def test_cancellation_during_backoff_has_no_extra_attempt(events, monkeypatch):
    started = asyncio.Event()
    monkeypatch.setattr("openviking.utils.model_call.random.uniform", lambda *_: 30)

    async def request():
        started.set()
        raise TimeoutError()

    with model_workload("session_commit"):
        task = asyncio.create_task(run_model_async(request, model_type="vlm"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len([e for e in events if e[0] == "model_retry.attempt"]) == 1
    assert [p["result"] for e, p in events if e == "model_retry.logical_call"] == ["cancelled"]


@pytest.mark.asyncio
async def test_parallel_online_offline_scopes_do_not_mutate_model_config(events):
    async def call(workload):
        sent = 0

        async def request():
            nonlocal sent
            sent += 1
            raise TimeoutError()

        with model_workload("search", workload=workload):
            with pytest.raises(TimeoutError):
                await run_model_async(request, model_type="vlm")
        return sent

    assert await asyncio.gather(call("online"), call("offline")) == [1, 4]
    assert current_model_workload().workload == "online"


def test_sync_recovery_and_normal_fanout_have_independent_budgets(events):
    sent = 0

    def request():
        nonlocal sent
        sent += 1
        if sent < 3:
            raise TimeoutError()
        return "ok"

    with model_workload("add_resource"):
        assert run_model_sync(request, model_type="vlm") == "ok"
        for _ in range(20):
            assert run_model_sync(lambda: "ok", model_type="vlm") == "ok"
    assert sent == 3
    assert len([e for e in events if e[0] == "model_retry.logical_call"]) == 21


@pytest.mark.parametrize("kind", ["embedding", "semantic"])
def test_message_roundtrip_preserves_operation_and_absolute_deadline(kind):
    cls = EmbeddingMsg if kind == "embedding" else SemanticMsg
    with model_workload("session_commit", deadline_at=12345):
        msg = (
            cls(message="text", context_data={"account_id": "account-a"})
            if kind == "embedding"
            else cls(uri="viking://x", context_type="memory")
        )
    with model_workload("add_resource", deadline_at=99999):
        restored = cls.from_dict(msg.to_dict())
    assert restored.model_operation == "session_commit"
    assert restored.model_deadline_at == 12345
    legacy = msg.to_dict()
    del legacy["model_operation"], legacy["model_deadline_at"]
    assert cls.from_dict(legacy).model_operation == "other"
    assert cls.from_dict(legacy).model_deadline_at is None
