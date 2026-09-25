# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Persistence and recovery tests for the TTL scheduling registry."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import (
    AGFSDirectoryNotEmptyError,
    AGFSNetworkError,
    AGFSTimeoutError,
)
from openviking.server.identity import RequestContext, Role
from openviking.storage.ttl_registry import (
    TTLRecord,
    TTLRegistry,
    cleanup_not_before,
    record_from_fields,
)
from openviking.storage.viking_fs import VikingFS
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


class _MemoryAGFS:
    def __init__(self):
        self.files = {}
        self.ensure_calls = []
        self.write_calls = []
        self.read_calls = []
        self.stat_calls = []
        self.rm_calls = []
        self.ls_calls = []
        self.locks = {}

    async def ensure_parent_dirs(self, path):
        self.ensure_calls.append(path)

    async def pathlock_acquire_exact(self, path):
        lock = self.locks.setdefault(path, asyncio.Lock())
        await lock.acquire()
        return path

    async def pathlock_release(self, lease):
        self.locks[lease].release()

    async def write(self, path, data, **kwargs):
        if "/records/" in path:
            assert self.locks[path].locked()
            assert kwargs["fs_ctx"]["lease_ref"] == path
        self.write_calls.append(path)
        self.files[path] = data

    async def read(self, path):
        self.read_calls.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def stat(self, path, **kwargs):
        self.stat_calls.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return {"path": path}

    async def rm(self, path, **kwargs):
        self.rm_calls.append(path)
        if path in self.files:
            del self.files[path]
        elif any(key.startswith(path + "/") for key in self.files):
            raise AGFSDirectoryNotEmptyError("directory not empty")
        else:
            raise FileNotFoundError(path)

    async def ls(self, path, *, offset=0, limit=None, sort_by=None, **kwargs):
        self.ls_calls.append((path, limit))
        prefix = path.rstrip("/") + "/"
        names = {}
        for key in self.files:
            if key.startswith(prefix):
                suffix = key[len(prefix) :]
                name = suffix.split("/")[0]
                names[name] = {"name": name, "isDir": "/" in suffix}
        values = [names[name] for name in sorted(names)]
        return values[offset:] if limit is None else values[offset : offset + limit]


def _record(*, generation="g1", uri="viking://user/u1/sessions/s1"):
    return TTLRecord(
        object_uri=uri,
        object_type="session",
        account_id="acct",
        user_id="u1",
        expires_at="2026-09-23T00:00:00.000Z",
        generation=generation,
    )


def test_physical_cleanup_jitter_is_stable_and_bounded_by_one_day():
    record = _record()

    first = cleanup_not_before(record, jitter_seconds=86400)
    restarted = cleanup_not_before(record, jitter_seconds=86400)
    offset = parse_iso_datetime(first) - parse_iso_datetime(record.expires_at)

    assert first == restarted
    assert timedelta(0) <= offset < timedelta(days=1)
    assert cleanup_not_before(record, jitter_seconds=0) == record.expires_at


@pytest.mark.asyncio
async def test_upsert_publishes_marker_before_record_and_roundtrips():
    agfs = _MemoryAGFS()
    registry = TTLRegistry(agfs)
    record = _record()

    assert await registry.account_may_have_records("acct") is False
    assert await registry.account_may_have_records("acct") is False
    assert agfs.stat_calls == [registry.marker_path("acct")] * 2

    await registry.upsert(record)

    assert agfs.write_calls[0] == registry.marker_path("acct")
    assert "/due/" in agfs.write_calls[1]
    assert agfs.write_calls[2] == registry.record_path("acct", record.object_uri)
    assert await registry.account_may_have_records("acct") is True
    assert await registry.get("acct", record.object_uri) == record


@pytest.mark.asyncio
@pytest.mark.parametrize("read_kind", ["text", "bytes", "grep"])
async def test_reader_sees_first_ttl_object_imported_by_another_worker(monkeypatch, read_kind):
    class CachedAGFS(_MemoryAGFS):
        async def stat(self, path, *, bypass_cache=False):
            if not bypass_cache:
                raise FileNotFoundError("cached marker miss")
            return await super().stat(path)

    agfs = CachedAGFS()
    reader = TTLRegistry(agfs)
    writer = TTLRegistry(agfs)
    uri = "viking://user/u1/memories/events/expired.md"
    assert await reader.account_may_have_records("acct") is False
    record = replace(_record(uri=uri), object_type="event", expires_at="2000-01-01T00:00:00Z")
    await writer.upsert(record)

    fs = VikingFS(agfs=SimpleNamespace())
    fs.ttl_registry = reader
    ctx = RequestContext(user=UserIdentifier("acct", "u1"), role=Role.ROOT)
    path = fs._uri_to_path(uri, ctx=ctx)
    fs._async_agfs.stat = AsyncMock(return_value={"isDir": False})
    fs._async_agfs.read = AsyncMock(
        return_value=b'body\n<!-- MEMORY_FIELDS {"expires_at":"2000-01-01T00:00:00Z"} -->'
    )
    monkeypatch.setattr("openviking.storage.viking_fs._access.ttl_enabled", lambda: False)
    if read_kind == "grep":
        fs._async_agfs.grep = AsyncMock(
            return_value={"matches": [{"file": path, "line": 1, "content": "body"}]}
        )
        result = await fs._grep_with_agfs(uri.rsplit("/", 1)[0], "body", node_limit=1, ctx=ctx)
        assert result["matches"] == []
        assert fs._async_agfs.grep.await_args.kwargs["node_limit"] is None
    else:
        read = fs.read_file if read_kind == "text" else fs.read_file_bytes
        with pytest.raises(NotFoundError):
            await read(uri, ctx=ctx)
    fs._async_agfs.read.assert_awaited()
    assert await reader.account_may_have_records("acct") is True
    # Only positive observations may be reused across requests.
    assert agfs.stat_calls == [reader.marker_path("acct")] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("backend unavailable"),
        AGFSNetworkError("endpoint not found"),
        AGFSTimeoutError("backend not found before timeout"),
    ],
)
async def test_marker_inspection_fails_open_on_storage_error(error):
    class _UnavailableAGFS(_MemoryAGFS):
        async def stat(self, path, **kwargs):
            raise error

    assert await TTLRegistry(_UnavailableAGFS()).account_may_have_records("acct") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["record", "claim"])
async def test_registry_outage_is_not_treated_as_absence(operation):
    agfs = _MemoryAGFS()
    registry = TTLRegistry(agfs)
    error = AGFSNetworkError("backend endpoint not found")
    agfs.read = agfs.stat = agfs.ls = AsyncMock(side_effect=error)
    with pytest.raises(AGFSNetworkError, match="endpoint not found"):
        if operation == "record":
            await registry.get("acct", _record().object_uri)
        else:
            _ = [item async for item in registry.claim_due(now=datetime.now(timezone.utc))]


@pytest.mark.asyncio
async def test_get_rejects_record_key_mismatch():
    agfs = _MemoryAGFS()
    registry = TTLRegistry(agfs)
    requested_uri = "viking://user/u1/sessions/s1"
    path = registry.record_path("acct", requested_uri)
    await registry.upsert(_record())
    payload = json.loads(agfs.files[path])
    payload["payload"]["record"]["object_uri"] = "viking://user/u1/sessions/other"
    agfs.files[path] = json.dumps(payload).encode()

    with pytest.raises(ValueError, match="Invalid TTL registry record"):
        await registry.get("acct", requested_uri)


@pytest.mark.asyncio
async def test_remove_is_generation_fenced_and_missing_is_idempotent():
    agfs = _MemoryAGFS()
    registry = TTLRegistry(agfs)
    record = _record()
    await registry.upsert(record)

    assert await registry.remove_if_generation("acct", record.object_uri, "stale") is False
    assert agfs.rm_calls == []
    assert await registry.remove_if_generation("acct", record.object_uri, "g1") is True
    assert registry.record_path("acct", record.object_uri) not in agfs.files
    assert not any("/due/" in path for path in agfs.files)
    assert await registry.remove_if_generation("acct", record.object_uri, "g1") is False


@pytest.mark.asyncio
async def test_due_index_only_reads_bounded_due_records_and_survives_restart():
    agfs = _MemoryAGFS()
    registry = TTLRegistry(agfs)
    due = replace(_record(), expires_at="2020-01-01T00:00:00.000Z")
    await registry.upsert(due)
    for i in range(150):
        await registry.upsert(
            replace(
                _record(uri=f"viking://user/u1/sessions/f{i}"),
                expires_at="2999-01-01T00:00:00.000Z",
            )
        )
    agfs.read_calls.clear()
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    claimed = [item async for item in TTLRegistry(agfs).claim_due(now=now, limit=1)]
    assert [item[0] for item in claimed] == [due]
    assert len(agfs.read_calls) == 2  # one due entry and its keyed projection
    assert all(limit is not None for _, limit in agfs.ls_calls)
    assert [item async for item in TTLRegistry(agfs).claim_due(now=now, limit=1)] == []
    assert [
        item[0]
        async for item in TTLRegistry(agfs).claim_due(now=now + timedelta(minutes=6), limit=1)
    ] == [due]


@pytest.mark.asyncio
async def test_retry_backoff_is_durable_and_cannot_postpone_replacement():
    agfs = _MemoryAGFS()
    registry = TTLRegistry(agfs)
    record = replace(_record(), expires_at="2020-01-01T00:00:00.000Z")
    await registry.upsert(record)
    assert await registry.defer_retry(
        record, retry_count=2, task_id="task-1", next_retry_at="2026-09-23T00:00:00.000Z"
    )
    # Ordinary writes of the same TTL fields must retain the scheduled retry.
    await registry.upsert(record)
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    assert [item async for item in TTLRegistry(agfs).claim_due(now=now)] == []
    claims = [item async for item in TTLRegistry(agfs).claim_due(now=now + timedelta(days=1))]
    assert claims[0][1]["task_id"] == "task-1"
    assert claims[0][1]["retry_count"] == 2
    await registry.upsert(replace(record, generation="new"))
    assert not await registry.defer_retry(
        record, retry_count=3, task_id="old", next_retry_at="2999-01-01T00:00:00.000Z"
    )


@pytest.mark.asyncio
async def test_interrupted_publication_is_recovered_from_due_entry():
    agfs = _MemoryAGFS()
    registry = TTLRegistry(agfs)
    record = replace(_record(), expires_at="2020-01-01T00:00:00.000Z")
    original = agfs.write

    async def fail_projection(path, data, **kwargs):
        if "/records/" in path:
            raise RuntimeError("crash before projection")
        await original(path, data, **kwargs)

    agfs.write = fail_projection
    with pytest.raises(RuntimeError, match="crash"):
        await registry.upsert(record)
    agfs.write = original
    recovered = TTLRegistry(agfs)
    claims = [item async for item in recovered.claim_due(now=datetime.now(timezone.utc))]
    assert [item[0] for item in claims] == [record]
    assert await recovered.get(record.account_id, record.object_uri) == record


@pytest.mark.asyncio
async def test_two_schedulers_cannot_claim_the_same_due_revision():
    agfs = _MemoryAGFS()
    registry = TTLRegistry(agfs)
    await registry.upsert(replace(_record(), expires_at="2020-01-01T00:00:00.000Z"))

    async def claim():
        return [item async for item in TTLRegistry(agfs).claim_due(now=datetime.now(timezone.utc))]

    first, second = await asyncio.gather(claim(), claim())
    assert len(first) + len(second) == 1


@pytest.mark.asyncio
async def test_due_claims_are_oldest_first_and_obey_object_byte_and_time_budgets():
    agfs = _MemoryAGFS()
    registry = TTLRegistry(agfs)
    newer = replace(
        _record(uri="viking://user/u1/sessions/newer"), expires_at="2021-01-01T00:00:00.000Z"
    )
    older = replace(
        _record(uri="viking://user/u1/sessions/older"), expires_at="2020-01-01T00:00:00.000Z"
    )
    for record in (newer, older):
        await registry.upsert(record)
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    assert [record async for record in registry.claim_due(now=now, time_budget=0)] == []
    assert [record async for record in registry.claim_due(now=now, max_bytes=1)] == []
    assert [
        record for record, _ in [item async for item in registry.claim_due(now=now, limit=1)]
    ] == [older]


def test_record_from_fields_requires_complete_frozen_fence():
    ctx = RequestContext(user=UserIdentifier("acct", "u1"), role=Role.ROOT)
    uri = "viking://user/u1/memories/events/e.md"

    assert record_from_fields(uri=uri, object_type="event", fields={}, ctx=ctx) is None
    record = record_from_fields(
        uri=uri,
        object_type="event",
        fields={"expires_at": "2026-09-23T00:00:00.000Z", "ttl_generation": "g1"},
        ctx=ctx,
    )
    assert record == TTLRecord(
        object_uri=uri,
        object_type="event",
        account_id="acct",
        user_id="u1",
        expires_at="2026-09-23T00:00:00.000Z",
        generation="g1",
    )
