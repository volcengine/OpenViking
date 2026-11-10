# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Event-loop safety and cleanup-throttle tests for temporary HTTP uploads."""

import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.server import temp_upload_store


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


# Backwards-compatible alias used by older tests below.
def _upload_entry(created_at: float) -> dict:
    return _flat_entry(created_at)


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
    assert "_create_temp_file" in calls
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


def test_schedule_deduplicates_while_pending(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=12 * 60 * 60))
    )
    monkeypatch.setattr(store, "_ensure_shared_cleanup_worker", lambda: None)
    _reset_state()

    store._schedule_shared_cleanup(SimpleNamespace(account_id="account-a"))
    store._schedule_shared_cleanup(SimpleNamespace(account_id="account-a"))

    assert temp_upload_store._SHARED_CLEANUP_QUEUE.qsize() == 1
    assert "account-a" in temp_upload_store._SHARED_CLEANUP_PENDING


def test_schedule_skips_when_due_at_in_future(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=12 * 60 * 60))
    )
    monkeypatch.setattr(store, "_ensure_shared_cleanup_worker", lambda: None)
    _reset_state()
    # due_at far in the future: nothing new can have expired, so skip.
    temp_upload_store._SHARED_CLEANUP_DUE_AT["account-a"] = time.time() + 3600

    store._schedule_shared_cleanup(SimpleNamespace(account_id="account-a"))

    assert temp_upload_store._SHARED_CLEANUP_QUEUE.qsize() == 0


def test_schedule_submits_when_due_at_elapsed(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=12 * 60 * 60))
    )
    monkeypatch.setattr(store, "_ensure_shared_cleanup_worker", lambda: None)
    _reset_state()
    temp_upload_store._SHARED_CLEANUP_DUE_AT["account-a"] = time.time() - 1

    store._schedule_shared_cleanup(SimpleNamespace(account_id="account-a"))

    assert temp_upload_store._SHARED_CLEANUP_QUEUE.qsize() == 1


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


@pytest.mark.asyncio
async def test_cleanup_stops_at_first_live_upload(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600))
    )
    _reset_state()
    now = time.time()
    expired_a = _upload_entry(now - 7200)
    expired_b = _upload_entry(now - 5400)
    live_created = now - 60
    live = _upload_entry(live_created)

    removed: list[str] = []
    list_calls: list = []

    class _FakeVfs:
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            list_calls.append((sort_by, sort_order))
            return [expired_a, expired_b, live]

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
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
    entries = [_upload_entry(now - 7200), _upload_entry(now - 5400)]

    removed: list[str] = []

    class _FakeVfs:
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            return entries

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
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
    first = _upload_entry(now - 7200)
    second = _upload_entry(now - 5400)
    temp_upload_store._SHARED_CLEANUP_DUE_AT["account-a"] = now + 999

    removed: list[str] = []

    class _FakeVfs:
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            return [first, second]

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
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
    expired = _upload_entry(now - 7200)

    removed: list[str] = []

    class _FakeVfs:
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            return [invalid, expired]

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
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
        SimpleNamespace(
            temp_upload=SimpleNamespace(ttl_seconds=3600, cleanup_invalid_dirs=True)
        )
    )
    _reset_state()
    now = time.time()
    invalid = {"isDir": True, "uri": f"{temp_upload_store._SHARED_UPLOAD_ROOT}/not-an-id"}
    expired = _upload_entry(now - 7200)
    live_created = now - 60
    live = _upload_entry(live_created)

    removed: list[str] = []

    class _FakeVfs:
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            return [invalid, expired, live]

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
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
        SimpleNamespace(
            temp_upload=SimpleNamespace(ttl_seconds=3600, cleanup_invalid_dirs=True)
        )
    )
    _reset_state()
    now = time.time()
    invalid = {"isDir": True, "uri": f"{temp_upload_store._SHARED_UPLOAD_ROOT}/not-an-id"}
    expired = _upload_entry(now - 7200)

    removed: list[str] = []

    class _FakeVfs:
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            return [invalid, expired]

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
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
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            return entries

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
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
async def test_cleanup_removes_expired_legacy_flat_uploads(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(temp_upload=SimpleNamespace(ttl_seconds=3600))
    )
    _reset_state()
    now = time.time()
    expired_flat = _flat_entry(now - 7200)
    live_flat = _flat_entry(now - 60)

    removed: list[str] = []

    class _FakeVfs:
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            return [expired_flat, live_flat]

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
            removed.append(uri)

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, next_due = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    # Legacy flat uploads are still reclaimed by their own creation-time TTL.
    assert removed == [expired_flat["uri"]]
    assert removed_count == 1


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
    live_legacy = _flat_entry(now - 60)                 # legacy, still valid
    expired_bucket = _bucket_entry(_bucket_for(now - 5 * 3600))  # bucket, expired

    removed: list[str] = []

    class _FakeVfs:
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            # Emulate name-ascending: legacy (1...) before bucket (2...).
            return [live_legacy, expired_bucket]

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
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
async def test_cleanup_never_treats_legacy_flat_as_invalid(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(
        SimpleNamespace(
            temp_upload=SimpleNamespace(ttl_seconds=3600, cleanup_invalid_dirs=True)
        )
    )
    _reset_state()
    now = time.time()
    invalid = {"isDir": True, "uri": f"{temp_upload_store._SHARED_UPLOAD_ROOT}/not-an-id"}
    live_flat = _flat_entry(now - 60)  # valid legacy id, not yet expired

    removed: list[str] = []

    class _FakeVfs:
        async def ls(self, uri, show_all_hidden=False, node_limit=None,
                     sort_by=None, sort_order="asc", ctx=None):
            return [invalid, live_flat]

        async def remove_files(self, uri, recursive=False, ctx=None, auto_pathlock=True):
            removed.append(uri)

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    listed, scanned, removed_count, next_due = await store._cleanup_shared_uploads(
        SimpleNamespace(account_id="account-a", user=SimpleNamespace())
    )

    # Only the invalid dir is removed; the live legacy upload is preserved and
    # sets due_at (it is a valid, unexpired upload), never deleted as invalid.
    assert removed == [invalid["uri"]]
    assert removed_count == 1
    assert next_due is not None


def test_new_upload_id_is_bucketed_and_legacy_is_not():
    # New id: 10-digit hour prefix -> bucketed.
    new_id = temp_upload_store._new_shared_upload_id()
    prefix = new_id.split("-", 1)[0]
    assert len(prefix) == 10
    assert temp_upload_store._shared_bucket_from_upload_id(new_id) == prefix
    # A new id has no legacy creation timestamp.
    assert temp_upload_store._shared_upload_created_at(new_id) is None

    # Legacy id: 13-digit ms prefix -> not bucketed, but has a creation time.
    legacy_id = _make_upload_id(time.time())
    assert temp_upload_store._shared_bucket_from_upload_id(legacy_id) is None
    assert temp_upload_store._shared_upload_created_at(legacy_id) is not None


@pytest.mark.asyncio
async def test_read_shared_meta_uses_bucket_path_for_new_id(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(SimpleNamespace(temp_upload=SimpleNamespace()))
    new_id = temp_upload_store._new_shared_upload_id()
    bucket, leaf = temp_upload_store._split_shared_upload_id(new_id)
    expected_uri = temp_upload_store._shared_meta_uri(bucket, leaf)

    read_uris: list[str] = []

    class _FakeVfs:
        async def read_file(self, uri, ctx=None):
            read_uris.append(uri)
            return '{"temp_file_id": "shared_%s"}' % new_id

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    meta = await store._read_shared_meta(
        new_id, SimpleNamespace(account_id="a", user=SimpleNamespace())
    )
    # Exactly one read, against the bucketed path (no legacy fallback probe).
    assert read_uris == [expected_uri]
    # In-bucket leaf is the uuid only, without repeating the bucket prefix.
    assert expected_uri == f"{temp_upload_store._SHARED_UPLOAD_ROOT}/{bucket}/{leaf}/meta"
    assert leaf == new_id.split("-", 1)[1]
    assert bucket not in leaf
    assert meta["temp_file_id"] == f"shared_{new_id}"


@pytest.mark.asyncio
async def test_read_shared_meta_uses_flat_path_for_legacy_id(monkeypatch: pytest.MonkeyPatch):
    store = temp_upload_store.TempUploadStore(SimpleNamespace(temp_upload=SimpleNamespace()))
    legacy_id = _make_upload_id(time.time())
    expected_uri = temp_upload_store._legacy_shared_meta_uri(legacy_id)

    read_uris: list[str] = []

    class _FakeVfs:
        async def read_file(self, uri, ctx=None):
            read_uris.append(uri)
            return '{"temp_file_id": "shared_%s"}' % legacy_id

    monkeypatch.setattr(temp_upload_store, "get_viking_fs", lambda: _FakeVfs())

    meta = await store._read_shared_meta(
        legacy_id, SimpleNamespace(account_id="a", user=SimpleNamespace())
    )
    # Exactly one read, against the legacy flat path.
    assert read_uris == [expected_uri]
    assert meta["temp_file_id"] == f"shared_{legacy_id}"



