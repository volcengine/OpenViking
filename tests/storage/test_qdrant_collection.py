# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any, Dict, List

import pytest

import openviking.storage.vectordb.collection.qdrant_collection as qdrant_collection_module
from openviking.storage.vectordb.collection.qdrant_collection import (
    ORIGINAL_ID_FIELD,
    QdrantCollection,
    _to_qdrant_point_id,
    _sparse_to_qdrant,
)
from openviking.storage.vectordb.collection.qdrant_rest import QdrantRestError


class _StubClient:
    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"method": method, "path": path, **kwargs})
        if not self._responses:
            raise AssertionError("No more stubbed Qdrant responses available")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _build_collection_stub(client: _StubClient) -> QdrantCollection:
    collection = object.__new__(QdrantCollection)
    collection._client = client
    collection._physical_collection_name = "proj__context"
    collection._dense_vector_name = "vector"
    collection._sparse_vector_name = "sparse_vector"
    collection._vector_dim = 3
    collection._path_payload_fields = {"parent_uri", "scope_roots"}
    return collection


class _FailingMetaStore:
    def save_collection_meta(self, **_: Any) -> None:
        raise RuntimeError("metadata write failed")


class _RecordingMetaStore:
    def __init__(self) -> None:
        self.deleted_keys: List[str] = []

    def delete_collection_meta(self, *, collection_key: str) -> None:
        self.deleted_keys.append(collection_key)


def test_sparse_to_qdrant_warns_on_hash_collision(monkeypatch, caplog):
    monkeypatch.setattr(
        qdrant_collection_module,
        "_hash_sparse_term",
        lambda term: 7 if term in {"alpha", "beta"} else 9,
    )

    payload = _sparse_to_qdrant({"alpha": 1.0, "beta": 2.5, "gamma": 4.0})

    assert payload == {"indices": [7, 9], "values": [3.5, 4.0]}
    assert "hash collision detected" in caplog.text


def test_scroll_points_stops_on_repeated_next_page_offset(caplog):
    client = _StubClient(
        [
            {"result": {"points": [{"id": "p1"}], "next_page_offset": "repeat-me"}},
            {"result": {"points": [{"id": "p2"}], "next_page_offset": "repeat-me"}},
        ]
    )
    collection = _build_collection_stub(client)

    points = collection._scroll_points(limit=None)

    assert [point["id"] for point in points] == ["p1", "p2"]
    assert "next_page_offset repeated" in caplog.text


def test_delete_all_data_prefers_filter_delete():
    client = _StubClient([{"status": "ok", "result": {}}])
    collection = _build_collection_stub(client)
    collection._scan_points_with_warning = lambda **_: (_ for _ in ()).throw(
        AssertionError("fallback scan should not be used")
    )
    collection._delete_points = lambda _: (_ for _ in ()).throw(
        AssertionError("batched delete should not be used")
    )

    collection.delete_all_data()

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["method"] == "POST"
    assert call["path"] == "/collections/proj__context/points/delete"
    assert call["json_body"] == {"filter": {}}
    assert call["params"] == {"wait": "true"}


def test_delete_all_data_falls_back_to_batched_delete(caplog):
    client = _StubClient([QdrantRestError("delete failed")])
    collection = _build_collection_stub(client)
    collection._scan_points_with_warning = lambda **_: [
        {"id": str(i)} for i in range(QdrantCollection.DEFAULT_DELETE_BATCH_SIZE * 2 + 5)
    ]

    deleted_batches: List[List[str]] = []
    collection._delete_points = lambda ids: deleted_batches.append(list(ids))

    collection.delete_all_data()

    assert [len(batch) for batch in deleted_batches] == [
        QdrantCollection.DEFAULT_DELETE_BATCH_SIZE,
        QdrantCollection.DEFAULT_DELETE_BATCH_SIZE,
        5,
    ]
    assert "Falling back to batched delete_all_data" in caplog.text


def test_make_point_accepts_zero_id():
    collection = _build_collection_stub(_StubClient([]))

    point = collection._make_point({"id": 0, "vector": [0.1, 0.2, 0.3], "name": "zero"})

    assert point["id"] == 0
    assert point["vector"]["vector"] == [0.1, 0.2, 0.3]
    assert point["payload"]["name"] == "zero"
    assert point["payload"][ORIGINAL_ID_FIELD] == 0


def test_make_point_maps_arbitrary_string_id_and_preserves_original_id():
    collection = _build_collection_stub(_StubClient([]))

    point = collection._make_point({"id": "doc_1", "vector": [0.1, 0.2, 0.3]})

    assert point["id"] == _to_qdrant_point_id("doc_1")
    assert point["id"] != "doc_1"
    assert point["payload"][ORIGINAL_ID_FIELD] == "doc_1"


def test_make_point_rejects_missing_vector():
    collection = _build_collection_stub(_StubClient([]))

    with pytest.raises(ValueError, match="requires at least one dense or sparse vector"):
        collection._make_point({"id": "doc_1", "name": "missing vector"})


def test_create_remote_collection_rolls_back_when_metadata_write_fails():
    client = _StubClient([{"status": "ok"}, {"status": "ok"}])
    collection = _build_collection_stub(client)
    collection._logical_collection_name = "context"
    collection._project_name = "proj"
    collection._distance_metric = "cosine"
    collection._meta_store = _FailingMetaStore()

    with pytest.raises(RuntimeError, match="metadata write failed"):
        collection.create_remote_collection(
            {
                "Fields": [
                    {"FieldName": "id", "FieldType": "string"},
                    {"FieldName": "vector", "FieldType": "vector", "Dim": 3},
                ]
            }
        )

    assert client.calls[0]["method"] == "PUT"
    assert client.calls[0]["path"] == "/collections/proj__context"
    assert client.calls[1]["method"] == "DELETE"
    assert client.calls[1]["path"] == "/collections/proj__context"
    assert client.calls[1]["expected_statuses"] == (200, 202, 404)


def test_drop_tolerates_missing_physical_collection_and_deletes_metadata():
    client = _StubClient([{"status": "ok"}])
    collection = _build_collection_stub(client)
    meta_store = _RecordingMetaStore()
    collection._meta_store = meta_store

    collection.drop()

    assert client.calls[0]["method"] == "DELETE"
    assert client.calls[0]["expected_statuses"] == (200, 202, 404)
    assert meta_store.deleted_keys == ["proj__context"]


def test_fetch_data_maps_ids_and_returns_original_ids():
    point_id = _to_qdrant_point_id("doc_1")
    client = _StubClient(
        [
            {
                "result": [
                    {
                        "id": point_id,
                        "payload": {ORIGINAL_ID_FIELD: "doc_1", "name": "Document"},
                    }
                ]
            }
        ]
    )
    collection = _build_collection_stub(client)

    result = collection.fetch_data(["doc_1", "doc_2"])

    assert client.calls[0]["json_body"]["ids"] == [point_id, _to_qdrant_point_id("doc_2")]
    assert result.items[0].id == "doc_1"
    assert result.items[0].fields == {"name": "Document"}
    assert result.ids_not_exist == ["doc_2"]


def test_search_by_id_uses_stored_vector_for_similarity_query():
    point_id = _to_qdrant_point_id("doc_1")
    client = _StubClient(
        [
            {
                "result": [
                    {
                        "id": point_id,
                        "payload": {ORIGINAL_ID_FIELD: "doc_1"},
                        "vector": {"vector": [0.1, 0.2, 0.3]},
                    }
                ]
            },
            {
                "result": {
                    "points": [
                        {
                            "id": point_id,
                            "payload": {ORIGINAL_ID_FIELD: "doc_1"},
                            "score": 1.0,
                        },
                        {
                            "id": _to_qdrant_point_id("doc_2"),
                            "payload": {ORIGINAL_ID_FIELD: "doc_2", "name": "Document 2"},
                            "score": 0.9,
                        },
                    ]
                }
            },
        ]
    )
    collection = _build_collection_stub(client)

    result = collection.search_by_id("default", "doc_1", limit=10)

    assert client.calls[0]["json_body"]["with_vector"] is True
    assert client.calls[1]["path"] == "/collections/proj__context/points/query"
    assert client.calls[1]["json_body"]["query"] == [0.1, 0.2, 0.3]
    assert result.data[0].id == "doc_2"
    assert result.data[0].fields == {"name": "Document 2"}
