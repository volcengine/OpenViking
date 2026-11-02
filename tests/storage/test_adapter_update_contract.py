# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openviking.storage.errors import VikingDBException
from openviking.storage.vectordb.collection.http_collection import HttpCollection
from openviking.storage.vectordb.collection.result import UpsertDataResult
from openviking.storage.vectordb.collection.vikingdb_collection import VikingDBCollection
from openviking.storage.vectordb.collection.volcengine_api_key_collection import (
    VolcengineApiKeyCollection,
)
from openviking.storage.vectordb.collection.volcengine_collection import VolcengineCollection
from openviking.storage.vectordb_adapters.http_adapter import HttpCollectionAdapter
from openviking.storage.vectordb_adapters.local_adapter import LocalCollectionAdapter
from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter
from openviking.storage.vectordb_adapters.vikingdb_private_adapter import (
    VikingDBPrivateCollectionAdapter,
)
from openviking.storage.vectordb_adapters.volcengine_adapter import VolcengineCollectionAdapter

ADAPTERS = (
    LocalCollectionAdapter,
    HttpCollectionAdapter,
    VolcengineCollectionAdapter,
    VikingDBPrivateCollectionAdapter,
    OpenGaussCollectionAdapter,
)
REMOTE_COLLECTIONS = (
    VolcengineCollection,
    VolcengineApiKeyCollection,
    VikingDBCollection,
    HttpCollection,
)


def _adapter(adapter_type, collection):
    adapter = object.__new__(adapter_type)
    adapter._collection = collection
    if adapter_type is LocalCollectionAdapter:
        adapter._load_lock = threading.Lock()
    return adapter


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_update_batches_and_preserves_explicit_empty_acl_fields(adapter_type):
    calls = []

    def update_data(records):
        calls.append(records)
        return UpsertDataResult(ids=[record["id"] for record in records])

    adapter = _adapter(adapter_type, SimpleNamespace(update_data=update_data))
    records = [{"id": f"doc-{i}", "acl_direct_grants": []} for i in range(205)]

    assert adapter.update_data(records) == [record["id"] for record in records]
    expected_sizes = [100, 100, 5] if adapter._DATA_BATCH_SIZE else [205]
    assert [len(batch) for batch in calls] == expected_sizes
    assert [record for batch in calls for record in batch] == records


@pytest.mark.parametrize("invalid_record", [{}, {"id": ""}, {"id": None}, {"id": "doc-0"}])
def test_update_validates_all_batches_before_first_write(invalid_record):
    collection = SimpleNamespace(update_data=Mock())
    adapter = _adapter(HttpCollectionAdapter, collection)
    records = [{"id": f"doc-{i}"} for i in range(100)] + [invalid_record]

    with pytest.raises(ValueError):
        adapter.update_data(records)

    collection.update_data.assert_not_called()


def test_empty_update_does_not_load_collection():
    adapter = _adapter(HttpCollectionAdapter, None)
    assert adapter.update_data([]) == []


@pytest.mark.parametrize(
    "result",
    [
        {"updated": 1},
        {"updated": True},
        {"updated": 2, "ids": ["doc-1"]},
        {"updated": 1, "ids": ["doc-1", "doc-2"]},
        {"updated": 2, "ids": ["doc-2", "doc-1"]},
        {"ids": ["doc-1", "doc-2"], "primary_keys": ["doc-1", "wrong"]},
        ["doc-1"],
        UpsertDataResult(ids=["doc-1", "wrong"]),
        {},
        None,
    ],
)
def test_update_rejects_incomplete_or_ambiguous_results(result):
    adapter = _adapter(
        HttpCollectionAdapter, SimpleNamespace(update_data=Mock(return_value=result))
    )
    with pytest.raises(RuntimeError):
        adapter.update_data([{"id": "doc-1"}, {"id": "doc-2"}])


def test_update_stops_after_a_failed_batch():
    update_data = Mock(side_effect=[{"updated": 100}, {"updated": 1}])
    adapter = _adapter(HttpCollectionAdapter, SimpleNamespace(update_data=update_data))

    with pytest.raises(RuntimeError, match="count"):
        adapter.update_data([{"id": f"doc-{i}"} for i in range(250)])

    assert update_data.call_count == 2


def _remote_adapter(monkeypatch, collection_type, body, status=200):
    response = SimpleNamespace(status_code=status, text=json.dumps(body), json=lambda: body)
    request = Mock(return_value=response)
    collection = object.__new__(collection_type)
    collection.project_name = "default"
    collection.collection_name = "context"
    if collection_type is HttpCollection:
        collection.url_prefix = "http://localhost:1933/"
        monkeypatch.setattr("requests.post", request)
        adapter_type = HttpCollectionAdapter
    elif collection_type is VikingDBCollection:
        collection.client = SimpleNamespace(do_req=request)
        adapter_type = VikingDBPrivateCollectionAdapter
    else:
        collection.data_client = SimpleNamespace(do_req=request)
        adapter_type = VolcengineCollectionAdapter
    return _adapter(adapter_type, collection), request, response


def _body(collection_type, result, **extra):
    key = "data" if collection_type is HttpCollection else "result"
    return {"code": 0, key: result, **extra}


@pytest.mark.parametrize("collection_type", REMOTE_COLLECTIONS)
@pytest.mark.parametrize("result", [{}, {"token_usage": {"total_tokens": 0}}, {"ids": ["doc-1"]}])
def test_remote_update_accepts_verified_acknowledgement(monkeypatch, collection_type, result):
    adapter, request, _ = _remote_adapter(
        monkeypatch, collection_type, _body(collection_type, result)
    )
    records = [{"id": "doc-1", "acl_direct_grants": [], "acl_inherited_grants": []}]

    assert adapter.update_data(records) == ["doc-1"]
    kwargs = request.call_args.kwargs
    payload = kwargs.get("req_body") or kwargs["json"]
    transmitted = payload.get("data") or json.loads(payload["fields"])
    assert transmitted == records


@pytest.mark.parametrize(
    "collection_type", (VolcengineCollection, VolcengineApiKeyCollection, VikingDBCollection)
)
def test_vikingdb_update_accepts_explicit_success_without_result(monkeypatch, collection_type):
    adapter, _, _ = _remote_adapter(
        monkeypatch, collection_type, {"code": "Success", "result": None}
    )

    assert adapter.update_data([{"id": "doc-1", "acl_direct_grants": []}]) == ["doc-1"]


@pytest.mark.parametrize("code", [None, 0, "0", "InternalError"])
@pytest.mark.parametrize("collection_type", REMOTE_COLLECTIONS)
def test_remote_update_rejects_null_without_vikingdb_success(monkeypatch, collection_type, code):
    adapter, _, _ = _remote_adapter(
        monkeypatch, collection_type, _body(collection_type, None, code=code)
    )

    with pytest.raises(VikingDBException):
        adapter.update_data([{"id": "doc-1"}])


def test_http_update_rejects_null_even_with_success_code(monkeypatch):
    adapter, _, _ = _remote_adapter(monkeypatch, HttpCollection, {"code": "Success", "data": None})

    with pytest.raises(VikingDBException):
        adapter.update_data([{"id": "doc-1"}])


@pytest.mark.parametrize("collection_type", REMOTE_COLLECTIONS)
@pytest.mark.parametrize(
    "body",
    [
        {"code": "InternalError", "result": {}, "data": {}},
        {"ResponseMetadata": {"Error": {"Code": "Denied"}}, "result": {}, "data": {}},
        {"result": {"error": "write failed"}, "data": {"error": "write failed"}},
        {"result": None, "data": None},
        {"result": "ok", "data": "ok"},
        {"result": {"status": "ok"}, "data": {"status": "ok"}},
        {"status": "failed", "result": {}, "data": {}},
        {"result": {"token_usage": "invalid"}, "data": {"token_usage": "invalid"}},
        {},
        [],
    ],
)
def test_remote_update_rejects_business_errors_and_malformed_success(
    monkeypatch, collection_type, body
):
    adapter, _, _ = _remote_adapter(monkeypatch, collection_type, body)

    with pytest.raises(VikingDBException):
        adapter.update_data([{"id": "doc-1"}])


@pytest.mark.parametrize("collection_type", REMOTE_COLLECTIONS)
def test_remote_update_propagates_http_errors(monkeypatch, collection_type):
    adapter, _, _ = _remote_adapter(
        monkeypatch, collection_type, _body(collection_type, {}), status=500
    )
    with pytest.raises(VikingDBException):
        adapter.update_data([{"id": "doc-1"}])


@pytest.mark.parametrize("collection_type", REMOTE_COLLECTIONS)
def test_remote_update_rejects_invalid_json(monkeypatch, collection_type):
    adapter, _, response = _remote_adapter(monkeypatch, collection_type, {})
    response.json = Mock(side_effect=ValueError("invalid JSON"))
    with pytest.raises(VikingDBException, match="invalid JSON"):
        adapter.update_data([{"id": "doc-1"}])


@pytest.mark.parametrize("collection_type", REMOTE_COLLECTIONS)
def test_remote_update_preserves_missing_id_error(monkeypatch, collection_type):
    adapter, _, _ = _remote_adapter(
        monkeypatch, collection_type, _body(collection_type, {"ids_not_exist": ["doc-1"]})
    )
    with pytest.raises(ValueError, match="not found"):
        adapter.update_data([{"id": "doc-1"}])
