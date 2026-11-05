# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import And, Eq, PathScope
from openviking.storage.vectordb.collection.result import AggregateResult
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter
from openviking.storage.viking_vector_index_backend import (
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


@pytest.mark.parametrize(
    ("agg", "expected"),
    [
        ({"0": 2, "1": 1, "2": 4}, (7, 4)),
        ({"0": 2, "1": 1, "2": 4, "level": 7}, (7, 4)),
        ({}, (0, 0)),
    ],
)
def test_collection_adapter_count_by_level_normalizes_backend_results(agg, expected):
    calls = []

    def aggregate_data(**kwargs):
        calls.append(kwargs)
        return AggregateResult(agg=agg, op="count", field="level")

    adapter = LocalCollectionAdapter("context", "", "default")
    adapter._collection = SimpleNamespace(aggregate_data=aggregate_data)

    assert adapter.count_by_level(filter=Eq("context_type", "memory")) == expected
    assert calls == [
        {
            "index_name": "default",
            "op": "count",
            "field": "level",
            "filters": {"op": "must", "field": "context_type", "conds": ["memory"]},
        }
    ]


@pytest.mark.parametrize("agg", [{"2": True}, {"2": -1}, {"2": "not-a-count"}])
def test_collection_adapter_count_by_level_rejects_invalid_counts(agg):
    adapter = LocalCollectionAdapter("context", "", "default")
    adapter._collection = SimpleNamespace(
        aggregate_data=lambda **_kwargs: AggregateResult(agg=agg, op="count", field="level")
    )

    with pytest.raises(RuntimeError, match="invalid grouped count"):
        adapter.count_by_level()


@pytest.mark.asyncio
async def test_single_account_backend_count_by_level_adds_bound_account_filter():
    calls = []

    class _Adapter:
        mode = "local"
        USE_CONTENT_FIELD = False

        def count_by_level(self, filter=None):
            calls.append(filter)
            return 7, 4

    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=4),
        bound_account_id="acct",
        shared_adapter=_Adapter(),
    )

    result = await backend.count_by_level(filter=Eq("context_type", "memory"))

    assert result == (7, 4)
    assert calls == [And([Eq("account_id", "acct"), Eq("context_type", "memory")])]


@pytest.mark.asyncio
async def test_vector_backend_count_by_level_merges_runtime_visibility_filter(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)
    account_backend = SimpleNamespace(count_by_level=AsyncMock(return_value=(7, 4)))
    backend = object.__new__(VikingVectorIndexBackend)
    monkeypatch.setattr(backend, "_get_backend_for_context", AsyncMock(return_value=account_backend))
    monkeypatch.setattr(backend, "_acl_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        backend,
        "_tenant_filter",
        lambda _ctx, *, acl_enabled: Eq("owner_user_id", "alice") if acl_enabled else None,
    )
    scope = PathScope("uri", "viking://resources/docs", depth=-1)

    result = await backend.count_by_level(filter=scope, ctx=ctx)

    assert result == (7, 4)
    account_backend.count_by_level.assert_awaited_once_with(
        filter=And([scope, Eq("owner_user_id", "alice")])
    )
