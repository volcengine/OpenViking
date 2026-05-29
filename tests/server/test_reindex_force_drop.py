"""Regression tests for the ReindexRequest ``force`` flag.

The bug:
  vector store records whose serialization is corrupted (uint16 length
  overflow in C++ BytesRow → see openviking/storage/vectordb/store/
  bytes_row.py) raise UnicodeDecodeError when read back during a
  dedupe query. The dedupe layer catches that error and returns an
  empty list, so reindex thinks "no record exists" and just upserts
  another row — but the corrupted row is still in the store and any
  later query keeps failing.

  ``force=true`` makes every ``_upsert_context`` first delete the
  existing vector record(s) for that URI via
  ``viking_fs._delete_from_vector_store``, then upsert fresh. This
  evicts any corrupted record that's been hiding behind the swallowed
  decode error.

These tests pin two behaviors:
  1. ``ReindexRequest`` accepts ``force`` and the router forwards it
     all the way down to ``ReindexExecutor.execute``.
  2. With ``force=True`` propagated through ``_run``, every
     ``_upsert_context`` call invokes ``_delete_from_vector_store``
     for that URI before the upsert. With ``force=False`` (default),
     no delete is made — preserving the existing fast path.
"""

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.reindex_executor import (
    ReindexExecutor,
    _ReindexCounters,
    _ReindexRunContext,
)
from openviking_cli.session.user_id import UserIdentifier


def _make_ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="test", user_id="alice", agent_id="default"),
        role=Role.ROOT,
    )


# ─────────────────────────────────────────────────────────────────────
# 1. Router-level: ReindexRequest accepts `force` and forwards it
# ─────────────────────────────────────────────────────────────────────


def test_reindex_request_accepts_force_field():
    from openviking.server.routers.content import ReindexRequest

    body = ReindexRequest(uri="viking://resources/demo", force=True)
    assert body.force is True

    default_body = ReindexRequest(uri="viking://resources/demo")
    assert default_body.force is False, "default must be False to keep current behavior"


@pytest.mark.asyncio
async def test_reindex_router_forwards_force_true(monkeypatch):
    from openviking.server.routers.content import ReindexRequest, reindex

    seen: dict = {}

    class FakeService:
        async def reindex(self, *, uri, mode, wait, force, ctx):
            seen["uri"] = uri
            seen["mode"] = mode
            seen["wait"] = wait
            seen["force"] = force
            return {
                "status": "completed",
                "uri": uri,
                "object_type": "resource",
                "mode": mode,
                "rebuilt_records": 0,
                "scanned_records": 0,
                "unsupported_records": 0,
                "failed_records": 0,
                "duration_ms": 1,
                "warnings": [],
            }

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    request = ReindexRequest(
        uri="viking://resources/demo", mode="vectors_only", wait=True, force=True
    )
    response = await reindex(body=request, ctx=_make_ctx())

    assert response.status == "ok"
    assert seen["force"] is True


@pytest.mark.asyncio
async def test_reindex_router_forwards_force_default_false(monkeypatch):
    from openviking.server.routers.content import ReindexRequest, reindex

    seen: dict = {}

    class FakeService:
        async def reindex(self, *, uri, mode, wait, force, ctx):
            seen["force"] = force
            return {
                "status": "completed",
                "uri": uri,
                "object_type": "resource",
                "mode": mode,
                "rebuilt_records": 0,
                "scanned_records": 0,
                "unsupported_records": 0,
                "failed_records": 0,
                "duration_ms": 1,
                "warnings": [],
            }

    monkeypatch.setattr("openviking.server.routers.content.get_service", lambda: FakeService())
    request = ReindexRequest(uri="viking://resources/demo")  # no force passed
    await reindex(body=request, ctx=_make_ctx())
    assert seen["force"] is False


# ─────────────────────────────────────────────────────────────────────
# 2. Executor-level: force=True triggers pre-delete on each _upsert
# ─────────────────────────────────────────────────────────────────────


class _RecordingVikingFS:
    """VikingFS stub that just records calls to _delete_from_vector_store."""

    def __init__(self):
        self.deleted_uri_batches: list[list[str]] = []

    async def _delete_from_vector_store(self, uris, ctx=None):
        self.deleted_uri_batches.append(list(uris))


def _stub_upsert_callable_dependencies(monkeypatch, vikingdb=object()):
    """Make _upsert_context's heavy enqueue path a no-op so we only
    measure the pre-delete behavior."""

    class _NoopMsg:
        id = "msg-id"
        telemetry_id = ""

    class _NoopConverter:
        @staticmethod
        def from_context(_context):
            return _NoopMsg()

    monkeypatch.setattr(
        "openviking.service.reindex_executor.EmbeddingMsgConverter",
        _NoopConverter,
    )

    class _NoopWaitTracker:
        def register_embedding_root(self, *a, **kw):
            pass

        def mark_embedding_failed(self, *a, **kw):
            pass

    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_request_wait_tracker",
        lambda: _NoopWaitTracker(),
    )

    class _Service:
        viking_fs = None  # set per-test
        vikingdb_manager = type(
            "_DB",
            (),
            {
                "enqueue_embedding_msg": staticmethod(
                    lambda _msg: _async_true()
                ),
            },
        )()

    return _Service


async def _async_true():
    return True


@pytest.mark.asyncio
async def test_upsert_context_with_force_pre_deletes(monkeypatch):
    """When force_drop_corrupt context is set, _upsert_context must
    call viking_fs._delete_from_vector_store([uri]) before enqueuing."""
    from openviking.core.context import ContextLevel
    from openviking.service.reindex_executor import _FORCE_DROP_CORRUPT

    fake_fs = _RecordingVikingFS()
    service_cls = _stub_upsert_callable_dependencies(monkeypatch)
    service_cls.viking_fs = fake_fs
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: service_cls,
    )
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_viking_fs",
        lambda: fake_fs,
    )

    executor = ReindexExecutor()
    token = _FORCE_DROP_CORRUPT.set(True)
    try:
        await executor._upsert_context(
            uri="viking://resources/foo/bar.md",
            parent_uri="viking://resources/foo",
            abstract="abs",
            vector_text="vec",
            is_leaf=True,
            context_type="resource",
            level=ContextLevel.DETAIL,
            ctx=_make_ctx(),
        )
    finally:
        _FORCE_DROP_CORRUPT.reset(token)

    assert fake_fs.deleted_uri_batches == [["viking://resources/foo/bar.md"]], (
        "force=True must call delete_from_vector_store with exactly the URI being upserted"
    )


@pytest.mark.asyncio
async def test_upsert_context_without_force_does_not_pre_delete(monkeypatch):
    """Default (force=False) path must NOT trigger any vector delete."""
    from openviking.core.context import ContextLevel

    fake_fs = _RecordingVikingFS()
    service_cls = _stub_upsert_callable_dependencies(monkeypatch)
    service_cls.viking_fs = fake_fs
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: service_cls,
    )
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_viking_fs",
        lambda: fake_fs,
    )

    executor = ReindexExecutor()
    # Do NOT set _FORCE_DROP_CORRUPT
    await executor._upsert_context(
        uri="viking://resources/foo/bar.md",
        parent_uri="viking://resources/foo",
        abstract="abs",
        vector_text="vec",
        is_leaf=True,
        context_type="resource",
        level=ContextLevel.DETAIL,
        ctx=_make_ctx(),
    )

    assert fake_fs.deleted_uri_batches == [], (
        "force=False (default) must NOT delete any vector record"
    )


@pytest.mark.asyncio
async def test_run_sets_force_drop_contextvar(monkeypatch):
    """_run(force=True) must set _FORCE_DROP_CORRUPT for the duration of
    the run so nested _upsert_context calls see it; the var must be reset
    on exit even when the body raises."""
    from openviking.service.reindex_executor import _FORCE_DROP_CORRUPT

    observed: list[bool] = []

    async def fake_reindex_resource(self, *, uri, mode, run):
        observed.append(_FORCE_DROP_CORRUPT.get())

    monkeypatch.setattr(ReindexExecutor, "_reindex_resource", fake_reindex_resource)

    # Bypass the heavy dependencies _run touches before dispatching
    class _FakeVikingFS:
        def _uri_to_path(self, uri, ctx=None):
            return f"/local/test{uri.removeprefix('viking:/')}"

    class _FakeDB:
        has_queue_manager = True

    class _FakeService:
        viking_fs = _FakeVikingFS()
        vikingdb_manager = _FakeDB()

    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_service",
        lambda: _FakeService(),
    )
    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_lock_manager",
        lambda: object(),
    )

    class _NoopLockHandle:
        pass

    class _NoopLockContext:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return _NoopLockHandle()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "openviking.service.reindex_executor.LockContext",
        _NoopLockContext,
    )

    class _NoopTelemetry:
        telemetry_id = ""

    monkeypatch.setattr(
        "openviking.service.reindex_executor.get_current_telemetry",
        lambda: _NoopTelemetry(),
    )

    pre = _FORCE_DROP_CORRUPT.get()
    await ReindexExecutor()._run(
        uri="viking://resources/foo",
        object_type="resource",
        mode="vectors_only",
        ctx=_make_ctx(),
        force=True,
    )
    post = _FORCE_DROP_CORRUPT.get()

    assert observed == [True], "_reindex_resource must observe force=True inside _run"
    assert pre is False and post is False, "contextvar must be reset after _run returns"
