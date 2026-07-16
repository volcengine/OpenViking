# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import hashlib
import json

import pytest

from openviking.server.identity import RequestContext, Role, UserIdentifier
from openviking.storage.expr import And, Eq
from openviking.storage.vectordb.collection.local_collection import PersistCollection
from openviking.storage.vectordb.index import local_index as local_index_module
from openviking.storage.vectordb.index.local_index import LocalIndex
from openviking.storage.vectordb.store.data import CandidateData, DeltaRecord
from openviking.storage.vectordb.utils.str_to_uint64 import str_to_uint64
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter
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


def test_local_index_upsert_conversion_rejects_unparseable_old_fields():
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
@pytest.mark.parametrize("mode", ["local", "cuvs"])
async def test_local_backed_modes_delete_tenant_scoped_ids_without_query(monkeypatch, mode):
    deleted = []

    class _Adapter:
        LOCAL_STORAGE_BACKED = True

        def __init__(self):
            self.mode = mode

        def delete(self, **kwargs):
            deleted.extend(kwargs["ids"])
            return len(kwargs["ids"])

    async def _fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "openviking.storage.viking_vector_index_backend.asyncio.to_thread", _fake_to_thread
    )
    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=4),
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
async def test_real_local_store_removes_corrupt_candidate_after_filter_skip(tmp_path):
    account_id = "acc1"
    uri = "viking://resources/acc1/wiki/broken.md"
    record_id = hashlib.md5(f"{account_id}:{uri}".encode()).hexdigest()
    adapter = LocalCollectionAdapter(
        collection_name="context",
        project_path=str(tmp_path),
        index_name="default",
    )
    backend = _SingleAccountBackend(
        config=VectorDBBackendConfig(backend="local", name="context", dimension=4),
        bound_account_id=account_id,
        shared_adapter=adapter,
    )
    schema = {
        "CollectionName": "context",
        "Fields": [
            {"FieldName": "id", "FieldType": "string", "IsPrimaryKey": True},
            {"FieldName": "uri", "FieldType": "path"},
            {"FieldName": "account_id", "FieldType": "string"},
            {"FieldName": "vector", "FieldType": "vector", "Dim": 4},
        ],
        "ScalarIndex": ["uri", "account_id"],
    }

    try:
        assert await backend.create_collection("context", schema)
        assert (
            await backend.upsert(
                {
                    "id": record_id,
                    "uri": uri,
                    "account_id": account_id,
                    "vector": [1.0, 0.0, 0.0, 0.0],
                }
            )
            == record_id
        )

        collection = adapter.get_collection()
        local_collection = collection._Collection__collection
        assert isinstance(local_collection, PersistCollection)
        label = str_to_uint64(record_id)
        assert local_collection.store_mgr is not None
        corrupt = CandidateData(
            label=label,
            vector=[1.0, 0.0, 0.0, 0.0],
            fields='{"truncated":',
        )
        local_collection.store_mgr.add_cands_data([corrupt], need_delta=False)

        # The scalar filter still finds the indexed label, but the collection
        # skips its unparseable CandidateData before the adapter can recover an id.
        assert (
            await backend.delete_by_filter(And([Eq("account_id", account_id), Eq("uri", uri)])) == 0
        )
        assert local_collection.store_mgr.fetch_cands_data([label]) == [corrupt]

        assert await backend.delete_deterministic_uris(account_id, [uri]) == 3
        assert local_collection.store_mgr.fetch_cands_data([label]) == [None]
        assert collection.search_by_vector("default", [1.0, 0.0, 0.0, 0.0], limit=1).data == []
    finally:
        await backend.close()


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
