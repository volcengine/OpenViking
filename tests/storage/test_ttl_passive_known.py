"""Known-object passive TTL: no discovery or extra metadata probes."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils.config.ttl_config import TTLConfig, TTLCleanupConfig
from tests.storage.test_transfer_merge_binding import binding_fs, indexed_fs, root_ctx

ROOT = "viking://user/default/memories/events"
OLD = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()


def enable(fs, monkeypatch, *, enabled=True):
    import openviking.config.ttl as module

    fs.runtime_config_manager = object()
    cfg = (
        TTLConfig.model_validate({"global": {"mode": "days", "ttl_days": 30}})
        if enabled
        else TTLConfig()
    )
    monkeypatch.setattr(module, "resolve_loaded_ttl_config", lambda *_: cfg)
    fs.ttl_cleanup = SimpleNamespace(settings=TTLCleanupConfig(enabled=True), on_expired=Mock())
    return fs.ttl_cleanup


@pytest.mark.asyncio
async def test_known_metadata_only(binding_fs, monkeypatch):
    fs = binding_fs
    ctx = root_ctx()
    cleanup = enable(fs, monkeypatch)
    monkeypatch.setattr(fs._async_agfs, "stat", AsyncMock(side_effect=AssertionError("extra stat")))
    monkeypatch.setattr(fs._async_agfs, "read", AsyncMock(side_effect=AssertionError("extra read")))
    assert not await fs._ttl_observe_known(ROOT + "/old.md", None, ctx, b"body")
    assert not await fs._ttl_observe_known(ROOT + "/bad.md", {"modTime": "bad"}, ctx)
    session = "viking://user/default/sessions/old"
    assert not await fs._ttl_observe_known(session + "/messages.jsonl", {"modTime": OLD}, ctx)
    assert not await fs._ttl_observe_known(session, {"isDir": True, "modTime": OLD}, ctx)
    cleanup.on_expired.assert_not_called()
    assert await fs._ttl_observe_known(ROOT + "/old.md", {"modTime": OLD}, ctx)
    cleanup.on_expired.assert_called_once_with(ROOT + "/old.md", ctx)
    cleanup.on_expired.reset_mock()
    assert await fs._ttl_observe_known(
        session + "/.meta.json", None, ctx, json.dumps({"created_at": OLD})
    )
    cleanup.on_expired.assert_called_once_with(session + "/.meta.json", ctx)


@pytest.mark.asyncio
async def test_raw_read_no_extra_stat_and_read_file_reuses_stat(binding_fs, monkeypatch, tmp_path):
    import os

    fs = binding_fs
    ctx = root_ctx()
    uri = ROOT + "/old.md"
    await fs.write_file(uri, "body", ctx=ctx)
    path = next(tmp_path.rglob("old.md"))
    old = datetime.fromisoformat(OLD).timestamp()
    os.utime(path, (old, old))
    stat = AsyncMock(wraps=fs._async_agfs.stat)
    read = AsyncMock(wraps=fs._async_agfs.read)
    monkeypatch.setattr(fs._async_agfs, "stat", stat)
    monkeypatch.setattr(fs._async_agfs, "read", read)
    enable(fs, monkeypatch, enabled=False)
    assert await fs.read(uri, ctx=ctx) == b"body"
    baseline = (stat.await_count, read.await_count)
    stat.reset_mock()
    read.reset_mock()
    cleanup = enable(fs, monkeypatch)
    assert await fs.read(uri, ctx=ctx) == b"body"
    assert (stat.await_count, read.await_count) == baseline
    cleanup.on_expired.assert_not_called()
    stat.reset_mock()
    read.reset_mock()
    with pytest.raises(NotFoundError, match="TTL expired"):
        await fs.read_file(uri, ctx=ctx)
    assert stat.await_count == 1
    assert read.await_count == 1
    cleanup.on_expired.assert_called_once_with(uri, ctx)


@pytest.mark.asyncio
async def test_listing_reuses_existing_page(binding_fs, monkeypatch):
    fs = binding_fs
    ctx = root_ctx()
    cleanup = enable(fs, monkeypatch)
    monkeypatch.setattr(fs._async_agfs, "stat", AsyncMock(side_effect=AssertionError("extra stat")))
    monkeypatch.setattr(fs._async_agfs, "read", AsyncMock(side_effect=AssertionError("extra read")))
    entries = [
        {"uri": ROOT + "/old.md", "modTime": OLD},
        {"uri": ROOT + "/unknown.md"},
        {"uri": ROOT + "/directory", "isDir": True, "modTime": OLD},
        {"uri": ROOT + "/denied.md", "access": "denied"},
    ]
    rows = await fs._finalize_listing_entries(
        entries, "original", 256, [], False, ctx=ctx, include_abstract=False
    )
    assert rows == entries[1:]
    cleanup.on_expired.assert_called_once_with(ROOT + "/old.md", ctx)


@pytest.mark.asyncio
async def test_session_load_does_not_swallow_expiry(binding_fs, monkeypatch):
    from openviking.session.session import Session

    fs = binding_fs
    ctx = root_ctx()
    enable(fs, monkeypatch)
    session = Session.__new__(Session)
    session._loaded = False
    session._viking_fs = fs
    session._session_uri = "viking://user/default/sessions/old"
    session.ctx = ctx
    session.session_id = "old"
    monkeypatch.setattr(
        fs,
        "read_file",
        AsyncMock(side_effect=["", NotFoundError("meta", "file", reason="TTL expired")]),
    )
    with pytest.raises(NotFoundError, match="TTL expired"):
        await session.load()


@pytest.mark.asyncio
async def test_queue_wakes_only_for_known_objects(binding_fs, monkeypatch):
    from openviking.service.ttl_cleanup import TTLCleanup
    import openviking.service.ttl_cleanup as module

    if not hasattr(TTLCleanup, "on_expired"):
        pytest.skip("Passive QueueFS branch only")
    queue = SimpleNamespace(
        name="test", enqueue=AsyncMock(), dequeue_raw=AsyncMock(return_value=None), _ack=AsyncMock()
    )
    worker = TTLCleanup(binding_fs, TTLCleanupConfig(enabled=True), queue=queue)
    monkeypatch.setattr(module, "delete_expired", AsyncMock(return_value=True))
    await worker.start()
    try:
        await asyncio.sleep(0.02)
        assert queue.dequeue_raw.await_count == 1
        await asyncio.sleep(0.25)
        assert queue.dequeue_raw.await_count == 1
        uri = ROOT + "/old.md"
        ctx = root_ctx()
        worker.on_expired(uri, ctx)
        worker.on_expired(uri, ctx)
        await asyncio.sleep(0.03)
        assert queue.enqueue.await_count == 1
        assert queue.enqueue.call_args.args[0]["uri"] == uri
        assert queue.enqueue.call_args.args[0]["kind"] == "delete"
        assert queue.dequeue_raw.await_count == 2
        await asyncio.sleep(0.25)
        assert queue.dequeue_raw.await_count == 2
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_legacy_discover_delivery_does_not_query(binding_fs, monkeypatch):
    from openviking.service.ttl_cleanup import TTLCleanup

    if not hasattr(TTLCleanup, "on_expired"):
        pytest.skip("Passive QueueFS branch only")
    queue = SimpleNamespace(
        name="test",
        dequeue_raw=AsyncMock(return_value={"id": "old", "data": {"kind": "discover"}}),
        _ack=AsyncMock(),
    )
    worker = TTLCleanup(binding_fs, TTLCleanupConfig(enabled=True), queue=queue)
    assert not hasattr(worker, "_discover")
    assert not hasattr(worker, "on_access")
    assert await worker.run_once()
    queue._ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_never_triggers_cleanup(indexed_fs, monkeypatch):
    fs, backend = indexed_fs
    backend.ttl_policy_reader = lambda _: TTLConfig.model_validate({"global": {"mode": "days", "ttl_days": 30}})
    class RejectCleanup:
        def __getattr__(self, name):
            raise AssertionError("Search accessed cleanup: " + name)
    backend.ttl_cleanup = RejectCleanup()
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(backend, "search", search)
    monkeypatch.setattr(backend, "filter", AsyncMock(side_effect=AssertionError("discovery query")))
    await backend.search_in_tenant(root_ctx(), [1, 0, 0, 0], target_directories=[ROOT])
    search.assert_awaited_once()
