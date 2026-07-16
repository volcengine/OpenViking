# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from benchmark.vectordb_perf.run import upsert_records


@pytest.mark.asyncio
async def test_upsert_records_uses_one_bulk_backend_call():
    calls = []

    class _Backend:
        async def upsert_many(self, records, *, ctx):
            calls.append((records, ctx))
            return [record["id"] for record in records]

        async def upsert(self, record, *, ctx):  # pragma: no cover - should never run
            raise AssertionError(f"unexpected serial upsert: {record}, {ctx}")

    backend = _Backend()
    ctx = object()
    records = [{"id": "rec-1"}, {"id": "rec-2"}]

    ids = await upsert_records(backend, ctx, records)

    assert ids == ["rec-1", "rec-2"]
    assert calls == [(records, ctx)]


@pytest.mark.asyncio
async def test_upsert_records_rejects_incomplete_bulk_result():
    class _Backend:
        async def upsert_many(self, records, *, ctx):
            del records, ctx
            return ["rec-1"]

    with pytest.raises(RuntimeError, match="returned 1 ids for 2 records"):
        await upsert_records(
            _Backend(),
            object(),
            [{"id": "rec-1"}, {"id": "rec-2"}],
        )
