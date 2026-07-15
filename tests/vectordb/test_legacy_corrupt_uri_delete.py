# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import hashlib
import json

import pytest

from openviking.server.identity import RequestContext, Role, UserIdentifier
from openviking.storage.vectordb.index import local_index as local_index_module
from openviking.storage.vectordb.index.local_index import LocalIndex
from openviking.storage.vectordb.store.data import DeltaRecord
from openviking.storage.viking_vector_index_backend import (
    VikingVectorIndexBackend,
    _SingleAccountBackend,
)
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


class _JsonFieldConverter:
    def convert_fields_for_index(self, fields_json: str) -> str:
        return json.dumps(json.loads(fields_json), separators=(",", ":"))


class _RecordingEngineProxy:
    def __init__(self):
        self.deleted = []
        self.upserted = []

    def delete_data(self, delta_list):
        self.deleted.extend(delta_list)

    def upsert_data(self, delta_list):
        self.upserted.extend(delta_list)


def _make_index() -> tuple[LocalIndex, _RecordingEngineProxy]:
    index = LocalIndex.__new__(LocalIndex)
    proxy = _RecordingEngineProxy()
    index.field_type_converter = _JsonFieldConverter()
    index.dense_search = None
    index.engine_proxy = proxy
    return index, proxy


def test_delete_legacy_record_ignores_unparseable_old_fields(monkeypatch):
    index, proxy = _make_index()
    warnings = []
    monkeypatch.setattr(
        local_index_module.logger,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )
    record = DeltaRecord(
        type=DeltaRecord.Type.DELETE,
        label=41,
        old_fields='{"truncated":',
    )

    index.delete_data([record])

    assert len(proxy.deleted) == 1
    assert proxy.deleted[0].label == 41
    assert proxy.deleted[0].old_fields == ""
    assert warnings == [
        "Ignoring unparseable old_fields while deleting legacy record label=41; "
        "scalar-index cleanup will be best-effort"
    ]


def test_upsert_legacy_record_keeps_invalid_old_fields_fail_closed():
    index, proxy = _make_index()
    record = DeltaRecord(
        type=DeltaRecord.Type.UPSERT,
        label=41,
        fields='{"current":true}',
        old_fields='{"truncated":',
    )

    with pytest.raises(json.JSONDecodeError):
        index.upsert_data([record])

    assert proxy.upserted == []


def test_delete_valid_old_fields_still_converts_them():
    index, proxy = _make_index()
    record = DeltaRecord(
        type=DeltaRecord.Type.DELETE,
        label=42,
        old_fields='{ "name": "healthy" }',
    )

    index.delete_data([record])

    assert proxy.deleted[0].old_fields == '{"name":"healthy"}'


@pytest.mark.asyncio
async def test_local_backend_deletes_tenant_scoped_ids_without_query(monkeypatch):
    deleted = []

    class _Adapter:
        mode = "local"

        def delete(self, **kwargs):
            deleted.extend(kwargs["ids"])
            return len(kwargs["ids"])

    async def _fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.asyncio.to_thread", _fake_to_thread
    )
    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=2),
        bound_account_id="acc1",
        shared_adapter=_Adapter(),
    )
    uri = "viking://resources/acc1/wiki/broken.md"

    assert await backend.delete_deterministic_uris("acc1", [uri]) == 3
    assert deleted == [
        hashlib.md5(f"acc1:{seed}".encode()).hexdigest()
        for seed in (uri, f"{uri}/.abstract.md", f"{uri}/.overview.md")
    ]

    with pytest.raises(ValueError, match="bound account"):
        await backend.delete_deterministic_uris("acc2", [uri])


@pytest.mark.asyncio
async def test_public_delete_uris_runs_filter_and_deterministic_repair():
    ctx = RequestContext(
        user=UserIdentifier(account_id="acc1", user_id="user1"),
        role=Role.USER,
    )
    calls = []

    class _Backend:
        async def delete_by_filter(self, filter_expr):
            calls.append(("filter", filter_expr))
            return 0

        async def delete_deterministic_uris(self, account_id, uris):
            calls.append(("deterministic", account_id, list(uris)))
            return 3

    backend = VikingVectorIndexBackend.__new__(VikingVectorIndexBackend)
    account_backend = _Backend()
    backend._get_backend_for_context = lambda _ctx: account_backend
    uri = "viking://resources/acc1/wiki/broken.md"

    await backend.delete_uris(ctx, [uri])

    assert calls[0][0] == "filter"
    assert calls[1] == ("deterministic", "acc1", [uri])
