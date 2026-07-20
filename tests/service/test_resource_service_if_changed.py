from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.resource.source_metadata import build_source_metadata, encode_source_metadata
from openviking.server.identity import RequestContext, Role
from openviking.service import resource_service as resource_service_module
from openviking.service.resource_service import ResourceService
from openviking_cli.session.user_id import UserIdentifier


class FakeLock:
    def __init__(self):
        self.active = True
        self.closed = False
        self.handed_off = False

    async def close(self):
        self.active = False
        self.closed = True

    def to_handoff(self):
        return None

    async def handoff(self):
        self.active = False
        self.handed_off = True


class FakeBackgroundTask:
    def add_done_callback(self, _callback):
        pass


class FakeVikingFS:
    def __init__(self, source_metadata=None):
        self.source_metadata = source_metadata

    def _uri_to_path(self, uri, ctx=None):
        return f"/fake/{uri}"

    async def read_file(self, uri, ctx=None):
        if self.source_metadata is None:
            raise FileNotFoundError(uri)
        return encode_source_metadata(self.source_metadata)


class FakeResourceProcessor:
    def __init__(self):
        self.calls = []
        self.lock = FakeLock()

    async def acquire_resource_lock(self, *_args, **_kwargs):
        return self.lock

    async def process_resource(self, **kwargs):
        self.calls.append(kwargs)
        result = {"status": "success", "root_uri": kwargs["to"]}
        if kwargs.get("defer_post_processing"):
            result.update({"_post_process": {}, "_resource_lock": self.lock})
        return result


def _fingerprint(sha="a" * 64):
    return {
        "source_kind": "temp_upload",
        "source_sha256": sha,
        "source_size": 12,
    }


def _ctx():
    return RequestContext(
        user=UserIdentifier("test_account", "test_user"),
        role=Role.USER,
    )


def _service(metadata=None):
    processor = FakeResourceProcessor()
    service = ResourceService(
        vikingdb=object(),
        viking_fs=FakeVikingFS(metadata),
        resource_processor=processor,
        skill_processor=object(),
    )
    service._should_use_connector = lambda *_args, **_kwargs: False
    service._should_use_understanding_api = lambda *_args, **_kwargs: False
    return service, processor


@pytest.fixture(autouse=True)
def avoid_git_and_queue_paths(monkeypatch):
    monkeypatch.setattr(resource_service_module, "is_git_repo_url", lambda _path: False)
    monkeypatch.setattr("openviking.storage.transaction.get_lock_manager", lambda: object())
    monkeypatch.setattr(
        resource_service_module,
        "get_current_telemetry",
        lambda: SimpleNamespace(
            measure=lambda *_a, **_k: nullcontext(),
            set=lambda *_a, **_k: None,
            set_error=lambda *_a, **_k: None,
        ),
    )
    monkeypatch.setattr(
        "openviking.service.task_tracker.get_task_tracker",
        lambda: SimpleNamespace(
            create=_async_return(SimpleNamespace(task_id="task-1")),
        ),
    )

    def discard_background(coro):
        coro.close()
        return FakeBackgroundTask()

    monkeypatch.setattr(resource_service_module.asyncio, "create_task", discard_background)


def _async_return(value):
    async def _return(*_args, **_kwargs):
        return value

    return _return


@pytest.mark.asyncio
async def test_if_changed_returns_noop_under_target_lock():
    existing = build_source_metadata(_fingerprint())
    service, processor = _service(existing)

    result = await service.add_resource(
        path="/safe/upload.md",
        ctx=_ctx(),
        to="viking://resources/stable",
        allow_local_path_resolution=True,
        if_changed=True,
        source_fingerprint=_fingerprint(),
        build_index=False,
    )

    assert result == {
        "status": "no-op",
        "reason": "source_sha256_match",
        "root_uri": "viking://resources/stable",
        "source_sha256": "a" * 64,
        "target_revision": existing["target_revision"],
    }
    assert processor.calls == []
    assert processor.lock.closed


@pytest.mark.asyncio
async def test_if_changed_noop_preserves_zero_interval_watch_cancellation():
    existing = build_source_metadata(_fingerprint())
    service, processor = _service(existing)
    service._get_watch_manager = lambda: object()
    service._handle_watch_task_cancellation = AsyncMock()
    ctx = _ctx()

    result = await service.add_resource(
        path="/safe/upload.md",
        ctx=ctx,
        to="viking://resources/stable",
        allow_local_path_resolution=True,
        if_changed=True,
        source_fingerprint=_fingerprint(),
        build_index=False,
    )

    assert result["status"] == "no-op"
    service._handle_watch_task_cancellation.assert_awaited_once_with(
        to_uri="viking://resources/stable",
        ctx=ctx,
    )
    assert processor.calls == []
    assert processor.lock.closed


@pytest.mark.asyncio
async def test_if_changed_changed_source_keeps_lock_through_processing():
    service, processor = _service(build_source_metadata(_fingerprint()))

    async def enqueue_job(_msg, *, resource_lock):
        assert resource_lock is processor.lock
        assert resource_lock.active
        await resource_lock.handoff()
        return SimpleNamespace(task_id="task-1")

    service._enqueue_add_resource_job = enqueue_job

    result = await service.add_resource(
        path="/safe/upload.md",
        ctx=_ctx(),
        to="viking://resources/changed",
        allow_local_path_resolution=True,
        if_changed=True,
        source_fingerprint=_fingerprint("b" * 64),
        build_index=False,
    )

    assert result["status"] == "success"
    assert result["task_id"] == "task-1"
    assert processor.calls[0]["resource_lock"] is processor.lock
    assert processor.calls[0]["source_fingerprint"]["source_sha256"] == "b" * 64
    assert processor.calls[0]["defer_post_processing"] is True
    assert processor.lock.handed_off
    assert not processor.lock.closed
