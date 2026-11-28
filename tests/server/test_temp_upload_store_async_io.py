# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Event-loop safety and cleanup-throttle tests for temporary HTTP uploads."""

import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server import temp_upload_store
from openviking.server.config import ServerConfig, TempUploadConfig
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.watch_fixtures import local_viking_fs


@pytest.fixture(autouse=True)
def no_active_pack_uploads(monkeypatch):
    @asynccontextmanager
    async def lock_upload_dir(uri, ctx):
        yield None

    async def task_uses_shared_dir(uri, ctx):
        return False

    store = temp_upload_store.TempUploadStore
    monkeypatch.setattr(store, "_lock_shared_upload_dir", staticmethod(lock_upload_dir))
    monkeypatch.setattr(store, "_task_uses_shared_dir", staticmethod(task_uses_shared_dir))


class _UploadFile:
    filename = "upload.md"

    def __init__(self, chunks: list[bytes]):
        self._chunks = iter(chunks)

    async def read(self, _size: int) -> bytes:
        return next(self._chunks, b"")


def _make_upload_id(created_at: float) -> str:
    """Build a valid shared upload id whose embedded timestamp is created_at."""
    return f"{int(created_at * 1000):013d}-{uuid.uuid4().hex}"


def _bucket_entry(bucket: str) -> dict:
    """A top-level YYYYMMDDHH bucket directory entry."""
    return {"isDir": True, "uri": f"{temp_upload_store._SHARED_UPLOAD_ROOT}/{bucket}"}


def _bucket_for(created_at: float) -> str:
    return time.strftime("%Y%m%d%H", time.gmtime(created_at))


def _flat_entry(created_at: float) -> dict:
    """A legacy flat <ms-ts>-<uuid> upload directory entry (pre-bucket)."""
    upload_id = _make_upload_id(created_at)
    return {"isDir": True, "uri": f"{temp_upload_store._SHARED_UPLOAD_ROOT}/{upload_id}"}


def _reset_state() -> None:
    temp_upload_store._SHARED_CLEANUP_DUE_AT.clear()
    temp_upload_store._SHARED_CLEANUP_PENDING.clear()
    while not temp_upload_store._SHARED_CLEANUP_QUEUE.empty():
        temp_upload_store._SHARED_CLEANUP_QUEUE.get_nowait()
        temp_upload_store._SHARED_CLEANUP_QUEUE.task_done()


@pytest.mark.asyncio
async def test_save_local_offloads_cleanup_writes_and_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    calls: list[str] = []
    real_to_thread = temp_upload_store.asyncio.to_thread

    async def recording_to_thread(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", type(func).__name__))
        return await real_to_thread(func, *args, **kwargs)

    config = SimpleNamespace(
        storage=SimpleNamespace(get_upload_temp_dir=lambda: tmp_path),
        temp_upload=SimpleNamespace(ttl_seconds=3600, shared_max_size_bytes=1024),
    )
    monkeypatch.setattr(temp_upload_store, "get_openviking_config", lambda: config)
    monkeypatch.setattr(temp_upload_store.asyncio, "to_thread", recording_to_thread)

    store = temp_upload_store.TempUploadStore(config)
    temp_file_id = await store.save_upload(_UploadFile([b"hello ", b"world"]), "local", object())

    assert (tmp_path / temp_file_id).read_bytes() == b"hello world"
    assert (tmp_path / f"{temp_file_id}.ov_upload.meta").is_file()
    assert "_cleanup_local_temp_files" in calls
    assert "_open_binary_for_write" in calls
    assert "write" in calls
    assert "_write_json" in calls
    assert "_close_file" in calls


@pytest.mark.asyncio
async def test_resolve_local_offloads_filesystem_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    calls: list[str] = []
    real_to_thread = temp_upload_store.asyncio.to_thread

    async def recording_to_thread(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", type(func).__name__))
        return await real_to_thread(func, *args, **kwargs)

    config = SimpleNamespace(storage=SimpleNamespace(get_upload_temp_dir=lambda: tmp_path))
    uploaded = tmp_path / "upload.md"
    uploaded.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(temp_upload_store, "get_openviking_config", lambda: config)
    monkeypatch.setattr(temp_upload_store.asyncio, "to_thread", recording_to_thread)

    store = temp_upload_store.TempUploadStore(SimpleNamespace(temp_upload=SimpleNamespace()))
    resolved = await store.resolve_for_consume("upload.md", object())

    assert resolved.local_path == str(uploaded)
    assert "_resolve_local" in calls


def test_schedule_deduplicates_and_waits_until_the_next_expiry(monkeypatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=12 * 60 * 60))
    )
    monkeypatch.setattr(store, "_ensure_shared_cleanup_worker", lambda: None)
    _reset_state()
    due = 1_800_000_000.0
    current = due - 1
    monkeypatch.setattr(temp_upload_store.time, "time", lambda: current)
    account_a = SimpleNamespace(account_id="account-a")
    account_b = SimpleNamespace(account_id="account-b")
    temp_upload_store._SHARED_CLEANUP_DUE_AT["account-a"] = due

    store._schedule_shared_cleanup(account_a)
    assert temp_upload_store._SHARED_CLEANUP_QUEUE.empty()
    store._schedule_shared_cleanup(account_b)
    store._schedule_shared_cleanup(account_b)
    assert temp_upload_store._SHARED_CLEANUP_QUEUE.qsize() == 1

    current = due
    store._schedule_shared_cleanup(account_a)
    store._schedule_shared_cleanup(account_a)
    queue = temp_upload_store._SHARED_CLEANUP_QUEUE
    try:
        assert queue.qsize() == 2
        assert temp_upload_store._SHARED_CLEANUP_PENDING == {"account-a", "account-b"}
        assert [queue.get_nowait()[2], queue.get_nowait()[2]] == ["account-b", "account-a"]
        queue.task_done()
        queue.task_done()
    finally:
        _reset_state()


def test_schedule_releases_pending_when_queue_is_full(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=12 * 60 * 60))
    )
    monkeypatch.setattr(store, "_ensure_shared_cleanup_worker", lambda: None)
    monkeypatch.setattr(
        temp_upload_store,
        "_SHARED_CLEANUP_QUEUE",
        temp_upload_store.Queue(maxsize=1),
    )
    temp_upload_store._SHARED_CLEANUP_DUE_AT.clear()
    temp_upload_store._SHARED_CLEANUP_PENDING.clear()
    temp_upload_store._SHARED_CLEANUP_QUEUE.put_nowait((store, SimpleNamespace(), "occupied"))

    store._schedule_shared_cleanup(SimpleNamespace(account_id="account-a"))

    assert "account-a" not in temp_upload_store._SHARED_CLEANUP_PENDING
    queue = temp_upload_store._SHARED_CLEANUP_QUEUE
    assert queue.get_nowait()[2] == "occupied"
    queue.task_done()
    try:
        store._schedule_shared_cleanup(SimpleNamespace(account_id="account-a"))
        assert queue.get_nowait()[2] == "account-a"
        queue.task_done()
    finally:
        _reset_state()


@pytest.mark.asyncio
async def test_cleanup_stops_at_first_live_upload(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600))
    )
    _reset_state()
    now = time.time()
    expired_a = _flat_entry(now - 7200)
    expired_b = _flat_entry(now - 5400)
    live_created = now - 60
    live = _flat_entry(live_created)

    removed: list[str] = []
    list_calls: list = []

    class _FakeVfs:
        async def ls(
            self,
            uri,
            show_all_hidden=False,
            node_limit=None,
            sort_by=None,
            sort_order="asc",
            ctx=None,
        ):
            list_calls.append((sort_by, sort_order))
            return [expired_a, expired_b, live]

        async def remove_files(
            self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True
        ):
            assert auto_pathlock is False
            removed.append(uri)

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, next_due = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    # Listing is requested name-ascending (oldest-first).
    assert list_calls == [("name", "asc")]
    assert removed == [expired_a["uri"], expired_b["uri"]]
    assert removed_count == 2
    assert scanned == 3
    assert listed == 3
    # due_at is the live upload's expiry, and it is stored on the account.
    assert next_due == pytest.approx(live_created + 3600)
    assert temp_upload_store._SHARED_CLEANUP_DUE_AT["account-a"] == pytest.approx(
        live_created + 3600
    )


@pytest.mark.asyncio
async def test_cleanup_full_page_expired_clears_due_at(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600))
    )
    _reset_state()
    now = time.time()
    entries = [_flat_entry(now - 7200), _flat_entry(now - 5400)]

    removed: list[str] = []

    class _FakeVfs:
        async def ls(
            self,
            uri,
            show_all_hidden=False,
            node_limit=None,
            sort_by=None,
            sort_order="asc",
            ctx=None,
        ):
            return entries

        async def remove_files(
            self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True
        ):
            removed.append(uri)

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, next_due = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    assert removed_count == 2
    assert listed == 2
    # Everything expired: due_at cleared for a fresh listing next time.
    assert next_due is None
    assert "account-a" not in temp_upload_store._SHARED_CLEANUP_DUE_AT


@pytest.mark.asyncio
async def test_cleanup_remove_failure_clears_due_at_and_stops(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600))
    )
    _reset_state()
    now = time.time()
    first = _flat_entry(now - 7200)
    second = _flat_entry(now - 5400)
    temp_upload_store._SHARED_CLEANUP_DUE_AT["account-a"] = now + 999

    class _FakeVfs:
        async def ls(
            self,
            uri,
            show_all_hidden=False,
            node_limit=None,
            sort_by=None,
            sort_order="asc",
            ctx=None,
        ):
            return [first, second]

        async def remove_files(
            self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True
        ):
            raise RuntimeError("boom")

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, next_due = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    assert removed_count == 0
    assert next_due is None
    assert "account-a" not in temp_upload_store._SHARED_CLEANUP_DUE_AT


@pytest.mark.asyncio
async def test_cleanup_skips_invalid_dirs_by_default(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600))
    )
    _reset_state()
    now = time.time()
    invalid = {"isDir": True, "uri": f"{temp_upload_store._SHARED_UPLOAD_ROOT}/not-an-id"}
    expired = _flat_entry(now - 7200)

    removed: list[str] = []

    class _FakeVfs:
        async def ls(
            self,
            uri,
            show_all_hidden=False,
            node_limit=None,
            sort_by=None,
            sort_order="asc",
            ctx=None,
        ):
            return [invalid, expired]

        async def remove_files(
            self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True
        ):
            removed.append(uri)

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, _ = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    # Invalid dir is left untouched; only the valid expired upload is removed.
    assert removed == [expired["uri"]]
    assert removed_count == 1
    assert scanned == 1
    assert listed == 2


@pytest.mark.asyncio
async def test_cleanup_removes_invalid_dirs_when_enabled(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600, cleanup_invalid_dirs=True))
    )
    _reset_state()
    now = time.time()
    invalid = {"isDir": True, "uri": f"{temp_upload_store._SHARED_UPLOAD_ROOT}/not-an-id"}
    expired = _flat_entry(now - 7200)
    live_created = now - 60
    live = _flat_entry(live_created)

    removed: list[str] = []

    class _FakeVfs:
        async def ls(
            self,
            uri,
            show_all_hidden=False,
            node_limit=None,
            sort_by=None,
            sort_order="asc",
            ctx=None,
        ):
            return [invalid, expired, live]

        async def remove_files(
            self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True
        ):
            assert auto_pathlock is False
            removed.append(uri)

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, next_due = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    # Invalid dir removed alongside the expired legacy upload; the legacy group
    # stops at the first live upload. Groups are processed independently, so the
    # removal order across groups is not asserted here.
    assert set(removed) == {invalid["uri"], expired["uri"]}
    assert removed_count == 2
    assert scanned == 2
    assert listed == 3
    assert next_due == pytest.approx(live_created + 3600)


@pytest.mark.asyncio
async def test_cleanup_invalid_dir_remove_failure_does_not_stop_scan(
    monkeypatch: pytest.MonkeyPatch,
):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600, cleanup_invalid_dirs=True))
    )
    _reset_state()
    now = time.time()
    invalid = {"isDir": True, "uri": f"{temp_upload_store._SHARED_UPLOAD_ROOT}/not-an-id"}
    expired = _flat_entry(now - 7200)

    removed: list[str] = []

    class _FakeVfs:
        async def ls(
            self,
            uri,
            show_all_hidden=False,
            node_limit=None,
            sort_by=None,
            sort_order="asc",
            ctx=None,
        ):
            return [invalid, expired]

        async def remove_files(
            self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True
        ):
            if uri == invalid["uri"]:
                raise RuntimeError("boom")
            removed.append(uri)

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, next_due = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    # A failed invalid-dir remove is logged but must not abort the expiry scan:
    # the valid expired upload is still removed and due_at is cleared normally.
    assert removed == [expired["uri"]]
    assert removed_count == 1
    assert next_due is None
    assert "account-a" not in temp_upload_store._SHARED_CLEANUP_DUE_AT


@pytest.mark.asyncio
async def test_cleanup_removes_expired_buckets_and_stops_at_live(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600))
    )
    _reset_state()
    now = time.time()
    # Two fully-expired hour buckets and one still-live bucket, oldest-first.
    old_bucket = _bucket_for(now - 3 * 3600)
    older_bucket = _bucket_for(now - 5 * 3600)
    live_bucket = _bucket_for(now)  # current hour: not yet past start+3600+ttl
    entries = [_bucket_entry(older_bucket), _bucket_entry(old_bucket), _bucket_entry(live_bucket)]

    removed: list[str] = []

    class _FakeVfs:
        async def ls(
            self,
            uri,
            show_all_hidden=False,
            node_limit=None,
            sort_by=None,
            sort_order="asc",
            ctx=None,
        ):
            return entries

        async def remove_files(
            self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True
        ):
            assert auto_pathlock is False
            assert recursive is True
            removed.append(uri)

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, next_due = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    # Both expired buckets removed whole; stops at the live bucket.
    assert removed == [_bucket_entry(older_bucket)["uri"], _bucket_entry(old_bucket)["uri"]]
    assert removed_count == 2
    assert listed == 3
    # due_at is the live bucket's expiry (bucket_start + 3600 + ttl).
    live_start = temp_upload_store._shared_bucket_start(live_bucket)
    assert next_due == pytest.approx(live_start + 3600 + 3600)


@pytest.mark.asyncio
async def test_cleanup_buckets_not_starved_by_live_legacy(monkeypatch: pytest.MonkeyPatch):
    # Legacy ids (13-digit prefix) sort before buckets (10-digit prefix), so in
    # a single mixed listing a still-live legacy upload appears first. Bucket and
    # legacy groups are processed independently, so an expired bucket must still
    # be removed even though the legacy group stopped at a live entry.
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600))
    )
    _reset_state()
    now = time.time()
    live_legacy = _flat_entry(now - 60)  # legacy, still valid
    expired_bucket = _bucket_entry(_bucket_for(now - 5 * 3600))  # bucket, expired

    removed: list[str] = []

    class _FakeVfs:
        async def ls(
            self,
            uri,
            show_all_hidden=False,
            node_limit=None,
            sort_by=None,
            sort_order="asc",
            ctx=None,
        ):
            # Emulate name-ascending: legacy (1...) before bucket (2...).
            return [live_legacy, expired_bucket]

        async def remove_files(
            self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True
        ):
            removed.append(uri)

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, next_due = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    # The expired bucket is removed; the live legacy upload is preserved and its
    # expiry becomes due_at (earliest live expiry across groups).
    assert removed == [expired_bucket["uri"]]
    assert removed_count == 1
    assert next_due is not None


@pytest.mark.asyncio
async def test_read_shared_meta_uses_bucket_path_for_new_id(monkeypatch, tmp_path):
    config = ServerConfig(temp_upload=TempUploadConfig(ttl_seconds=0))
    viking_fs = local_viking_fs(tmp_path / "agfs")
    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: viking_fs)
    ctx = RequestContext(user=UserIdentifier("account-a", "alice"), role=Role.ROOT)
    content = b"stored upload"
    upload_id = await temp_upload_store.TempUploadStore(config).save_upload(
        _UploadFile([content]), "shared", ctx
    )
    bucket, leaf = upload_id.removeprefix("shared_").split("-", 1)
    assert len(bucket) == 10 and bucket.isdigit()
    assert len(leaf) == 32
    directory = f"viking://upload/{bucket}/{leaf}"
    assert await viking_fs.read_file_bytes(f"{directory}/content", ctx=ctx) == content
    reader = AsyncMock(wraps=viking_fs.read_file)
    monkeypatch.setattr(viking_fs, "read_file", reader)

    resolved = await temp_upload_store.TempUploadStore(config).resolve_shared_reference(
        upload_id, ctx
    )

    assert resolved.original_filename == "upload.md"
    assert resolved.content_uri == f"{directory}/content"
    reader.assert_awaited_once_with(f"{directory}/meta", ctx=ctx)


@pytest.mark.asyncio
async def test_read_shared_meta_uses_flat_path_for_legacy_id(monkeypatch, tmp_path):
    viking_fs = local_viking_fs(tmp_path / "agfs")
    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: viking_fs)
    ctx = RequestContext(user=UserIdentifier("account-a", "alice"), role=Role.ROOT)
    stored_id = _make_upload_id(time.time())
    upload_id = f"shared_{stored_id}"
    directory = f"viking://upload/{stored_id}"
    content = b"legacy content"
    await viking_fs.write_file_bytes(f"{directory}/content", content, ctx=ctx)
    await viking_fs.write_file(
        f"{directory}/meta",
        json.dumps(
            {
                "version": 1,
                "temp_file_id": upload_id,
                "account": ctx.account_id,
                "user": ctx.user.user_id,
                "storage_uri": f"{directory}/content",
                "original_filename": "legacy.md",
                "file_ext": ".md",
            }
        ),
        ctx=ctx,
    )
    reader = AsyncMock(wraps=viking_fs.read_file)
    monkeypatch.setattr(viking_fs, "read_file", reader)

    resolved = await temp_upload_store.TempUploadStore(ServerConfig()).resolve_for_consume(
        upload_id, ctx
    )
    try:
        assert resolved.original_filename == "legacy.md"
        assert Path(resolved.local_path).read_bytes() == content
        reader.assert_awaited_once_with(f"{directory}/meta", ctx=ctx)
    finally:
        await resolved.cleanup()
