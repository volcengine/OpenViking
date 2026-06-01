# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from typing import Any, Dict, List

from openviking.storage.vectordb.collection.qdrant_meta_store import QdrantMetaStore
from openviking.storage.vectordb.collection.qdrant_rest import QdrantRestError


class _StubClient:
    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def collection_exists(self, name: str) -> bool:
        self.calls.append({"method": "COLLECTION_EXISTS", "name": name})
        return True

    def request(self, method: str, path: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"method": method, "path": path, **kwargs})
        if not self._responses:
            raise AssertionError("No more stubbed Qdrant responses available")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_scroll_index_docs_paginates_until_exhausted():
    client = _StubClient(
        [
            {"result": {"points": [{"id": "idx-1"}], "next_page_offset": "page-2"}},
            {"result": {"points": [{"id": "idx-2"}], "next_page_offset": None}},
        ]
    )
    store = QdrantMetaStore(client=client, meta_collection_name="__meta")

    docs = store._scroll_index_docs("proj__context")

    assert [doc["id"] for doc in docs] == ["idx-1", "idx-2"]
    assert client.calls[2]["json_body"]["offset"] == "page-2"


def test_get_point_returns_none_for_missing_meta_collection():
    client = _StubClient([QdrantRestError("missing", status_code=404)])
    store = QdrantMetaStore(client=client, meta_collection_name="__meta")

    assert store._get_point("missing") is None


def test_get_point_reraises_non_missing_qdrant_errors():
    client = _StubClient([QdrantRestError("auth failed", status_code=401)])
    store = QdrantMetaStore(client=client, meta_collection_name="__meta")

    try:
        store._get_point("meta-id")
    except QdrantRestError as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("Expected QdrantRestError to be reraised")
