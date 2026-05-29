# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any, Dict, List

import pytest

import openviking.storage.vectordb.collection.qdrant_collection as qdrant_collection_module
from openviking.storage.vectordb.collection.qdrant_collection import (
    QdrantCollection,
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
    return collection


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
