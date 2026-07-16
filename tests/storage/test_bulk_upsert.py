# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import threading
import uuid
from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_vector_index_backend import (
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def make_backend(adapter):
    return _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=4),
        bound_account_id="acc1",
        shared_adapter=adapter,
    )


@pytest.mark.asyncio
async def test_single_account_backend_upsert_many_uses_one_adapter_call():
    calls = []

    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "uri"},
                    {"FieldName": "abstract", "FieldType": "text"},
                    {"FieldName": "account_id"},
                    {"FieldName": "context_type"},
                ]
            }

    class _Adapter:
        mode = "local"
        USE_CONTENT_FIELD = False

        def get_collection(self):
            return _Collection()

        def upsert(self, data):
            calls.append(data)
            return [row["id"] for row in data]

    backend = make_backend(_Adapter())
    records = [
        {
            "id": "rec-1",
            "uri": "viking://resources/one",
            "abstract": "one",
            "context_type": "resource",
            "unknown": "ignored",
        },
        {
            "uri": "viking://resources/two",
            "context_type": "resource",
        },
    ]

    ids = await backend.upsert_many(records)

    assert len(calls) == 1
    assert isinstance(calls[0], list)
    assert ids == [row["id"] for row in calls[0]]
    assert ids[0] == "rec-1"
    uuid.UUID(ids[1])
    assert calls[0] == [
        {
            "id": "rec-1",
            "uri": "viking://resources/one",
            "abstract": "one",
            "account_id": "acc1",
            "context_type": "resource",
        },
        {
            "id": ids[1],
            "uri": "viking://resources/two",
            "abstract": "",
            "account_id": "acc1",
            "context_type": "resource",
        },
    ]
    assert "id" not in records[1]
    assert "account_id" not in records[0]
    assert records[0]["unknown"] == "ignored"


@pytest.mark.asyncio
async def test_single_account_backend_upsert_many_empty_batch_skips_adapter():
    class _Adapter:
        mode = "local"
        USE_CONTENT_FIELD = False

        def upsert(self, data):  # pragma: no cover - should never run
            raise AssertionError(f"unexpected upsert: {data}")

    assert await make_backend(_Adapter()).upsert_many([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("records", "error_type", "match"),
    [
        (
            [{"id": "rec-1", "context_type": "invalid"}],
            ValueError,
            "record at index 0: Invalid context_type",
        ),
        (
            [{"id": "rec-1", "account_id": "another-account"}],
            PermissionError,
            "record at index 0: record account_id does not match",
        ),
        (
            [{"id": "rec-1"}, {"id": "rec-1"}],
            ValueError,
            "duplicate record id at index 1",
        ),
    ],
)
async def test_single_account_backend_upsert_many_rejects_invalid_batch_before_write(
    records, error_type, match
):
    class _Adapter:
        mode = "local"
        USE_CONTENT_FIELD = False

        def upsert(self, data):  # pragma: no cover - should never run
            raise AssertionError(f"unexpected upsert: {data}")

    with pytest.raises(error_type, match=match):
        await make_backend(_Adapter()).upsert_many(records)


@pytest.mark.asyncio
async def test_single_account_backend_upsert_rejects_foreign_account_before_write():
    class _Adapter:
        mode = "local"
        USE_CONTENT_FIELD = False

        def upsert(self, data):  # pragma: no cover - should never run
            raise AssertionError(f"unexpected upsert: {data}")

    result = await make_backend(_Adapter()).upsert({"id": "rec-1", "account_id": "another-account"})

    assert result == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_ids", [["rec-1"], ["rec-2", "rec-1"]])
async def test_single_account_backend_upsert_many_rejects_invalid_adapter_result(returned_ids):
    class _Collection:
        def get_meta_data(self):
            return {
                "Fields": [
                    {"FieldName": "id"},
                    {"FieldName": "account_id"},
                ]
            }

    class _Adapter:
        mode = "local"
        USE_CONTENT_FIELD = False

        def get_collection(self):
            return _Collection()

        def upsert(self, data):
            del data
            return returned_ids

    with pytest.raises(RuntimeError, match="do not match the input count and order"):
        await make_backend(_Adapter()).upsert_many([{"id": "rec-1"}, {"id": "rec-2"}])


@pytest.mark.asyncio
async def test_viking_vector_index_backend_upsert_many_delegates_once():
    backend = object.__new__(VikingVectorIndexBackend)
    ctx = SimpleNamespace(account_id="acc1")
    records = [{"id": "rec-1"}, {"id": "rec-2"}]
    calls = []

    class _BoundBackend:
        async def upsert_many(self, data_list):
            calls.append(data_list)
            return [row["id"] for row in data_list]

    backend._get_backend_for_context = lambda _ctx: _BoundBackend()

    assert await backend.upsert_many(records, ctx=ctx) == ["rec-1", "rec-2"]
    assert calls == [records]


@pytest.mark.asyncio
async def test_viking_vector_index_backend_bulk_ingest_balances_scope_on_error():
    backend = object.__new__(VikingVectorIndexBackend)
    ctx = SimpleNamespace(account_id="acc1")
    calls = []

    class _BoundBackend:
        async def begin_bulk_ingest(self):
            calls.append("begin")

        async def end_bulk_ingest(self):
            calls.append("end")

    bound_backend = _BoundBackend()
    backend._get_backend_for_context = lambda _ctx: bound_backend

    with pytest.raises(RuntimeError, match="injected"):
        async with backend.bulk_ingest(ctx=ctx):
            calls.append("body")
            raise RuntimeError("injected")

    assert calls == ["begin", "body", "end"]


@pytest.mark.asyncio
async def test_bulk_ingest_cancellation_during_threaded_begin_balances_scope():
    backend = object.__new__(VikingVectorIndexBackend)
    ctx = SimpleNamespace(account_id="acc1")
    begin_started = threading.Event()
    allow_begin = threading.Event()
    begin_finished = threading.Event()
    calls = []

    class _BoundBackend:
        async def begin_bulk_ingest(self):
            def blocking_begin():
                begin_started.set()
                assert allow_begin.wait(timeout=5)
                calls.append("begin")
                begin_finished.set()

            await asyncio.to_thread(blocking_begin)

        async def end_bulk_ingest(self):
            calls.append("end")

    backend._get_backend_for_context = lambda _ctx: _BoundBackend()

    async def enter_scope():
        async with backend.bulk_ingest(ctx=ctx):
            calls.append("body")

    task = asyncio.create_task(enter_scope())
    try:
        assert await asyncio.to_thread(begin_started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        allow_begin.set()
        assert await asyncio.to_thread(begin_finished.wait, 5)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == ["begin", "end"]
    finally:
        allow_begin.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_bulk_ingest_cancellation_during_threaded_end_waits_for_cleanup():
    backend = object.__new__(VikingVectorIndexBackend)
    ctx = SimpleNamespace(account_id="acc1")
    end_started = threading.Event()
    allow_end = threading.Event()
    end_finished = threading.Event()
    calls = []

    class _BoundBackend:
        async def begin_bulk_ingest(self):
            calls.append("begin")

        async def end_bulk_ingest(self):
            def blocking_end():
                end_started.set()
                assert allow_end.wait(timeout=5)
                calls.append("end")
                end_finished.set()

            await asyncio.to_thread(blocking_end)

    backend._get_backend_for_context = lambda _ctx: _BoundBackend()

    async def use_scope():
        async with backend.bulk_ingest(ctx=ctx):
            calls.append("body")

    task = asyncio.create_task(use_scope())
    try:
        assert await asyncio.to_thread(end_started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        assert not task.done()
        allow_end.set()
        assert await asyncio.to_thread(end_finished.wait, 5)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == ["begin", "body", "end"]
    finally:
        allow_end.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_viking_vector_index_backend_upsert_many_persists_batch(tmp_path):
    collection_name = "bulk_upsert_test"
    backend = VikingVectorIndexBackend(
        VectorDBBackendConfig(
            backend="local",
            name=collection_name,
            dimension=4,
            path=str(tmp_path),
        )
    )
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    schema = {
        "CollectionName": collection_name,
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
            {"FieldName": "abstract", "FieldType": "string"},
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "context_type", "FieldType": "string"},
        ],
        "ScalarIndex": ["uri", "account_id", "context_type"],
    }
    records = [
        {
            "id": f"rec-{index}",
            "uri": f"viking://resources/{index}",
            "vector": [1.0, 0.0, 0.0, float(index)],
            "abstract": f"record {index}",
            "context_type": "resource",
        }
        for index in range(2)
    ]

    try:
        assert await backend.create_collection(collection_name, schema)
        assert await backend.upsert_many(records, ctx=ctx) == ["rec-0", "rec-1"]
        assert await backend.count(ctx=ctx) == 2
        fetched = await backend.get(["rec-0", "rec-1"], ctx=ctx)
        assert [row["id"] for row in fetched] == ["rec-0", "rec-1"]
        assert all(row["account_id"] == "acc1" for row in fetched)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_vikingdb_manager_proxy_upsert_many_forwards_bound_context():
    from openviking.storage.vikingdb_manager import VikingDBManagerProxy

    ctx = SimpleNamespace(account_id="acc1")
    captured = {}

    class _Manager:
        collection_name = "context"
        mode = "local"

        async def upsert_many(self, data_list, *, ctx):
            captured["data_list"] = data_list
            captured["ctx"] = ctx
            return [row["id"] for row in data_list]

    proxy = VikingDBManagerProxy(_Manager(), ctx)
    records = [{"id": "rec-1"}, {"id": "rec-2"}]

    assert await proxy.upsert_many(records) == ["rec-1", "rec-2"]
    assert captured == {"data_list": records, "ctx": ctx}


@pytest.mark.asyncio
async def test_vikingdb_manager_proxy_bulk_ingest_forwards_bound_context():
    from contextlib import asynccontextmanager

    from openviking.storage.vikingdb_manager import VikingDBManagerProxy

    ctx = SimpleNamespace(account_id="acc1")
    calls = []

    class _Manager:
        collection_name = "context"
        mode = "local"

        @asynccontextmanager
        async def bulk_ingest(self, *, ctx):
            calls.append(("begin", ctx))
            try:
                yield
            finally:
                calls.append(("end", ctx))

    proxy = VikingDBManagerProxy(_Manager(), ctx)
    async with proxy.bulk_ingest():
        calls.append(("body", ctx))

    assert calls == [("begin", ctx), ("body", ctx), ("end", ctx)]
